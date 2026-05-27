"""
Video editor: combine gameplay footage, narration audio, and burned subtitles
into a 1080x1920 YouTube Shorts-ready MP4.

Uses FFmpeg for all heavy lifting (fast, reliable, no GPU required).
MoviePy used for simpler clip manipulation and fallback.
"""

import os
import random
import subprocess
from pathlib import Path
from typing import List, Optional

from config.utils import CONFIG, ensure_dir, get_logger

logger = get_logger("video_editor")


# ── Gameplay pool ─────────────────────────────────────────────────────────────────

def get_gameplay_clips(folder: str) -> List[Path]:
    """Return all video files in the gameplay folder."""
    p = Path(folder)
    if not p.exists():
        logger.warning(f"Gameplay folder '{folder}' not found. Creating it.")
        p.mkdir(parents=True, exist_ok=True)
        return []

    extensions = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
    clips = [f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in extensions]
    logger.info(f"Found {len(clips)} gameplay clips in '{folder}'")
    return clips


def get_background_tracks(folder: str, mood: Optional[str] = None) -> List[Path]:
    """Return background music tracks for a selected mood."""
    root = Path(folder)
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        return []

    extensions = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
    search_root = root / mood if mood and (root / mood).exists() else root
    tracks = [f for f in search_root.rglob("*") if f.is_file() and f.suffix.lower() in extensions]
    if not tracks and search_root != root:
        tracks = [f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in extensions]
    return tracks


