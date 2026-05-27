"""
Heuristic content quality scoring for Shorts scripts.
"""

import re
from dataclasses import dataclass

from config.utils import CONFIG

EMOTION_WORDS = {
    "betrayed", "cheated", "secret", "shocked", "angry", "crying",
    "panic", "caught", "exposed", "hidden", "destroyed", "heartbroken",
    "tense", "nervous", "twist", "discovered", "jealous", "fear",
    "nightmare", "unbelievable", "insane", "awkward", "toxic",
}

HOOK_WORDS = {
    "wait", "imagine", "this", "secret", "hidden", "accident", "caught",
    "shocking", "wild", "unbelievable", "what", "why", "how", "suddenly",
}


@dataclass
class QualityResult:
    score: float
    emotional_intensity: float
    hook_quality: float
    pacing: float
    readability: float
    retention: float
    reject: bool


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", text.lower())


def _sentences(text: str) -> list[str]:
    chunks = re.split(r"[.!?]+", text)
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _flesch_readability(text: str) -> float:
    words = _words(text)
    sentences = _sentences(text)
    if not words or not sentences:
        return 0.0

    syllables = 0
    for word in words:
        syllables += max(1, len(re.findall(r"[aeiouy]+", word)))

    wps = len(words) / len(sentences)
    spw = syllables / len(words)
    score = 206.835 - (1.015 * wps) - (84.6 * spw)
    return max(0.0, min(100.0, score))


def _emotion_score(text: str) -> float:
    words = _words(text)
    if not words:
        return 0.0
    hits = sum(1 for word in words if word in EMOTION_WORDS)
    return min(100.0, (hits / max(8, len(words) / 25)) * 100)


def _hook_score(hook: str) -> float:
    if not hook:
        return 0.0
    words = _words(hook)
    if not words:
        return 0.0
    curiosity = sum(1 for word in words if word in HOOK_WORDS)
    length_bonus = 1.0 if 4 <= len(words) <= 18 else 0.5
    line_bonus = 1.0 if hook.count("\n") <= 1 else 0.8
    return min(100.0, (curiosity / max(2, len(words) / 6)) * 60 * length_bonus * line_bonus)


def _pacing_score(text: str) -> float:
    sentences = _sentences(text)
    if not sentences:
        return 0.0
    avg_len = sum(len(_words(sentence)) for sentence in sentences) / len(sentences)
    short_sentence_bonus = sum(1 for sentence in sentences if len(_words(sentence)) <= 14)
    fast_flow = max(0.0, 100.0 - abs(avg_len - 10.0) * 7.0)
    bonus = min(20.0, short_sentence_bonus * 4.0)
    return max(0.0, min(100.0, fast_flow + bonus))


def score_script(script: str, hook: str = "", title: str = "") -> QualityResult:
    """Score a script and decide whether it should be rejected."""
    emotional_intensity = _emotion_score(script)
    hook_quality = _hook_score(hook or title)
    pacing = _pacing_score(script)
    readability = _flesch_readability(script)

    retention = (
        emotional_intensity * 0.32
        + hook_quality * 0.28
        + pacing * 0.24
        + readability * 0.16
    )

    min_score = CONFIG.get("quality", {}).get("min_score", 58)
    reject = retention < min_score or emotional_intensity < 20 or hook_quality < 20

    return QualityResult(
        score=round(retention, 2),
        emotional_intensity=round(emotional_intensity, 2),
        hook_quality=round(hook_quality, 2),
        pacing=round(pacing, 2),
        readability=round(readability, 2),
        retention=round(retention, 2),
        reject=reject,
    )
