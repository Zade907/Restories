"""
TTS module: convert scripts to natural-sounding narration audio.
Primary: edge-tts (Microsoft neural voices, free)
Fallback: Coqui TTS (local)
"""

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Optional

from config.utils import CONFIG, ensure_dir, get_logger, retry, sanitize_filename

logger = get_logger("tts")


# ── Edge-TTS (primary) ───────────────────────────────────────────────────────────

class EdgeTTSProvider:
    """
    Microsoft Edge TTS via the `edge-tts` Python package.
    Completely free, no API key needed.
    """

    def available(self) -> bool:
        try:
            import edge_tts
            return True
        except ImportError:
            return False

    async def _synthesize(self, text: str, voice: str, rate: str,
                           output_path: str) -> dict:
        """Async synthesis with word-level timestamps."""
        import edge_tts

        communicate = edge_tts.Communicate(text, voice, rate=rate)
        word_boundaries = []

        async with communicate as com:
            audio_bytes = bytearray()
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    audio_bytes.extend(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word_boundaries.append({
                        "word": chunk["text"],
                        "start": chunk["offset"] / 1e7,  # 100ns → seconds
                        "duration": chunk["duration"] / 1e7,
                        "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                    })

        # Write MP3 audio
        with open(output_path, "wb") as f:
            f.write(audio_bytes)

        return word_boundaries

    def generate(self, text: str, output_path: str,
                 voice: Optional[str] = None,
                 rate: Optional[str] = None) -> dict:
        """
        Generate TTS audio. Returns word boundary timing dict.
        """
        tts_cfg = CONFIG["tts"]
        voice = voice or tts_cfg["voice"]
        rate = rate or tts_cfg["rate"]

        boundaries = asyncio.run(
            self._synthesize(text, voice, rate, output_path)
        )
        logger.info(f"Edge-TTS generated: {output_path} ({len(boundaries)} words)")
        return boundaries


# ── Coqui TTS (fallback) ─────────────────────────────────────────────────────────

class CoquiTTSProvider:
    """
    Coqui TTS — local neural TTS. No API key, runs offline.
    Install: pip install TTS
    """

    MODEL = "tts_models/en/ljspeech/tacotron2-DDC"

    def available(self) -> bool:
        try:
            from TTS.api import TTS
            return True
        except ImportError:
            return False

    def generate(self, text: str, output_path: str, **kwargs) -> dict:
        from TTS.api import TTS

        tts = TTS(self.MODEL, progress_bar=False, gpu=False)
        tts.tts_to_file(text=text, file_path=output_path)
        logger.info(f"Coqui TTS generated: {output_path}")
        # Coqui doesn't give word boundaries natively; return empty
        return []


# ── TTS Router ────────────────────────────────────────────────────────────────────

class TTSEngine:
    """Tries edge-tts first, then Coqui."""

    def __init__(self):
        self.providers = [
            ("edge-tts", EdgeTTSProvider()),
            ("coqui", CoquiTTSProvider()),
        ]
        # Override with configured provider
        cfg_provider = CONFIG["tts"]["provider"]
        self.preferred = cfg_provider

    @retry(attempts=2, delay=3, logger_name="tts")
    def speak(self, text: str, output_path: str,
              voice: Optional[str] = None,
              rate: Optional[str] = None) -> dict:
        """
        Generate audio for `text`, save to `output_path`.
        Returns word boundaries list (may be empty for some providers).
        """
        ensure_dir(Path(output_path).parent)

        # Try preferred first
        for name, provider in self.providers:
            if not provider.available():
                logger.debug(f"TTS provider '{name}' not available.")
                continue
            try:
                kwargs = {}
                if voice:
                    kwargs["voice"] = voice
                if rate:
                    kwargs["rate"] = rate
                return provider.generate(text, output_path, **kwargs)
            except Exception as e:
                logger.warning(f"TTS provider '{name}' failed: {e}")

        raise RuntimeError("All TTS providers failed. Install edge-tts: pip install edge-tts")


# ── Audio utilities ───────────────────────────────────────────────────────────────

def get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        logger.warning(f"Could not determine audio duration for {audio_path}")
        return 0.0


def convert_to_wav(mp3_path: str) -> str:
    """Convert MP3 to WAV for Whisper compatibility."""
    wav_path = mp3_path.replace(".mp3", ".wav")
    subprocess.run([
        "ffmpeg", "-y", "-i", mp3_path,
        "-ar", "16000", "-ac", "1", wav_path
    ], check=True, capture_output=True)
    return wav_path


# ── Public interface ──────────────────────────────────────────────────────────────

def generate_narration(script: str, post_id: str, voice: Optional[str] = None) -> tuple[str, dict]:
    """
    Generate narration audio for a script.
    Returns (audio_file_path, word_boundaries).
    """
    output_dir = ensure_dir("output/audio")
    audio_path = str(output_dir / f"{post_id}_narration.mp3")

    engine = TTSEngine()
    word_boundaries = engine.speak(script, audio_path, voice=voice)
    duration = get_audio_duration(audio_path)

    logger.info(f"Narration ready: {audio_path} ({duration:.1f}s)")
    return audio_path, word_boundaries