def get_video_duration(path: str) -> float:
    """Get video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


# ── Clip selection and looping ────────────────────────────────────────────────────

def select_and_prepare_gameplay(
    target_duration: float,
    gameplay_folder: str,
    temp_dir: Path,
) -> str:
    """
    Select random gameplay clips, loop or trim to match target_duration.
    Returns path to a single prepared gameplay video (no audio).
    """
    clips = get_gameplay_clips(gameplay_folder)

    if not clips:
        # Generate a placeholder black video if no clips available
        logger.warning("No gameplay clips found. Using black background.")
        placeholder = str(temp_dir / "placeholder.mp4")
        _generate_placeholder(target_duration, placeholder)
        return placeholder

    # Randomly select and shuffle clips until we have enough footage
    random.shuffle(clips)
    selected = []
    total = 0.0

    for clip in clips * 5:  # allow repeating clips if needed
        d = get_video_duration(str(clip))
        if d > 0:
            selected.append(str(clip))
            total += d
        if total >= target_duration:
            break

    # Write concat list
    concat_file = temp_dir / "concat.txt"
    with open(concat_file, "w") as f:
        for c in selected:
            f.write(f"file '{c}'\n")

    # Concat, then trim, then crop to 9:16
    raw_concat = str(temp_dir / "raw_concat.mp4")
    trimmed = str(temp_dir / "gameplay_prepared.mp4")

    # Step 1: Concat
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy", raw_concat,
    ], check=True, capture_output=True)

    # Step 2: Trim to target duration + crop to vertical 9:16 (1080x1920)
    # Random start offset for variety with a slight pan/zoom.
    total_src = get_video_duration(raw_concat)
    max_offset = max(0, total_src - target_duration - 1)
    start_offset = random.uniform(0, max_offset) if max_offset > 0 else 0
    scale_factor = random.uniform(1.08, 1.15)
    x_shift = random.randint(-42, 42)
    y_shift = random.randint(-36, 36)

    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(start_offset),
        "-i", raw_concat,
        "-t", str(target_duration + 0.5),
        "-vf", (
            f"scale=iw*{scale_factor:.4f}:ih*{scale_factor:.4f},"
            f"crop=1080:1920:x='(in_w-1080)/2+{x_shift}+sin(n/48)*18':"
            f"y='(in_h-1920)/2+{y_shift}+cos(n/54)*22'"
        ),
        "-an",          # Remove audio from gameplay
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-r", str(CONFIG["video"]["fps"]),
        trimmed,
    ], check=True, capture_output=True)

    return trimmed


def _generate_placeholder(duration: float, output_path: str):
    """Generate a dark gradient placeholder when no gameplay clips exist."""
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x1a1a2e:size=1080x1920:rate=30:duration={duration}",
        "-c:v", "libx264", "-preset", "fast",
        output_path,
    ], check=True, capture_output=True)


# ── Overlay / composite ───────────────────────────────────────────────────────────

def add_dark_overlay(video_path: str, output_path: str, opacity: float = 0.4) -> str:
    """Add semi-transparent dark overlay to gameplay to improve subtitle readability."""
    alpha = int(opacity * 255)
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"drawbox=0:0:iw:ih:color=black@{opacity:.2f}:t=fill",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        output_path,
    ], check=True, capture_output=True)
    return output_path


def infer_music_mood(subreddit: Optional[str] = None, hook: Optional[str] = None) -> str:
    text = f"{subreddit or ''} {hook or ''}".lower()
    if any(word in text for word in ["nosleep", "horror", "scary", "ghost", "murder"]):
        return "horror"
    if any(word in text for word in ["relationship", "aita", "confession", "cheat", "wife", "husband", "family"]):
        return "dramatic"
    if any(word in text for word in ["heartbroken", "cry", "emotional", "loss"]):
        return "emotional"
    return "suspenseful"


def select_background_music(mood: str, music_root: str = "assets/music") -> Optional[str]:
    music_root = CONFIG.get("music", {}).get("root", music_root)
    tracks = get_background_tracks(music_root, mood)
    if not tracks:
        return None
    return str(random.choice(tracks))


def mix_narration_with_music(
    narration_path: str,
    music_path: Optional[str],
    output_path: str,
    duration: float,
    narration_volume: float = 1.0,
    music_volume: float = 0.12,
) -> str:
    if not music_path or not os.path.exists(music_path):
        return narration_path

    ensure_dir(Path(output_path).parent)
    fade_out_start = max(0.0, duration - 2.0)
    filter_complex = (
        f"[0:a]volume={music_volume:.2f},afade=t=in:st=0:d=1.2,"
        f"afade=t=out:st={fade_out_start:.2f}:d=1.5[music];"
        f"[1:a]volume={narration_volume:.2f}[narr];"
        f"[narr][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )

    subprocess.run([
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", music_path,
        "-i", narration_path,
        "-filter_complex", filter_complex,
        "-map", "[aout]",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path,
    ], check=True, capture_output=True)

    return output_path


# ── Final composition ─────────────────────────────────────────────────────────────

def compose_video(
    gameplay_path: str,
    audio_path: str,
    subtitle_path: str,  # .ass preferred for styling
    output_path: str,
    post_id: str,
    audio_duration: Optional[float] = None,
    subreddit: Optional[str] = None,
    hook: Optional[str] = None,
) -> str:
    """
    Compose final Shorts video:
    1. Overlay gameplay with audio
    2. Burn subtitles
    3. Export 1080x1920 @ 30fps H.264 MP4

    Returns output_path.
    """
    ensure_dir(Path(output_path).parent)
    temp_dir = ensure_dir(f"output/temp/{post_id}")

    # Step 1: Get audio duration
    if audio_duration is None or audio_duration <= 0:
        audio_duration = get_video_duration(audio_path)
        if audio_duration <= 0:
            cmd = [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", audio_path
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            audio_duration = float(res.stdout.strip() or "45")

    logger.info(f"Audio duration: {audio_duration:.1f}s")

    # Step 2: Prepare gameplay video
    gameplay_prepared = select_and_prepare_gameplay(
        audio_duration, CONFIG["video"]["gameplay_folder"], temp_dir
    )

    # Step 3: Add dark overlay for readability
    gameplay_overlaid = str(temp_dir / "gameplay_overlaid.mp4")
    add_dark_overlay(gameplay_prepared, gameplay_overlaid, opacity=0.35)

    # Step 4: Mix optional background music with narration
    mood = infer_music_mood(subreddit=subreddit, hook=hook)
    music_path = select_background_music(mood)
    mixed_audio = str(temp_dir / "mixed_audio.aac")
    narration_mix = mix_narration_with_music(
        audio_path,
        music_path,
        mixed_audio,
        audio_duration,
    )

    # Step 5: Merge gameplay + mixed audio
    merged_path = str(temp_dir / "merged.mp4")
    subprocess.run([
        "ffmpeg", "-y",
        "-i", gameplay_overlaid,
        "-i", narration_mix,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        merged_path,
    ], check=True, capture_output=True)

    # Step 6: Burn subtitles
    ext = Path(subtitle_path).suffix.lower()
    if ext == ".ass":
        subtitle_filter = f"ass={subtitle_path}"
    else:
        subtitle_filter = (
            f"subtitles={subtitle_path}:force_style="
            "'Fontname=Arial Rounded MT Bold,"
            "Fontsize=55,"
            "PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,"
            "Bold=1,Outline=3,Shadow=2,"
            "Alignment=2,MarginV=80'"
        )

    subprocess.run([
        "ffmpeg", "-y",
        "-i", merged_path,
        "-vf", subtitle_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        output_path,
    ], check=True, capture_output=True)

    logger.info(f"Final video composed: {output_path}")

    # Cleanup temp dir
    import shutil
    try:
        shutil.rmtree(str(temp_dir))
    except Exception:
        pass

    return output_path


# ── Thumbnail generation ──────────────────────────────────────────────────────────

def generate_thumbnail(video_path: str, title: str, output_path: str) -> str:
    """Extract a frame from the video and overlay the title as thumbnail."""
    ensure_dir(Path(output_path).parent)

    # Extract frame at 2 seconds
    frame_path = output_path.replace(".jpg", "_frame.jpg")
    subprocess.run([
        "ffmpeg", "-y", "-ss", "2", "-i", video_path,
        "-vframes", "1", "-q:v", "2", frame_path,
    ], check=True, capture_output=True)

    # Overlay title text
    safe_title = title[:50].replace("'", "\\'").replace(":", "\\:")
    subprocess.run([
        "ffmpeg", "-y", "-i", frame_path,
        "-vf", (
            f"drawtext=text='{safe_title}':"
            "fontcolor=white:fontsize=60:font=Arial Rounded MT Bold:"
            "x=(w-text_w)/2:y=h-200:"
            "shadowcolor=black:shadowx=3:shadowy=3:"
            "box=1:boxcolor=black@0.5:boxborderw=20"
        ),
        output_path,
    ], check=True, capture_output=True)

    Path(frame_path).unlink(missing_ok=True)
    logger.info(f"Thumbnail saved: {output_path}")
    return output_path


# ── Public interface ──────────────────────────────────────────────────────────────

def create_shorts_video(
    post_id: str,
    audio_path: str,
    subtitle_path: str,
    yt_title: str,
    audio_duration: Optional[float] = None,
    subreddit: Optional[str] = None,
    hook: Optional[str] = None,
) -> tuple[str, str]:
    """
    Full video creation pipeline for a single post.
    Returns (video_path, thumbnail_path).
    """
    output_dir = ensure_dir("output/videos")
    thumb_dir = ensure_dir("output/thumbnails")

    video_path = str(output_dir / f"{post_id}_shorts.mp4")
    thumb_path = str(thumb_dir / f"{post_id}_thumb.jpg")

    compose_video(
        gameplay_path=CONFIG["video"]["gameplay_folder"],
        audio_path=audio_path,
        subtitle_path=subtitle_path,
        output_path=video_path,
        post_id=post_id,
        audio_duration=audio_duration,
        subreddit=subreddit,
        hook=hook,
    )

    generate_thumbnail(video_path, yt_title, thumb_path)

    return video_path, thumb_path
