"""
Summarizer: rewrite Reddit posts into punchy Shorts scripts.
Provider order: Gemini → OpenRouter → Ollama (local).
Also generates YouTube titles, descriptions, and hashtags.
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Optional

import requests

from config.utils import CONFIG, RateLimiter, env, get_logger, retry
from scraper.scraper import RedditPost

logger = get_logger("summarizer")

# ── Prompts ──────────────────────────────────────────────────────────────────────

SCRIPT_SYSTEM_PROMPT = """You are a viral short-form video scriptwriter specializing in Reddit storytelling content.
Your scripts are optimized for YouTube Shorts and TikTok retention.
Rules:
- Write like a viral TikTok storyteller.
- Keep the pacing fast and conversational.
- Use short sentences.
- Every sentence should increase curiosity.
- Create tension every 5-7 seconds.
- Avoid filler, long explanations, and robotic wording.
- Use cliffhanger transitions and emotional escalation.
- Keep it between 150-220 words.
- End with ONE of these CTAs: "What would you do?", "Was he wrong?", "Was she right?", "Comment your thoughts.", "Drop your verdict below."
- NEVER copy the Reddit text verbatim — rewrite it completely in your own words.
- Do NOT mention Reddit, upvotes, or any platform.
- Make it feel like you're telling a friend an unbelievable story.
- Use only plain text, no markdown, no asterisks, no headers."""

SCRIPT_USER_PROMPT = """Turn this Reddit story into a viral short-form video script.

STORY TITLE: {title}
STORY: {text}

HOOK TO OPEN WITH: {hook}

Write ONLY the script. No intro, no explanation. Start directly with the hook."""

METADATA_SYSTEM_PROMPT = """You are an SEO expert for YouTube Shorts.
Generate metadata in strict JSON format only. No markdown, no explanation."""

METADATA_USER_PROMPT = """Generate YouTube metadata for this Shorts video script.

SCRIPT: {script}
HOOK: {hook}

