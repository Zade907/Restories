"""
Scheduler: runs the pipeline automatically at configurable intervals.
Uses APScheduler for reliability. Also supports cron/Task Scheduler integration.
"""

import signal
import sys
import time
from datetime import datetime

from config.utils import CONFIG, get_logger
from pipeline import run_pipeline

logger = get_logger("scheduler")


def run_scheduled_pipeline():
    """Single scheduled pipeline execution."""
    logger.info(f"⏰ Scheduled run triggered at {datetime.now().isoformat()}")
    cfg = CONFIG["scheduler"]

    try:
        results = run_pipeline(
            max_videos=cfg.get("max_videos_per_run", 3),
            upload=True,
            dry_run=False,
        )
        successes = sum(1 for r in results if r.success)
        logger.info(f"Scheduled run complete: {successes}/{len(results)} videos generated.")
    except Exception as e:
        logger.error(f"Scheduled run failed: {e}", exc_info=True)


def start_scheduler():
    """Start APScheduler with the configured interval."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        logger.error("APScheduler not installed. Run: pip install apscheduler")
        sys.exit(1)

    cfg = CONFIG["scheduler"]
    interval_hours = cfg.get("interval_hours", 6)

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_scheduled_pipeline,
        trigger=IntervalTrigger(hours=interval_hours),
        id="reddit_shorts_pipeline",
        name="Reddit Shorts Factory",
        replace_existing=True,
        max_instances=1,
    )

    # Also run immediately on start
    scheduler.add_job(
        run_scheduled_pipeline,
        id="initial_run",
        name="Initial pipeline run",
        max_instances=1,
    )

    def handle_shutdown(signum, frame):
        logger.info("Shutting down scheduler...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    logger.info(f"🕐 Scheduler started. Running every {interval_hours} hours.")
    scheduler.start()


if __name__ == "__main__":
    start_scheduler()
