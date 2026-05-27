"""
Database layer: SQLite for tracking processed posts, generated videos, uploads,
review state, and stage timings.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.utils import CONFIG, get_logger

logger = get_logger("database")

DB_PATH = Path(CONFIG["database"]["path"])
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


# ── Schema ──────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reddit_post_id  TEXT    UNIQUE NOT NULL,
    subreddit       TEXT    NOT NULL,
    title           TEXT,
    post_text       TEXT,
    url             TEXT,
    upvotes         INTEGER,
    created_utc     REAL,
    processed_at    TEXT    NOT NULL,
    status          TEXT    DEFAULT 'pending',
    error_msg       TEXT
);

CREATE TABLE IF NOT EXISTS generated_videos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         TEXT    NOT NULL REFERENCES processed_posts(reddit_post_id),
    subreddit       TEXT,
    script_path     TEXT,
    audio_path      TEXT,
    video_path      TEXT,
    thumbnail_path  TEXT,
    hook            TEXT,
    yt_title        TEXT,
    yt_description  TEXT,
    yt_hashtags     TEXT,
    review_status   TEXT    DEFAULT 'pending',
    reviewer_notes  TEXT,
    youtube_url     TEXT,
    upload_date     TEXT,
    quality_score   REAL,
    processing_secs REAL,
    stage_timings_json TEXT,
    created_at      TEXT    NOT NULL,
    duration_secs   REAL
);

CREATE TABLE IF NOT EXISTS uploads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        INTEGER REFERENCES generated_videos(id),
    platform        TEXT    NOT NULL,
    platform_video_id TEXT,
    upload_url      TEXT,
    uploaded_at     TEXT,
    status          TEXT    DEFAULT 'pending',
    error_msg       TEXT
);

CREATE TABLE IF NOT EXISTS processing_stage_timings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        INTEGER REFERENCES generated_videos(id),
    stage_name      TEXT    NOT NULL,
    duration_secs   REAL    NOT NULL,
    status          TEXT    DEFAULT 'success',
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    status      TEXT    DEFAULT 'running',
    posts_found INTEGER DEFAULT 0,
    videos_made INTEGER DEFAULT 0,
    error_msg   TEXT
);
"""

SCHEMA_MIGRATIONS = {
    "processed_posts": {
        "post_text": "TEXT",
        "created_utc": "REAL",
    },
    "generated_videos": {
        "subreddit": "TEXT",
        "hook": "TEXT",
        "review_status": "TEXT DEFAULT 'pending'",
        "reviewer_notes": "TEXT",
        "youtube_url": "TEXT",
        "upload_date": "TEXT",
        "quality_score": "REAL",
        "processing_secs": "REAL",
        "stage_timings_json": "TEXT",
    },
}


# ── Connection context manager ──────────────────────────────────────────────────

