#!/usr/bin/env python3
"""
Reddit Shorts Factory — CLI entry point.

Usage:
    python main.py run             # Run pipeline (dry run)
    python main.py run --upload    # Run + upload to YouTube
    python main.py schedule        # Start automated scheduler
    python main.py dashboard       # Launch Streamlit dashboard
    python main.py scrape          # Test: scrape posts only
    python main.py test-tts        # Test: TTS engine
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent))

from config.utils import get_logger
from database.db import init_db

logger = get_logger("main")


def cmd_run(args):
    from pipeline import run_pipeline
    logger.info(f"Running pipeline (max={args.max}, dry_run={not args.upload})")
    results = run_pipeline(
        max_videos=args.max,
        upload=args.upload,
        dry_run=not args.upload,
        review_mode=args.review,
        parallel_workers=args.workers,
    )
    success = sum(1 for r in results if r.success)
    print(f"\n✅ Done: {success}/{len(results)} videos generated successfully.")
    for r in results:
        if r.success:
            print(f"  📹 {r.yt_title}")
            print(f"     Audio: {r.audio_path}")
            print(f"     Video: {r.video_path}")
        else:
            print(f"  ❌ Post {r.post_id}: {r.error}")


def cmd_scrape(args):
    from scraper.scraper import scrape_posts
    logger.info("Testing scraper...")
    posts = scrape_posts(max_posts=5)
    print(f"\nFound {len(posts)} posts:\n")
    for p in posts:
        print(f"  [{p.subreddit}] ⬆️{p.upvotes:,} | {p.title[:80]}...")


def cmd_test_tts(args):
    from tts.tts_engine import generate_narration
    test_text = (
        "So this happened last week and I honestly cannot believe it. "
        "My roommate of three years just casually told me she's been dating my ex "
        "for six months. Six. Months. And apparently everyone knew except me. "
        "What would you do?"
    )
    print("Testing TTS engine with sample text...")
    audio_path, boundaries = generate_narration(test_text, "tts_test")
    print(f"✅ TTS generated: {audio_path}")
    print(f"   Word boundaries: {len(boundaries)} words")


def cmd_schedule(args):
    from scheduler.scheduler import start_scheduler
    start_scheduler()


def cmd_dashboard(args):
    dashboard_path = Path(__file__).parent / "dashboard" / "app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(dashboard_path)])


def main():
    parser = argparse.ArgumentParser(
        description="Reddit Shorts Factory — AI-powered Reddit → YouTube Shorts pipeline"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # run
    p_run = subparsers.add_parser("run", help="Run the full pipeline")
    p_run.add_argument("--max", type=int, default=3, help="Max videos to generate")
    p_run.add_argument("--upload", action="store_true", help="Upload to YouTube")
    p_run.add_argument("--review", action="store_true", help="Keep videos in review mode before upload")
    p_run.add_argument("--workers", type=int, default=1, help="Parallel workers for batch generation")
    p_run.set_defaults(func=cmd_run)

    # scrape
    p_scrape = subparsers.add_parser("scrape", help="Test scraper only")
    p_scrape.set_defaults(func=cmd_scrape)

    # test-tts
    p_tts = subparsers.add_parser("test-tts", help="Test TTS engine")
    p_tts.set_defaults(func=cmd_test_tts)

    # schedule
    p_sched = subparsers.add_parser("schedule", help="Start automated scheduler")
    p_sched.set_defaults(func=cmd_schedule)

    # dashboard
    p_dash = subparsers.add_parser("dashboard", help="Launch Streamlit dashboard")
    p_dash.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    init_db()
    args.func(args)


if __name__ == "__main__":
    main()
