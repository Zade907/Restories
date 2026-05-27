"""
Streamlit dashboard for Reddit Shorts Factory.

Focus:
- retention-first analytics
- searchable history
- manual review queue before upload
- metadata editing and hook regeneration
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.utils import CONFIG
from database.db import (
    get_all_videos,
    get_dashboard_metrics,
    get_pending_reviews,
    init_db,
    mark_video_uploaded,
    save_upload,
    update_video_review_status,
)
from hook_generator import generate_hook
from scraper.scraper import RedditPost


st.set_page_config(
    page_title="Reddit Shorts Factory",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Space Grotesk', sans-serif;
    }

    .hero {
        background: radial-gradient(circle at top left, rgba(255,197,87,0.28), transparent 32%),
                    linear-gradient(135deg, #0f172a 0%, #111827 55%, #1f2937 100%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 24px;
        padding: 2rem;
        color: white;
        margin-bottom: 1.25rem;
    }

    .card {
        background: rgba(15, 23, 42, 0.72);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 18px;
        padding: 1rem;
    }

    .stButton > button {
        border-radius: 999px;
        border: none;
        background: linear-gradient(135deg, #f97316, #fb7185);
        color: white;
        font-weight: 700;
    }
</style>
""",
    unsafe_allow_html=True,
)


