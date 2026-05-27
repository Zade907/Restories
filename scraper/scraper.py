"""
Reddit scraper: fetch high-engagement text posts using PRAW.
Filters for emotional, dramatic content suitable for Shorts.
"""

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

import praw
from praw.models import Submission

from config.utils import CONFIG, RateLimiter, env, get_logger, retry
from database.db import is_post_processed, mark_post_processed

logger = get_logger("scraper")

# Emotional/dramatic keywords that signal good content
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
    """Structured Reddit post data."""
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
    top_comments: List[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return f"{self.title}\n\n{self.text}"

    @property
    def char_count(self) -> int:
        return len(self.full_text)


# ── Reddit client ───────────────────────────────────────────────────────────────

def build_reddit_client() -> praw.Reddit:
    """Instantiate authenticated PRAW client from env vars."""
    return praw.Reddit(
        client_id=env("REDDIT_CLIENT_ID"),
        client_secret=env("REDDIT_CLIENT_SECRET"),
        user_agent=env("REDDIT_USER_AGENT", "RedditShortsBot/1.0"),
        # Read-only mode; no username/password needed for public posts
    )


# ── Scoring ─────────────────────────────────────────────────────────────────────

def engagement_score(post: RedditPost) -> float:
    """
    Compute a 0-100 engagement score combining upvotes, comments,
    keyword presence, and text richness.
    """
    cfg = CONFIG["reddit"]

    # Normalised upvote score (cap at 50k)
    upvote_score = min(post.upvotes / 50_000, 1.0) * 40

    # Comment engagement (cap at 2k)
    comment_score = min(post.comment_count / 2_000, 1.0) * 20

    # Keyword density
    text_lower = post.full_text.lower()
    kw_hits = sum(1 for kw in ENGAGEMENT_KEYWORDS if kw in text_lower)
    keyword_score = min(kw_hits / 5, 1.0) * 25

    # Text length sweet-spot (600-2500 chars is ideal)
    length = post.char_count
    if 600 <= length <= 2500:
        length_score = 15
    elif length < 600:
        length_score = max(0, (length / 600) * 15)
    else:
        # Too long; penalise
        overshoot = (length - 2500) / 2500
        length_score = max(0, 15 - overshoot * 15)

    return upvote_score + comment_score + keyword_score + length_score


# ── Filtering ───────────────────────────────────────────────────────────────────

def is_eligible(submission: Submission) -> tuple[bool, str]:
    """
    Return (eligible, reason) for a PRAW submission.
    Filters out: link posts, short posts, already processed, mods/deleted.
    """
    cfg = CONFIG["reddit"]

    if submission.is_self is False:
        return False, "link post"

    if not submission.selftext or submission.selftext in ("[removed]", "[deleted]", ""):
        return False, "no text content"

    if submission.score < cfg["min_upvotes"]:
        return False, f"low upvotes ({submission.score})"

    if submission.num_comments < cfg["min_comments"]:
        return False, f"low comments ({submission.num_comments})"

    total_len = len(submission.title) + len(submission.selftext)
    if total_len > cfg["max_char_limit"]:
        return False, f"too long ({total_len} chars)"

    if total_len < cfg["min_char_limit"]:
        return False, f"too short ({total_len} chars)"

    if is_post_processed(submission.id):
        return False, "already processed"

    return True, "ok"


def clean_text(text: str) -> str:
    """Basic cleanup: remove markdown, extra whitespace, URLs."""
    text = re.sub(r"http\S+", "", text)                # Remove URLs
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text) # Remove bold/italic
    text = re.sub(r"#{1,6}\s", "", text)               # Remove headers
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Fetching ─────────────────────────────────────────────────────────────────────

@retry(attempts=3, delay=10, exceptions=(Exception,), logger_name="scraper")
def fetch_top_comments(submission: Submission, n: int = 3) -> List[str]:
    """Fetch top n top-level comments (short ones for context)."""
    submission.comments.replace_more(limit=0)
    comments = []
    for comment in submission.comments[:n]:
        if hasattr(comment, "body") and len(comment.body) < 300:
            comments.append(clean_text(comment.body))
    return comments


def fetch_posts(subreddit_name: str, reddit: praw.Reddit) -> List[RedditPost]:
    """Fetch and filter posts from a single subreddit."""
    cfg = CONFIG["reddit"]
    rl = RateLimiter(calls_per_second=0.5)  # 1 request per 2 seconds
    posts: List[RedditPost] = []

    logger.info(f"Fetching r/{subreddit_name} ...")
    subreddit = reddit.subreddit(subreddit_name)

    try:
        submissions = subreddit.top(
            time_filter=cfg["time_filter"],
            limit=cfg["post_limit"]
        )
        for sub in submissions:
            rl.wait()
            eligible, reason = is_eligible(sub)
            if not eligible:
                logger.debug(f"Skipping {sub.id}: {reason}")
                continue

            post = RedditPost(
                id=sub.id,
                subreddit=subreddit_name,
                title=clean_text(sub.title),
                text=clean_text(sub.selftext),
                upvotes=sub.score,
                comment_count=sub.num_comments,
                url=f"https://reddit.com{sub.permalink}",
                author=str(sub.author) if sub.author else "unknown",
                created_utc=sub.created_utc,
                flair=sub.link_flair_text,
                top_comments=fetch_top_comments(sub),
            )
            posts.append(post)
            logger.debug(f"Accepted post {sub.id} from r/{subreddit_name} (score={post.upvotes})")

    except Exception as e:
        logger.error(f"Error fetching r/{subreddit_name}: {e}")

    return posts


# ── Main scraper ─────────────────────────────────────────────────────────────────

def scrape_posts(max_posts: int = 10) -> List[RedditPost]:
    """
    Scrape posts from all configured subreddits, score and rank them.
    Returns up to `max_posts` best posts.
    """
    reddit = build_reddit_client()
    cfg = CONFIG["reddit"]
    all_posts: List[RedditPost] = []

    for sr in cfg["subreddits"]:
        posts = fetch_posts(sr, reddit)
        all_posts.extend(posts)
        time.sleep(1)

    # Score and sort
    ranked = sorted(all_posts, key=engagement_score, reverse=True)

    # Mark all as pending in DB
    for post in ranked[:max_posts]:
        mark_post_processed(
            post.id, post.subreddit, post.title,
            post.url, post.upvotes, status="pending",
            post_text=post.text, created_utc=post.created_utc,
        )

    logger.info(f"Scraped {len(all_posts)} eligible posts; returning top {max_posts}")
    return ranked[:max_posts]
