"""
Reddit fetcher built on public RSS feeds and JSON endpoints.

Primary flow:
RSS discovery -> public JSON post details -> fallback parser
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

import feedparser

from config.utils import CONFIG, build_reddit_headers, create_cached_session, ensure_dir, get_logger

logger = get_logger("reddit_fetcher")


ENGAGEMENT_KEYWORDS = [
    "aita", "wibta", "aitah", "tifu", "confession", "update",
    "cheated", "betrayed", "divorce", "fired", "pregnant", "secret",
    "family", "friend", "boyfriend", "girlfriend", "husband", "wife",
    "revenge", "caught", "lied", "shocked", "discovered", "found out",
    "destroyed", "ruined", "nightmare", "worst", "best", "incredible",
    "unbelievable", "heartbroken", "angry", "manipulative", "toxic",
]


@dataclass
class RedditPost:
    """Normalized public Reddit post payload."""

    id: str
    subreddit: str
    title: str
    text: str
    upvotes: int
    comment_count: int
    url: str
    author: str
    created_utc: float
    flair: Optional[str] = None
    top_comments: list[str] = field(default_factory=list)
    nsfw: bool = False
    source: str = "rss"

    @property
    def full_text(self) -> str:
        return f"{self.title}\n\n{self.text}".strip()

    @property
    def char_count(self) -> int:
        return len(self.full_text)


def clean_text(text: str) -> str:
    """Basic cleanup: remove URLs, markdown artifacts, and extra whitespace."""
    text = re.sub(r"http\S+", "", text)
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    text = re.sub(r"#{1,6}\s", "", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class RedditFetcher:
    """Fetch public Reddit posts without OAuth or PRAW."""

    def __init__(self):
        fetch_cfg = CONFIG.get("reddit_fetcher", {})
        self.request_delay = float(fetch_cfg.get("request_delay_seconds", 1.25))
        self.cache_seconds = int(fetch_cfg.get("cache_seconds", 3600))
        self.max_retries = int(fetch_cfg.get("max_retries", 4))
        self.backoff_factor = float(fetch_cfg.get("backoff_factor", 1.8))
        self.json_limit = int(fetch_cfg.get("json_limit", CONFIG["reddit"].get("post_limit", 25)))
        cache_path = ensure_dir("database") / "reddit_cache"
        self.session = create_cached_session(str(cache_path), expire_after_seconds=self.cache_seconds)
        self.seen_ids: set[str] = set()

    def _sleep(self):
        time.sleep(self.request_delay)

    def _request_json(self, url: str, params: Optional[dict] = None) -> dict | list:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            headers = build_reddit_headers()
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=20)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                wait = min(30.0, (self.backoff_factor ** attempt) + random.uniform(0.1, 0.75))
                logger.warning("Request failed for %s (%s/%s): %s. Retrying in %.1fs", url, attempt, self.max_retries, exc, wait)
                time.sleep(wait)
            finally:
                self._sleep()

        raise RuntimeError(f"Failed to fetch {url}: {last_error}")

    def _request_text(self, url: str, params: Optional[dict] = None) -> str:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            headers = build_reddit_headers()
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=20)
                response.raise_for_status()
                return response.text
            except Exception as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                wait = min(30.0, (self.backoff_factor ** attempt) + random.uniform(0.1, 0.75))
                logger.warning("Text fetch failed for %s (%s/%s): %s. Retrying in %.1fs", url, attempt, self.max_retries, exc, wait)
                time.sleep(wait)
            finally:
                self._sleep()

        raise RuntimeError(f"Failed to fetch {url}: {last_error}")

    def _extract_post_id(self, url: str) -> str:
        match = re.search(r"/comments/([a-z0-9]+)/", url)
        if match:
            return match.group(1)
        return url.rstrip("/").split("/")[-1]

    def _is_removed(self, text: str) -> bool:
        lowered = text.lower().strip()
        return lowered in {"", "[removed]", "[deleted]"}

    def _emotion_score(self, text: str) -> int:
        lowered = text.lower()
        return sum(1 for keyword in ENGAGEMENT_KEYWORDS if keyword in lowered)

    def _is_quality_candidate(self, post: RedditPost) -> tuple[bool, str]:
        cfg = CONFIG["reddit"]

        if post.nsfw:
            return False, "nsfw"
        if self._is_removed(post.text):
            return False, "removed or deleted"
        if post.upvotes < cfg["min_upvotes"]:
            return False, f"low upvotes ({post.upvotes})"
        if post.char_count < cfg["min_char_limit"]:
            return False, f"too short ({post.char_count} chars)"
        if post.char_count > cfg["max_char_limit"]:
            return False, f"too long ({post.char_count} chars)"
        if self._emotion_score(post.full_text) < 2:
            return False, "not emotionally engaging"
        if post.id in self.seen_ids:
            return False, "duplicate"
        return True, "ok"

    def _parse_top_comments(self, listing: list) -> list[str]:
        comments: list[str] = []
        if not listing or len(listing) < 2:
            return comments

        children = listing[1].get("data", {}).get("children", [])
        for child in children:
            if child.get("kind") != "t1":
                continue
            body = clean_text(child.get("data", {}).get("body", ""))
            if body and len(body) < 300:
                comments.append(body)
            if len(comments) >= 3:
                break
        return comments

    def _normalize_from_json(self, subreddit: str, data: dict, source: str = "json") -> Optional[RedditPost]:
        post_id = data.get("id") or self._extract_post_id(data.get("permalink", ""))
        if not post_id:
            return None

        permalink = data.get("permalink") or f"/r/{subreddit}/comments/{post_id}/"
        text = clean_text(data.get("selftext") or data.get("body", ""))
        post = RedditPost(
            id=post_id,
            subreddit=subreddit,
            title=clean_text(data.get("title", "")),
            text=text,
            upvotes=int(data.get("score", 0) or 0),
            comment_count=int(data.get("num_comments", 0) or 0),
            url=f"https://www.reddit.com{permalink}",
            author=str(data.get("author") or "unknown"),
            created_utc=float(data.get("created_utc") or 0.0),
            flair=data.get("link_flair_text"),
            top_comments=[],
            nsfw=bool(data.get("over_18", False)),
            source=source,
        )
        return post

    def _normalize_from_rss_entry(self, subreddit: str, entry) -> Optional[RedditPost]:
        link = getattr(entry, "link", "") or getattr(entry, "id", "")
        if not link:
            return None

        post_id = self._extract_post_id(link)
        title = clean_text(getattr(entry, "title", ""))
        summary = clean_text(getattr(entry, "summary", ""))
        published_parsed = getattr(entry, "published_parsed", None)
        created_utc = 0.0
        if published_parsed:
            created_utc = time.mktime(published_parsed)

        return RedditPost(
            id=post_id,
            subreddit=subreddit,
            title=title,
            text=summary,
            upvotes=0,
            comment_count=0,
            url=link,
            author="unknown",
            created_utc=created_utc,
            flair=None,
            top_comments=[],
            nsfw=False,
            source="rss",
        )

    def _discover_rss(self, subreddit: str) -> list:
        rss_url = f"https://www.reddit.com/r/{subreddit}/.rss"
        try:
            feed_text = self._request_text(rss_url)
            feed = feedparser.parse(feed_text)
            if getattr(feed, "bozo", False):
                raise ValueError(getattr(feed, "bozo_exception", "invalid RSS"))
            return list(feed.entries)
        except Exception as exc:
            logger.warning("RSS discovery failed for r/%s: %s", subreddit, exc)
            return []

    def _discover_json(self, subreddit: str) -> list[dict]:
        json_url = f"https://www.reddit.com/r/{subreddit}/top.json"
        try:
            payload = self._request_json(json_url, params={"t": CONFIG["reddit"].get("time_filter", "day"), "limit": self.json_limit, "raw_json": 1})
            return payload.get("data", {}).get("children", []) if isinstance(payload, dict) else []
        except Exception as exc:
            logger.warning("JSON discovery failed for r/%s: %s", subreddit, exc)
            return []

    def _fetch_post_details(self, permalink: str) -> Optional[tuple[dict, list]]:
        if not permalink:
            return None

        permalink = permalink if permalink.startswith("http") else f"https://www.reddit.com{permalink}"
        json_url = permalink.rstrip("/") + ".json"
        try:
            payload = self._request_json(json_url, params={"raw_json": 1})
            if isinstance(payload, list) and payload:
                first = payload[0].get("data", {}).get("children", [])
                if first:
                    post_data = first[0].get("data", {})
                    return post_data, payload
            return None
        except Exception as exc:
            logger.warning("Post JSON fetch failed for %s: %s", permalink, exc)
            return None

    def fetch_subreddit_posts(self, subreddit: str) -> list[RedditPost]:
        discovered = self._discover_rss(subreddit)
        discovery_source = "rss"

        if not discovered:
            discovered = self._discover_json(subreddit)
            discovery_source = "json"

        posts: list[RedditPost] = []
        for item in discovered:
            if hasattr(item, "link"):
                rss_post = self._normalize_from_rss_entry(subreddit, item)
                permalink = getattr(item, "link", "")
            else:
                data = item.get("data", {}) if isinstance(item, dict) else {}
                permalink = data.get("permalink", "")
                rss_post = self._normalize_from_json(subreddit, data, source=discovery_source)

            if not permalink:
                continue

            details = self._fetch_post_details(permalink)
            if details:
                post_data, payload = details
                post = self._normalize_from_json(subreddit, post_data, source="json")
                if post:
                    post.top_comments = self._parse_top_comments(payload)
            else:
                post = rss_post

            if not post:
                continue

            ok, reason = self._is_quality_candidate(post)
            if not ok:
                logger.debug("Skipping %s: %s", post.id, reason)
                continue

            self.seen_ids.add(post.id)
            posts.append(post)

        return posts

    def fetch_posts(self, subreddits: Iterable[str], max_posts: int) -> list[RedditPost]:
        posts: list[RedditPost] = []
        for subreddit in subreddits:
            subreddit_posts = self.fetch_subreddit_posts(subreddit)
            posts.extend(subreddit_posts)
            if len(posts) >= max_posts:
                break

        # Deduplicate after merging across subreddits.
        unique_posts: list[RedditPost] = []
        seen: set[str] = set()
        for post in posts:
            if post.id in seen:
                continue
            seen.add(post.id)
            unique_posts.append(post)

        return unique_posts[:max_posts]
