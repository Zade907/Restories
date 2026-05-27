"""
Reddit scraping wrapper built on public RSS feeds and JSON endpoints.

This module preserves the legacy `RedditPost` type and `scrape_posts()` API
so the rest of the pipeline can stay stable while the fetch implementation is
handled by `services.reddit_fetcher`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from config.utils import CONFIG, get_logger
from database.db import is_post_processed, mark_post_processed
from services.reddit_fetcher import RedditFetcher, RedditPost as FetchedRedditPost

logger = get_logger("scraper")


@dataclass
class RedditPost:
    """Structured Reddit post data used throughout the pipeline."""

    id: str
    subreddit: str
    title: str
    text: str
    upvotes: int
    comment_count: int
    url: str
    author: str
    created_utc: float
    flair: str | None = None
    top_comments: List[str] = field(default_factory=list)
    nsfw: bool = False
    source: str = "rss"

    @property
    def full_text(self) -> str:
        return f"{self.title}\n\n{self.text}"

    @property
    def char_count(self) -> int:
        return len(self.full_text)


ENGAGEMENT_KEYWORDS = [
    "aita", "wibta", "aitah", "tifu", "confession", "update",
    "cheated", "betrayed", "divorce", "fired", "pregnant", "secret",
    "family", "friend", "boyfriend", "girlfriend", "husband", "wife",
    "revenge", "caught", "lied", "shocked", "discovered", "found out",
    "destroyed", "ruined", "nightmare", "worst", "best", "incredible",
    "unbelievable", "heartbroken", "angry", "manipulative", "toxic",
]


def clean_text(text: str) -> str:
    """Basic cleanup: remove markdown, extra whitespace, URLs."""
    text = re.sub(r"http\S+", "", text)
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    text = re.sub(r"#{1,6}\s", "", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def engagement_score(post: RedditPost) -> float:
    """Compute a 0-100 engagement score for ranking public Reddit posts."""
    cfg = CONFIG["reddit"]

    upvote_score = min(post.upvotes / 50_000, 1.0) * 40
    comment_score = min(post.comment_count / 2_000, 1.0) * 20

    text_lower = post.full_text.lower()
    kw_hits = sum(1 for keyword in ENGAGEMENT_KEYWORDS if keyword in text_lower)
    keyword_score = min(kw_hits / 5, 1.0) * 25

    length = post.char_count
    if 600 <= length <= 2500:
        length_score = 15
    elif length < 600:
        length_score = max(0, (length / 600) * 15)
    else:
        overshoot = (length - 2500) / 2500
        length_score = max(0, 15 - overshoot * 15)

    score = upvote_score + comment_score + keyword_score + length_score
    if post.nsfw:
        score *= 0.5
    if post.char_count < cfg["min_char_limit"]:
        score *= 0.75
    return score


def _convert_post(post: FetchedRedditPost) -> RedditPost:
    return RedditPost(
        id=post.id,
        subreddit=post.subreddit,
        title=clean_text(post.title),
        text=clean_text(post.text),
        upvotes=post.upvotes,
        comment_count=post.comment_count,
        url=post.url,
        author=post.author,
        created_utc=post.created_utc,
        flair=post.flair,
        top_comments=list(post.top_comments),
        nsfw=post.nsfw,
        source=post.source,
    )


def scrape_posts(max_posts: int = 10) -> List[RedditPost]:
    """
    Fetch posts from public RSS and JSON endpoints, rank them, and store them.
    Returns up to `max_posts` best posts.
    """
    fetcher = RedditFetcher()
    cfg = CONFIG["reddit"]

    raw_posts = fetcher.fetch_posts(cfg["subreddits"], max_posts=max_posts * 2)
    posts = [_convert_post(post) for post in raw_posts]

    ranked = sorted(posts, key=engagement_score, reverse=True)
    selected = []
    seen_ids: set[str] = set()

    for post in ranked:
        if post.id in seen_ids or is_post_processed(post.id):
            continue
        seen_ids.add(post.id)
        selected.append(post)
        mark_post_processed(
            post.id,
            post.subreddit,
            post.title,
            post.url,
            post.upvotes,
            status="pending",
            post_text=post.text,
            created_utc=post.created_utc,
        )
        if len(selected) >= max_posts:
            break

    logger.info("Scraped %s eligible posts; returning top %s", len(posts), max_posts)
    return selected