Return ONLY valid JSON with these exact keys:
{{
  "title": "YouTube title under 100 chars, no clickbait emojis at start",
  "description": "2-3 sentence description under 300 chars",
  "hashtags": ["hashtag1", "hashtag2", ...] (15-20 tags, no # symbol, mix broad and niche)
}}"""


# ── Provider implementations ──────────────────────────────────────────────────────

class GeminiProvider:
    """Google Gemini API (free tier)."""

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    rate_limiter = RateLimiter(calls_per_second=0.5)

    def __init__(self):
        self.api_key = env("GEMINI_API_KEY", "")
        self.model = CONFIG["llm"]["gemini_model"]

    def available(self) -> bool:
        return bool(self.api_key)

    @retry(attempts=2, delay=5, logger_name="summarizer")
    def generate(self, system: str, user: str) -> str:
        self.rate_limiter.wait()
        url = self.BASE_URL.format(model=self.model)
        payload = {
            "contents": [{"parts": [{"text": f"{system}\n\n{user}"}]}],
            "generationConfig": {
                "maxOutputTokens": CONFIG["llm"]["max_tokens"],
                "temperature": CONFIG["llm"]["temperature"],
            },
        }
        resp = requests.post(
            f"{url}?key={self.api_key}",
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()


class OpenRouterProvider:
    """OpenRouter free-tier models (OpenAI-compatible API)."""

    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
    rate_limiter = RateLimiter(calls_per_second=0.3)

    def __init__(self):
        self.api_key = env("OPENROUTER_API_KEY", "")
        self.model = CONFIG["llm"]["openrouter_model"]

    def available(self) -> bool:
        return bool(self.api_key)

    @retry(attempts=2, delay=8, logger_name="summarizer")
    def generate(self, system: str, user: str) -> str:
        self.rate_limiter.wait()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/reddit-shorts-factory",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": CONFIG["llm"]["max_tokens"],
            "temperature": CONFIG["llm"]["temperature"],
        }
        resp = requests.post(self.BASE_URL, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


class OllamaProvider:
    """Local Ollama models — no API key required."""

    rate_limiter = RateLimiter(calls_per_second=1.0)

    def __init__(self):
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.model = CONFIG["llm"]["ollama_model"]

    def available(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    @retry(attempts=2, delay=5, logger_name="summarizer")
    def generate(self, system: str, user: str) -> str:
        self.rate_limiter.wait()
        payload = {
            "model": self.model,
            "prompt": f"[SYSTEM]\n{system}\n\n[USER]\n{user}",
            "stream": False,
            "options": {
                "num_predict": CONFIG["llm"]["max_tokens"],
                "temperature": CONFIG["llm"]["temperature"],
            },
        }
        resp = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["response"].strip()


# ── LLM router ────────────────────────────────────────────────────────────────────

class LLMRouter:
    """Tries providers in configured order until one succeeds."""

    _PROVIDERS = {
        "gemini": GeminiProvider,
        "openrouter": OpenRouterProvider,
        "ollama": OllamaProvider,
    }

    def __init__(self):
        order = CONFIG["llm"]["provider_order"]
        self.providers = []
        for name in order:
            if name in self._PROVIDERS:
                self.providers.append((name, self._PROVIDERS[name]()))
        logger.info(f"LLM providers loaded: {[n for n, _ in self.providers]}")

    def generate(self, system: str, user: str) -> str:
        for name, provider in self.providers:
            if not provider.available():
                logger.debug(f"Provider '{name}' not available, skipping.")
                continue
            try:
                logger.debug(f"Using provider: {name}")
                result = provider.generate(system, user)
                if result:
                    logger.info(f"Generated via {name} ({len(result)} chars)")
                    return result
            except Exception as e:
                logger.warning(f"Provider '{name}' failed: {e}")
        raise RuntimeError("All LLM providers failed. Check your API keys and local Ollama setup.")


# ── Script generation ─────────────────────────────────────────────────────────────

@dataclass
class VideoScript:
    post_id: str
    hook: str
    script: str
    yt_title: str
    yt_description: str
    yt_hashtags: list[str]
    word_count: int


def generate_script(post: RedditPost, llm: LLMRouter, hook: str) -> str:
    """Generate a Shorts script from a Reddit post."""
    # Truncate overly long posts to stay within token limits
    max_text_chars = 2000
    text = post.text[:max_text_chars] if len(post.text) > max_text_chars else post.text

    user_prompt = SCRIPT_USER_PROMPT.format(title=post.title, text=text, hook=hook)
    script = llm.generate(SCRIPT_SYSTEM_PROMPT, user_prompt)

    # Validate length
    word_count = len(script.split())
    if word_count < 50:
        raise ValueError(f"Script too short ({word_count} words), retrying...")
    if word_count > 280:
        # Trim to roughly 220 words
        script = " ".join(script.split()[:220])

    return script


def generate_metadata(script: str, hook: str, llm: LLMRouter) -> dict:
    """Generate YouTube title, description, and hashtags."""
    user_prompt = METADATA_USER_PROMPT.format(script=script[:1000], hook=hook[:200])

    raw = llm.generate(METADATA_SYSTEM_PROMPT, user_prompt)

    # Strip markdown code fences if present
    raw = re.sub(r"```(?:json)?", "", raw).strip()

    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Metadata JSON parse failed; using fallbacks.")
        metadata = {
                "title": "You won't believe this Reddit story...",
                "description": "An incredible Reddit story that will leave you speechless.",
            "hashtags": ["reddit", "redditstories", "shorts", "storytelling",
                         "redditreadings", "viralstories", "aitah"],
        }

    # Ensure hashtags are clean (no # symbol)
    metadata["hashtags"] = [h.lstrip("#") for h in metadata.get("hashtags", [])]

    return metadata


# ── Public interface ──────────────────────────────────────────────────────────────

def summarize_post(post: RedditPost, llm: Optional[LLMRouter] = None, hook: str = "") -> VideoScript:
    """
    Full summarization pipeline for a single post.
    Returns a VideoScript ready for TTS and video generation.
    """
    if llm is None:
        llm = LLMRouter()

    logger.info(f"Summarizing post {post.id} from r/{post.subreddit}")

    script = generate_script(post, llm, hook=hook)
    metadata = generate_metadata(script, hook=hook, llm=llm)

    return VideoScript(
        post_id=post.id,
        hook=hook,
        script=script,
        yt_title=metadata["title"],
        yt_description=metadata["description"],
        yt_hashtags=metadata["hashtags"],
        word_count=len(script.split()),
    )
