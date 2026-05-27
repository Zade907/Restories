"""
Subtitle generation from the final script and narration duration.

Primary path: split the script into subtitle chunks and allocate timings
proportionally from the final TTS duration.
Optional debug path: Whisper transcription for comparison only.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from config.utils import CONFIG, ensure_dir, get_logger

logger = get_logger("subtitles")


@dataclass
class WordTiming:
    word: str
    start: float
    end: float


@dataclass
class SubtitleBlock:
    index: int
    start: float
    end: float
    words: List[WordTiming]

    @property
    def text(self) -> str:
        return " ".join(word.word for word in self.words)

    def to_srt_time(self, t: float) -> str:
        hours = int(t // 3600)
        minutes = int((t % 3600) // 60)
        seconds = int(t % 60)
        milliseconds = int((t % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    def to_srt_entry(self) -> str:
        return (
            f"{self.index}\n"
            f"{self.to_srt_time(self.start)} --> {self.to_srt_time(self.end)}\n"
            f"{self.text}\n"
        )


def transcribe_with_whisper(audio_path: str) -> List[WordTiming]:
    """Optional debug helper that transcribes audio with Whisper."""
    try:
        import whisper

        model_name = CONFIG["subtitles"].get("whisper_model", "base")
        logger.info("Loading Whisper model '%s' for debug transcription...", model_name)
        model = whisper.load_model(model_name)
        result = model.transcribe(audio_path, word_timestamps=True, language="en", verbose=False)

        words: List[WordTiming] = []
        for segment in result.get("segments", []):
            for word_data in segment.get("words", []):
                words.append(
                    WordTiming(
                        word=word_data["word"].strip(),
                        start=word_data["start"],
                        end=word_data["end"],
                    )
                )

        logger.info("Whisper debug transcription returned %s words", len(words))
        return words
    except ImportError:
        logger.warning("Whisper not installed; skipping debug transcription.")
    except Exception as exc:
        logger.warning("Whisper debug transcription failed: %s", exc)
    return []


def _normalize_script(script: str) -> str:
    cleaned = script.replace("\r", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def split_script_into_chunks(script: str, target_words_per_chunk: int = 6) -> List[str]:
    """Split a script into compact subtitle chunks."""
    script = _normalize_script(script)
    if not script:
        return []

    candidates = re.split(r"(?<=[.!?])\s+|(?<=[,;:])\s+", script)
    chunks: List[str] = []

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue

        words = candidate.split()
        if len(words) <= target_words_per_chunk and len(candidate) <= 52:
            chunks.append(candidate)
            continue

        for start in range(0, len(words), target_words_per_chunk):
            subchunk = " ".join(words[start:start + target_words_per_chunk]).strip()
            if subchunk:
                chunks.append(subchunk)

    return chunks


def _chunk_weight(chunk: str) -> float:
    words = chunk.split()
    weight = max(1.0, float(len(words)))
    if chunk.endswith(("!", "?")):
        weight *= 1.2
    elif chunk.endswith((",", ";", ":")):
        weight *= 1.08
    return weight


def build_word_timings_for_chunk(chunk: str, start: float, end: float) -> List[WordTiming]:
    words = [word for word in chunk.split() if word]
    if not words:
        return []

    duration = max(0.2, end - start)
    lengths = [max(1, len(re.sub(r"[^A-Za-z0-9']", "", word))) for word in words]
    total = float(sum(lengths))
    current = start
    timings: List[WordTiming] = []

    for index, word in enumerate(words):
        share = lengths[index] / total if total else 1 / len(words)
        word_duration = duration * share
        next_time = end if index == len(words) - 1 else min(end, current + word_duration)
        timings.append(WordTiming(word=word, start=current, end=max(current + 0.05, next_time)))
        current = timings[-1].end

    if timings:
        timings[-1].end = end
    return timings


def allocate_timings(chunks: List[str], audio_duration: float) -> List[SubtitleBlock]:
    if not chunks:
        return []

    weights = [_chunk_weight(chunk) for chunk in chunks]
    total_weight = sum(weights) or float(len(chunks))

    blocks: List[SubtitleBlock] = []
    current_start = 0.0
    min_block_duration = 0.75
    max_block_duration = 3.25

    for index, chunk in enumerate(chunks, start=1):
        remaining_time = max(0.0, audio_duration - current_start)
        remaining_weight = sum(weights[index - 1:]) or 1.0
        desired = audio_duration * (weights[index - 1] / total_weight)
        duration = max(min_block_duration, min(max_block_duration, desired))

        if index == len(chunks):
            duration = max(min_block_duration, remaining_time)
        else:
            max_allowed = max(min_block_duration, remaining_time - (len(chunks) - index) * min_block_duration)
            duration = min(duration, max_allowed)

        end = min(audio_duration, current_start + duration)
        words = build_word_timings_for_chunk(chunk, current_start, end)
        blocks.append(SubtitleBlock(index=index, start=current_start, end=end, words=words))

        current_start = end
        if current_start >= audio_duration:
            break

    if blocks and blocks[-1].end < audio_duration:
        blocks[-1].end = audio_duration
        if blocks[-1].words:
            blocks[-1].words[-1].end = audio_duration

    return blocks


def export_srt(blocks: List[SubtitleBlock], output_path: str) -> str:
    ensure_dir(Path(output_path).parent)
    content = "\n".join(block.to_srt_entry() for block in blocks)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    logger.info("SRT saved: %s (%s blocks)", output_path, len(blocks))
    return output_path


def _escape_ass_text(text: str) -> str:
    return text.replace("{", "\\{").replace("}", "\\}").replace("\n", " ")


def _karaoke_text(words: List[WordTiming]) -> str:
    parts: List[str] = []
    for word in words:
        duration_cs = max(1, int(round((word.end - word.start) * 100)))
        parts.append(f"{{\\k{duration_cs}}}{_escape_ass_text(word.word)}")
    return " ".join(parts)


def export_ass(blocks: List[SubtitleBlock], output_path: str) -> str:
    cfg = CONFIG["video"]
    font_size = cfg.get("subtitle_font_size", 55)
    primary_color = "&H00FFFFFF"
    outline_color = "&H00000000"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Rounded MT Bold,{font_size},{primary_color},&H000000FF,{outline_color},&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,20,20,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def fmt_time(t: float) -> str:
        hours = int(t // 3600)
        minutes = int((t % 3600) // 60)
        seconds = int(t % 60)
        centiseconds = int((t % 1) * 100)
        return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"

    lines = [header]
    for block in blocks:
        text = _karaoke_text(block.words) if block.words else _escape_ass_text(block.text)
        lines.append(f"Dialogue: 0,{fmt_time(block.start)},{fmt_time(block.end)},Default,,0,0,0,,{text}")

    ensure_dir(Path(output_path).parent)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    logger.info("ASS subtitle file saved: %s", output_path)
    return output_path


def generate_subtitles(
    script: str,
    audio_duration: float,
    post_id: str,
    audio_path: Optional[str] = None,
    debug_whisper: bool = False,
) -> tuple[str, str, List[SubtitleBlock]]:
    """
    Generate SRT and ASS subtitle files from script text.

    The default path derives timing from the final narration duration.
    Whisper can be enabled for debug comparison, but it is not used in the
    normal rendering flow.
    """
    srt_dir = ensure_dir("output/subtitles")
    srt_path = str(srt_dir / f"{post_id}.srt")
    ass_path = str(srt_dir / f"{post_id}.ass")

    if debug_whisper and audio_path:
        transcribe_with_whisper(audio_path)

    chunks = split_script_into_chunks(script)
    if not chunks:
        logger.warning("No script chunks available; subtitles will be empty.")
        blocks: List[SubtitleBlock] = []
    else:
        blocks = allocate_timings(chunks, audio_duration)

    export_srt(blocks, srt_path)
    export_ass(blocks, ass_path)

    return srt_path, ass_path, blocks