def _safe_load_json(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except Exception:
        return []


def _video_exists(path: str) -> bool:
    return bool(path) and Path(path).exists()


def _preview_video(path: str):
    if _video_exists(path):
        st.video(path)
    else:
        st.info("Video file not found on disk.")


def _make_reddit_post(row) -> RedditPost:
    return RedditPost(
        id=row["post_id"],
        subreddit=row.get("subreddit") or row.get("post_subreddit") or "unknown",
        title=row.get("post_title") or row.get("yt_title") or "Untitled",
        text=row.get("post_text") or "",
        upvotes=int(row.get("upvotes") or 0),
        comment_count=0,
        url="",
        author="dashboard",
        created_utc=0.0,
    )


def _regen_hook(row) -> str:
    llm = None
    post = _make_reddit_post(row)
    return generate_hook(post, llm).hook


try:
    init_db()
except Exception as exc:
    st.error(f"Database init failed: {exc}")
    st.stop()


with st.sidebar:
    st.markdown("### Controls")
    review_default = st.toggle("Review mode", value=True)
    parallel_workers = st.slider("Parallel workers", 1, 4, 2)
    max_videos = st.slider("Max videos per run", 1, 10, 3)
    upload_after_review = st.toggle("Allow upload from dashboard", value=True)

    st.divider()
    st.markdown("### Filters")
    search_text = st.text_input("Search history", placeholder="title, subreddit, hook")
    review_filter = st.selectbox("Review status", ["all", "pending_review", "approved", "rejected", "completed"])


st.markdown(
    """
    <div class="hero">
        <h1>Reddit Shorts Factory</h1>
        <p>Retention-first Shorts generation with hook-first scripting, script-based subtitles, review gates, and analytics.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


metrics = get_dashboard_metrics()

overview_a, overview_b, overview_c, overview_d = st.columns(4)
overview_a.metric("Videos generated", metrics.get("total_videos", 0))
overview_b.metric("Upload success rate", f"{metrics.get('upload_success_rate', 0):.1f}%")
overview_c.metric("Avg duration", f"{metrics.get('avg_video_duration', 0):.1f}s")
overview_d.metric("Processing time", f"{metrics.get('total_processing_secs', 0)/60:.1f} min")


tab_run, tab_review, tab_history, tab_analytics = st.tabs([
    "Run Pipeline",
    "Review Queue",
    "History",
    "Analytics",
])


with tab_run:
    st.markdown("### Generate new videos")
    col1, col2, col3 = st.columns(3)
    with col1:
        run_upload = st.toggle("Upload after generation", value=False)
    with col2:
        run_review_mode = st.toggle("Keep in review queue", value=review_default)
    with col3:
        run_button = st.button("Run pipeline", use_container_width=True)

    if run_button:
        from pipeline import run_pipeline

        with st.spinner("Generating videos..."):
            results = run_pipeline(
                max_videos=max_videos,
                upload=run_upload,
                dry_run=not run_upload,
                review_mode=run_review_mode,
                parallel_workers=parallel_workers,
            )

        success_count = sum(1 for result in results if result.success)
        st.success(f"Completed {success_count}/{len(results)} jobs")
        for result in results:
            with st.expander(f"Post {result.post_id} - {'success' if result.success else 'failed'}"):
                st.write(f"Hook: {result.hook or 'n/a'}")
                st.write(f"Title: {result.yt_title or 'n/a'}")
                st.write(f"Quality score: {result.quality_score:.1f}")
                if result.error:
                    st.error(result.error)
                if result.video_path:
                    _preview_video(result.video_path)


with tab_review:
    st.markdown("### Pending review")
    pending = get_pending_reviews(limit=50)
    if not pending:
        st.info("No items waiting for review.")
    else:
        for row in pending:
            with st.container(border=True):
                left, right = st.columns([1.3, 1])
                with left:
                    st.markdown(f"**{row.get('yt_title') or row.get('post_title') or 'Untitled'}**")
                    st.caption(f"r/{row.get('post_subreddit') or row.get('subreddit') or 'unknown'} • score {row.get('quality_score') or 0:.1f}")
                    st.write(row.get("hook") or "No hook saved.")
                    if row.get("yt_description"):
                        st.caption(row["yt_description"])

                    video_path = row.get("video_path") or ""
                    _preview_video(video_path)

                with right:
                    title = st.text_input("Title", value=row.get("yt_title") or "", key=f"title_{row['id']}")
                    description = st.text_area("Description", value=row.get("yt_description") or "", height=120, key=f"desc_{row['id']}")
                    hook = st.text_area("Hook", value=row.get("hook") or "", height=90, key=f"hook_{row['id']}")
                    notes = st.text_area("Reviewer notes", value=row.get("reviewer_notes") or "", height=90, key=f"notes_{row['id']}")

                    btn_a, btn_b, btn_c = st.columns(3)
                    with btn_a:
                        if st.button("Regenerate hook", key=f"regen_{row['id']}"):
                            regenerated = _regen_hook(row)
                            update_video_review_status(row["id"], row.get("review_status") or "pending_review", hook=regenerated)
                            st.rerun()
                    with btn_b:
                        if st.button("Approve", key=f"approve_{row['id']}"):
                            update_video_review_status(
                                row["id"],
                                "approved",
                                title=title,
                                description=description,
                                hook=hook,
                                reviewer_notes=notes,
                            )
                            st.success("Approved.")
                            st.rerun()
                    with btn_c:
                        if st.button("Reject", key=f"reject_{row['id']}"):
                            update_video_review_status(
                                row["id"],
                                "rejected",
                                title=title,
                                description=description,
                                hook=hook,
                                reviewer_notes=notes,
                            )
                            st.warning("Rejected.")
                            st.rerun()

                    if upload_after_review and st.button("Approve & upload", key=f"upload_{row['id']}"):
                        from uploader.youtube_uploader import simulate_upload, upload_video

                        update_video_review_status(
                            row["id"],
                            "approved",
                            title=title,
                            description=description,
                            hook=hook,
                            reviewer_notes=notes,
                        )
                        video_path = row.get("video_path")
                        if _video_exists(video_path):
                            upload_result = upload_video(
                                video_path=video_path,
                                title=title,
                                description=description,
                                hashtags=_safe_load_json(row.get("yt_hashtags")),
                                thumbnail_path=row.get("thumbnail_path"),
                            )
                        else:
                            upload_result = simulate_upload(
                                video_path=video_path,
                                title=title,
                                description=description,
                                hashtags=_safe_load_json(row.get("yt_hashtags")),
                            )
                        save_upload(
                            video_id=row["id"],
                            platform="youtube",
                            platform_video_id=upload_result.get("video_id"),
                            upload_url=upload_result.get("url"),
                            status=upload_result.get("status", "success"),
                        )
                        if upload_result.get("url"):
                            mark_video_uploaded(row["id"], upload_result.get("url"), upload_result.get("video_id"))
                        st.success(upload_result.get("url", "Upload queued."))


with tab_history:
    st.markdown("### Searchable history")
    history_review_filter = None if review_filter == "all" else review_filter
    history_rows = get_all_videos(limit=200, search=search_text or None, review_status=history_review_filter)

    if not history_rows:
        st.info("No matching history.")
    else:
        history_df = pd.DataFrame(history_rows)
        st.dataframe(
            history_df[[col for col in [
                "id", "post_id", "post_subreddit", "yt_title", "hook", "review_status", "duration_secs", "quality_score", "youtube_url", "created_at"
            ] if col in history_df.columns]],
            use_container_width=True,
            hide_index=True,
        )

        for row in history_rows[:20]:
            with st.expander(f"{row.get('yt_title') or 'Untitled'} • {row.get('review_status')}"):
                st.write(f"Hook: {row.get('hook') or 'n/a'}")
                st.write(f"Subreddit: r/{row.get('post_subreddit') or row.get('subreddit') or 'unknown'}")
                st.write(f"Processing: {row.get('processing_secs') or 0:.1f}s")
                st.write(f"YouTube: {row.get('youtube_url') or 'n/a'}")
                stage_timings = row.get("stage_timings_json")
                if stage_timings:
                    try:
                        st.json(json.loads(stage_timings))
                    except Exception:
                        st.caption(stage_timings)


with tab_analytics:
    st.markdown("### Retention-first analytics")
    analytics_left, analytics_right = st.columns(2)

    with analytics_left:
        top_subreddits = metrics.get("top_subreddits", {})
        if top_subreddits:
            st.bar_chart(pd.Series(top_subreddits))
        else:
            st.info("No subreddit data yet.")

    with analytics_right:
        hooks = metrics.get("most_used_hooks", {})
        if hooks:
            st.bar_chart(pd.Series(hooks))
        else:
            st.info("No hook data yet.")

    stage_rows = metrics.get("stage_timings", [])
    if stage_rows:
        stage_df = pd.DataFrame(stage_rows)
        st.markdown("#### Stage timings")
        st.dataframe(stage_df[[col for col in ["stage_name", "duration_secs", "status", "created_at"] if col in stage_df.columns]], use_container_width=True, hide_index=True)

    runs = metrics.get("recent_runs", [])
    if runs:
        run_df = pd.DataFrame(runs)
        st.markdown("#### Recent pipeline runs")
        st.dataframe(run_df[[col for col in ["started_at", "finished_at", "status", "posts_found", "videos_made"] if col in run_df.columns]], use_container_width=True, hide_index=True)
