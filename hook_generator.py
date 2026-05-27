"""
Hook generation for Shorts-first narratives.
"""

from dataclasses import dataclass

from config.utils import get_logger

logger = get_logger("hook_generator")


@dataclass
class HookResult:
    hook: str
    tone: str


HOOK_SYSTEM_PROMPT = """You write viral YouTube Shorts hooks.
Rules:
- Return only the hook text.
- Keep it to 1-2 short lines maximum.
- Optimize for the first 1-3 seconds.
- Trigger curiosity, shock, tension, or emotion immediately.
- Avoid generic introductions, context dumps, and filler.
- Make it sound like a viral TikTok storyteller.
- Every word should increase curiosity.
"""

HOOK_USER_PROMPT = """Write a Shorts hook for this Reddit story.

TITLE: {title}
TEXT: {text}

Return only the hook. No explanation."""


def _fallback_hook(title: str, text: str) -> str:
    text = text.strip().replace("\n", " ")
    if len(text) > 140:
        text = text[:140].rstrip() + "..."
    title = title.strip()
    if title:
        return f"{title[:70]}"
    return text or "You are not going to believe this."


def _clean_hook(raw: str) -> str:
    hook = raw.strip().replace("```", "")
    hook = " ".join(hook.split())
    lines = [line.strip() for line in hook.splitlines() if line.strip()]
    hook = "\n".join(lines[:2])
    return hook[:220].strip()


def generate_hook(post, llm=None) -> HookResult:
    """Generate a retention-focused hook for a Reddit post."""
    text = getattr(post, "text", "") or getattr(post, "full_text", "")
    title = getattr(post, "title", "")

    if llm is None:
        logger.warning("No LLM provided for hook generation; using fallback hook.")
        return HookResult(hook=_fallback_hook(title, text), tone="fallback")

    user_prompt = HOOK_USER_PROMPT.format(title=title, text=text[:1800])
    try:
        raw = llm.generate(HOOK_SYSTEM_PROMPT, user_prompt)
        hook = _clean_hook(raw)
        if not hook:
            raise ValueError("empty hook")
        logger.info("Generated hook (%s chars)", len(hook))
        return HookResult(hook=hook, tone="llm")
    except Exception as exc:
        logger.warning("Hook generation failed: %s", exc)
        return HookResult(hook=_fallback_hook(title, text), tone="fallback")