@contextmanager
def get_db():
    """Context manager providing a SQLite connection."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_columns(conn, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _apply_migrations():
    with get_db() as conn:
        for table_name, columns in SCHEMA_MIGRATIONS.items():
            existing = _table_columns(conn, table_name)
            for column_name, column_type in columns.items():
                if column_name not in existing:
                    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def init_db():
    """Initialize database schema."""
    with get_db() as conn:
        conn.executescript(SCHEMA)
    _apply_migrations()
    logger.info(f"Database initialized at {DB_PATH}")


# ── Post tracking ───────────────────────────────────────────────────────────────

def is_post_processed(reddit_post_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM processed_posts WHERE reddit_post_id = ?",
            (reddit_post_id,)
        ).fetchone()
    return row is not None


def mark_post_processed(
    reddit_post_id: str,
    subreddit: str,
    title: str,
    url: str,
    upvotes: int,
    status: str = "pending",
    error_msg: Optional[str] = None,
    post_text: Optional[str] = None,
    created_utc: Optional[float] = None,
):
    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO processed_posts
                (reddit_post_id, subreddit, title, post_text, url, upvotes, created_utc, processed_at, status, error_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            reddit_post_id,
            subreddit,
            title,
            post_text,
            url,
            upvotes,
            created_utc,
            datetime.utcnow().isoformat(),
            status,
            error_msg,
        ))


def update_post_status(reddit_post_id: str, status: str, error_msg: Optional[str] = None):
    with get_db() as conn:
        conn.execute(
            "UPDATE processed_posts SET status = ?, error_msg = ? WHERE reddit_post_id = ?",
            (status, error_msg, reddit_post_id)
        )


# ── Video tracking ───────────────────────────────────────────────────────────────

def save_video_record(post_id: str, script_path: str, audio_path: str,
                       video_path: str, yt_title: str, yt_description: str,
                       yt_hashtags: str, duration_secs: float,
                       thumbnail_path: Optional[str] = None,
                       subreddit: Optional[str] = None,
                       hook: Optional[str] = None,
                       quality_score: Optional[float] = None,
                       review_status: str = "pending",
                       processing_secs: Optional[float] = None,
                       stage_timings: Optional[dict] = None) -> int:
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO generated_videos
                (post_id, subreddit, script_path, audio_path, video_path, thumbnail_path,
                 hook, yt_title, yt_description, yt_hashtags, review_status,
                 quality_score, processing_secs, stage_timings_json, created_at, duration_secs)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            post_id,
            subreddit,
            script_path,
            audio_path,
            video_path,
            thumbnail_path,
            hook,
            yt_title,
            yt_description,
            yt_hashtags,
            review_status,
            quality_score,
            processing_secs,
            json.dumps(stage_timings or {}),
            datetime.utcnow().isoformat(),
            duration_secs,
        ))
        return cursor.lastrowid


def get_all_videos(limit: int = 50, search: Optional[str] = None, review_status: Optional[str] = None) -> list:
    with get_db() as conn:
        query = """
            SELECT gv.*, pp.subreddit AS post_subreddit, pp.upvotes, pp.title AS post_title, pp.post_text
            FROM generated_videos gv
            JOIN processed_posts pp ON gv.post_id = pp.reddit_post_id
        """
        clauses = []
        params: list = []
        if review_status:
            clauses.append("gv.review_status = ?")
            params.append(review_status)
        if search:
            clauses.append("(gv.yt_title LIKE ? OR gv.hook LIKE ? OR pp.title LIKE ? OR pp.subreddit LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like, like])
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY gv.created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def get_pending_reviews(limit: int = 20) -> list:
    return get_all_videos(limit=limit, review_status="pending_review")


def update_video_review_status(
    video_id: int,
    review_status: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    hook: Optional[str] = None,
    reviewer_notes: Optional[str] = None,
):
    with get_db() as conn:
        if title is not None:
            conn.execute("UPDATE generated_videos SET yt_title = ? WHERE id = ?", (title, video_id))
        if description is not None:
            conn.execute("UPDATE generated_videos SET yt_description = ? WHERE id = ?", (description, video_id))
        if hook is not None:
            conn.execute("UPDATE generated_videos SET hook = ? WHERE id = ?", (hook, video_id))
        conn.execute(
            "UPDATE generated_videos SET review_status = ?, reviewer_notes = ? WHERE id = ?",
            (review_status, reviewer_notes, video_id),
        )


def mark_video_uploaded(video_id: int, upload_url: str, platform_video_id: Optional[str] = None):
    with get_db() as conn:
        conn.execute(
            """
            UPDATE generated_videos
            SET youtube_url = ?, upload_date = ?
            WHERE id = ?
            """,
            (upload_url, datetime.utcnow().isoformat(), video_id),
        )
        conn.execute(
            """
            UPDATE uploads
            SET upload_url = ?, platform_video_id = ?, status = 'success', uploaded_at = ?
            WHERE id = (
                SELECT id FROM uploads
                WHERE video_id = ?
                ORDER BY id DESC
                LIMIT 1
            )
            """,
            (upload_url, platform_video_id, datetime.utcnow().isoformat(), video_id),
        )


def record_stage_timing(video_id: int, stage_name: str, duration_secs: float, status: str = "success"):
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO processing_stage_timings (video_id, stage_name, duration_secs, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (video_id, stage_name, duration_secs, status, datetime.utcnow().isoformat()),
        )


def log_stage_timing(video_id: int, stage_name: str, duration_secs: float, status: str = "success"):
    """Compatibility alias for pipeline callers."""
    record_stage_timing(video_id, stage_name, duration_secs, status=status)


def get_dashboard_metrics() -> dict:
    with get_db() as conn:
        videos = conn.execute("SELECT * FROM generated_videos").fetchall()
        uploads = conn.execute("SELECT * FROM uploads").fetchall()
        stages = conn.execute("SELECT * FROM processing_stage_timings").fetchall()
        runs = conn.execute("SELECT * FROM pipeline_runs").fetchall()

    videos = [dict(row) for row in videos]
    uploads = [dict(row) for row in uploads]
    stages = [dict(row) for row in stages]
    runs = [dict(row) for row in runs]

    total_videos = len(videos)
    success_uploads = sum(1 for upload in uploads if upload.get("status") == "success")
    failed_uploads = sum(1 for upload in uploads if upload.get("status") not in {"success", None, "dry_run"})
    avg_duration = (sum(video.get("duration_secs") or 0 for video in videos) / total_videos) if total_videos else 0.0
    total_processing = sum(video.get("processing_secs") or 0 for video in videos)

    top_subreddits = {}
    for video in videos:
        subreddit = video.get("subreddit") or "unknown"
        top_subreddits[subreddit] = top_subreddits.get(subreddit, 0) + 1

    hook_usage = {}
    for video in videos:
        hook = (video.get("hook") or "").strip()
        if not hook:
            continue
        hook_usage[hook] = hook_usage.get(hook, 0) + 1

    return {
        "total_videos": total_videos,
        "upload_success_rate": (success_uploads / len(uploads) * 100) if uploads else 0.0,
        "upload_failure_rate": (failed_uploads / len(uploads) * 100) if uploads else 0.0,
        "avg_video_duration": avg_duration,
        "total_processing_secs": total_processing,
        "top_subreddits": dict(sorted(top_subreddits.items(), key=lambda item: item[1], reverse=True)),
        "most_used_hooks": dict(sorted(hook_usage.items(), key=lambda item: item[1], reverse=True)[:10]),
        "recent_runs": runs[:10],
        "stage_timings": stages[-50:],
        "videos": videos[:50],
    }


# ── Upload tracking ──────────────────────────────────────────────────────────────

def save_upload(video_id: int, platform: str, platform_video_id: Optional[str] = None,
                upload_url: Optional[str] = None, status: str = "success",
                error_msg: Optional[str] = None):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO uploads
                (video_id, platform, platform_video_id, upload_url, uploaded_at, status, error_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (video_id, platform, platform_video_id, upload_url,
              datetime.utcnow().isoformat(), status, error_msg))


# ── Pipeline run tracking ────────────────────────────────────────────────────────

def start_pipeline_run() -> int:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO pipeline_runs (started_at) VALUES (?)",
            (datetime.utcnow().isoformat(),)
        )
        return cursor.lastrowid


def finish_pipeline_run(run_id: int, status: str = "success",
                         posts_found: int = 0, videos_made: int = 0,
                         error_msg: Optional[str] = None):
    with get_db() as conn:
        conn.execute("""
            UPDATE pipeline_runs
            SET finished_at = ?, status = ?, posts_found = ?, videos_made = ?, error_msg = ?
            WHERE id = ?
        """, (datetime.utcnow().isoformat(), status, posts_found, videos_made,
              error_msg, run_id))


def get_recent_runs(limit: int = 10) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
