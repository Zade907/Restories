"""
Pipeline orchestrator: ties together all modules into an end-to-end flow.
  scrape → summarize → TTS → subtitles → video → upload
"""

import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

from concurrent.futures import ThreadPoolExecutor, as_completed

from config.utils import CONFIG, ensure_dir, get_logger, sanitize_filename
from database.db import (
    finish_pipeline_run, init_db, log_stage_timing, mark_video_uploaded,
    save_upload, save_video_record, start_pipeline_run, update_post_status,
)
from scraper.scraper import scrape_posts
from hook_generator import generate_hook
from quality import score_script
from subtitles.subtitle_gen import generate_subtitles
from summarizer.summarizer import LLMRouter, summarize_post
from tts.tts_engine import generate_narration, get_audio_duration
from uploader.youtube_uploader import simulate_upload, upload_video
from video_editor.editor import create_shorts_video

logger = get_logger("pipeline")


# ── Result container ──────────────────────────────────────────────────────────────

class PipelineResult:
    def __init__(self):
        self.post_id: Optional[str] = None
        self.hook: Optional[str] = None
        self.script: Optional[str] = None
        self.audio_path: Optional[str] = None
        self.video_path: Optional[str] = None
        self.thumbnail_path: Optional[str] = None
        self.yt_title: Optional[str] = None
        self.yt_description: Optional[str] = None
        self.yt_hashtags: list = []
        self.quality_score: float = 0.0
        self.review_status: str = "pending"
        self.stage_timings: dict = {}
        self.upload_result: Optional[dict] = None
        self.success: bool = False
        self.error: Optional[str] = None
        self.duration: float = 0.0


# ── Individual post pipeline ──────────────────────────────────────────────────────

def run_post_pipeline(
    post,
    llm: LLMRouter,
    upload: bool = False,
    dry_run: bool = True,
    review_mode: bool = False,
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> PipelineResult:
    """
    Full pipeline for a single Reddit post.
    Returns a PipelineResult.
    """
    result = PipelineResult()
    result.post_id = post.id

    def progress(msg: str, pct: int):
        logger.info(f"[{post.id}] {msg} ({pct}%)")
        if progress_cb:
            progress_cb(msg, pct)

    try:
        pipeline_started = time.perf_counter()

        # Step 1: Summarize
        progress("Generating hook...", 5)
        hook_started = time.perf_counter()
        hook_result = generate_hook(post, llm)
        result.hook = hook_result.hook
        result.stage_timings["hook"] = time.perf_counter() - hook_started

        progress("Generating script...", 12)
        script_started = time.perf_counter()
        video_script = summarize_post(post, llm, hook=hook_result.hook)
        result.script = video_script.script
        result.yt_title = video_script.yt_title
        result.yt_description = video_script.yt_description
        result.yt_hashtags = video_script.yt_hashtags
        result.stage_timings["script"] = time.perf_counter() - script_started

        progress("Scoring script quality...", 20)
        quality_started = time.perf_counter()
        quality = score_script(video_script.script, hook=video_script.hook, title=video_script.yt_title)
        result.quality_score = quality.score
        result.stage_timings["quality"] = time.perf_counter() - quality_started
        if quality.reject:
            raise ValueError(
                f"Low quality script rejected (score={quality.score:.1f}, "
                f"hook={quality.hook_quality:.1f}, pacing={quality.pacing:.1f})"
            )

        # Save script to file
        script_dir = ensure_dir("output/scripts")
        script_path = str(script_dir / f"{post.id}_script.txt")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(f"TITLE: {video_script.yt_title}\n\n")
            f.write(f"SCRIPT:\n{video_script.script}\n\n")
            f.write(f"HASHTAGS: {', '.join(video_script.yt_hashtags)}\n")

        # Step 2: TTS
        progress("Generating narration...", 30)
        tts_started = time.perf_counter()
        audio_path, word_boundaries = generate_narration(video_script.script, post.id)
        result.audio_path = audio_path
        result.duration = get_audio_duration(audio_path)
        result.stage_timings["tts"] = time.perf_counter() - tts_started

        # Step 3: Subtitles
        progress("Generating subtitles...", 50)
        subtitle_started = time.perf_counter()
        srt_path, ass_path, blocks = generate_subtitles(
            video_script.script,
            result.duration,
            post.id,
            audio_path=audio_path,
        )
        result.stage_timings["subtitles"] = time.perf_counter() - subtitle_started

        # Step 4: Video
        progress("Composing video...", 70)
        video_started = time.perf_counter()
        video_path, thumb_path = create_shorts_video(
            post_id=post.id,
            audio_path=audio_path,
            subtitle_path=ass_path,
            yt_title=video_script.yt_title,
            audio_duration=result.duration,
            subreddit=post.subreddit,
            hook=video_script.hook,
        )
        result.video_path = video_path
        result.thumbnail_path = thumb_path
        result.stage_timings["video"] = time.perf_counter() - video_started

        total_processing = time.perf_counter() - pipeline_started

        # Step 5: Save to DB
        review_status = "pending_review" if review_mode or not upload else "approved"
        result.review_status = review_status
        db_video_id = save_video_record(
            post_id=post.id,
            script_path=script_path,
            audio_path=audio_path,
            video_path=video_path,
            yt_title=video_script.yt_title,
            yt_description=video_script.yt_description,
            yt_hashtags=json.dumps(video_script.yt_hashtags),
            duration_secs=result.duration,
            thumbnail_path=thumb_path,
            subreddit=post.subreddit,
            hook=video_script.hook,
            quality_score=quality.score,
            review_status=review_status,
            processing_secs=total_processing,
            stage_timings=result.stage_timings,
        )

        for stage_name, duration_secs in result.stage_timings.items():
            log_stage_timing(db_video_id, stage_name, duration_secs)

        # Step 6: Upload (optional)
        if upload and not review_mode:
            progress("Uploading to YouTube...", 85)
            uploader = simulate_upload if dry_run else upload_video
            upload_res = uploader(
                video_path=video_path,
                title=video_script.yt_title,
                description=video_script.yt_description,
                hashtags=video_script.yt_hashtags,
                thumbnail_path=thumb_path,
            )
            result.upload_result = upload_res
            save_upload(
                video_id=db_video_id,
                platform="youtube",
                platform_video_id=upload_res.get("video_id"),
                upload_url=upload_res.get("url"),
                status=upload_res.get("status", "success"),
            )
            if upload_res.get("url"):
                mark_video_uploaded(db_video_id, upload_res.get("url"), upload_res.get("video_id"))

        update_post_status(post.id, "review_pending" if review_mode else "completed")
        result.success = True
        progress("Done!", 100)
        logger.info(f"✅ Pipeline complete for post {post.id}")

    except Exception as e:
        result.error = str(e)
        result.success = False
        update_post_status(post.id, "failed", str(e))
        logger.error(f"❌ Pipeline failed for post {post.id}: {e}", exc_info=True)

    return result


# ── Batch pipeline ────────────────────────────────────────────────────────────────

def run_pipeline(
    max_videos: int = 3,
    upload: bool = False,
    dry_run: bool = True,
    review_mode: bool = False,
    parallel_workers: int = 1,
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> list[PipelineResult]:
    """
    Full automated pipeline run:
    1. Init DB
    2. Scrape posts
    3. Process each post through the full pipeline
    4. Return results
    """
    init_db()
    run_id = start_pipeline_run()
    results = []
    llm = LLMRouter()

    try:
        logger.info(f"🚀 Starting pipeline run #{run_id} (max_videos={max_videos})")

        # Scrape
        posts = scrape_posts(max_posts=max_videos * 2)  # Scrape extra as buffer
        logger.info(f"Scraped {len(posts)} eligible posts")

        if not posts:
            logger.warning("No posts scraped. Check Reddit credentials or subreddit config.")
            finish_pipeline_run(run_id, "no_posts", posts_found=0, videos_made=0)
            return []

        selected_posts = []
        seen_post_ids = set()
        for post in posts:
            if post.id in seen_post_ids:
                continue
            seen_post_ids.add(post.id)
            selected_posts.append(post)
            if len(selected_posts) >= max_videos:
                break

        if parallel_workers <= 1:
            videos_made = 0
            for post in selected_posts:
                result = run_post_pipeline(
                    post,
                    llm,
                    upload=upload,
                    dry_run=dry_run,
                    review_mode=review_mode,
                    progress_cb=progress_cb,
                )
                results.append(result)
                if result.success:
                    videos_made += 1
                time.sleep(CONFIG["pipeline"]["rate_limit_delay"])
        else:
            videos_made = 0
            with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
                futures = {
                    executor.submit(
                        run_post_pipeline,
                        post,
                        llm,
                        upload,
                        dry_run,
                        review_mode,
                        progress_cb,
                    ): post.id
                    for post in selected_posts
                }
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
                    if result.success:
                        videos_made += 1

        finish_pipeline_run(run_id, "success",
                             posts_found=len(posts),
                             videos_made=videos_made)
        logger.info(f"🎉 Pipeline run #{run_id} complete: {videos_made} videos created")

    except Exception as e:
        finish_pipeline_run(run_id, "failed", error_msg=str(e))
        logger.error(f"Pipeline run #{run_id} failed: {e}", exc_info=True)
        raise

    return results
