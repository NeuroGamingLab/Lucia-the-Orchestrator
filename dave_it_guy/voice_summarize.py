"""
Optional LLM summarization for Dave voice TTS (long job results).

Provider priority (see DAVE_VOICE_SUMMARY_PROVIDER):
  - anthropic: ANTHROPIC_API_KEY
  - openai: OPENAI_API_KEY
  - ollama: OLLAMA_HOST (local)
  - auto: Anthropic if key set, else OpenAI if key set, else Ollama
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable

import httpx

DEFAULT_OLLAMA = "http://127.0.0.1:11434"
MIN_CHARS_TO_SUMMARIZE = 400

ANTHROPIC_API = "https://api.anthropic.com/v1/messages"
OPENAI_API = "https://api.openai.com/v1/chat/completions"


def _strip_markdown_for_speech(text: str) -> str:
    """Remove common markdown so `say` does not read asterisks."""
    s = text
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"^#+\s*", "", s, flags=re.MULTILINE)
    s = re.sub(r"^\s*[-*]\s+", "", s, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", s).strip()


def _build_prompt(raw: str) -> str:
    return (
        "Summarize the following in 3 to 5 short sentences for voice-only playback. "
        "Use plain English only: no markdown, no bullet symbols, no asterisks, no headings.\n\n"
        + raw[:12000]
    )


def _fallback_on_error(raw: str, hint: str) -> str:
    return _strip_markdown_for_speech(raw[:1200]) + f" ... truncated. {hint}"


def _summarize_anthropic(prompt: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise ValueError("no anthropic key")
    model = os.environ.get(
        "ANTHROPIC_VOICE_SUMMARY_MODEL",
        "claude-3-5-haiku-20241022",
    )
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            ANTHROPIC_API,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        r.raise_for_status()
        data = r.json()
    blocks = data.get("content") or []
    parts = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
    return "".join(parts).strip()


def _summarize_openai(prompt: str) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise ValueError("no openai key")
    model = os.environ.get("OPENAI_VOICE_SUMMARY_MODEL", "gpt-4o-mini")
    base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/chat/completions"
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
        )
        r.raise_for_status()
        data = r.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return str(msg.get("content") or "").strip()


def _summarize_ollama(prompt: str) -> str:
    host = os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA).rstrip("/")
    model = os.environ.get("DAVE_VOICE_SUMMARY_MODEL", "llama3.2")
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            f"{host}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
            },
        )
        r.raise_for_status()
        data = r.json()
    return str(data.get("response") or "").strip()


def _provider_order() -> list[tuple[str, Callable[[str], str]]]:
    """
    Return ordered list of (name, fn) for summarize attempts.
    DAVE_VOICE_SUMMARY_PROVIDER: auto | anthropic | openai | ollama
    """
    mode = (os.environ.get("DAVE_VOICE_SUMMARY_PROVIDER") or "auto").strip().lower()
    has_a = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    has_o = bool(os.environ.get("OPENAI_API_KEY", "").strip())

    anth = ("anthropic", _summarize_anthropic)
    oai = ("openai", _summarize_openai)
    oll = ("ollama", _summarize_ollama)

    if mode == "anthropic":
        return [anth, oai, oll]
    if mode == "openai":
        return [oai, anth, oll]
    if mode == "ollama":
        return [oll, anth, oai]
    # auto
    if has_a and has_o:
        return [anth, oai, oll]
    if has_a:
        return [anth, oll, oai]
    if has_o:
        return [oai, oll, anth]
    return [oll, anth, oai]


def summarize_for_voice(long_text: str) -> str:
    """
    Produce a short spoken summary using Anthropic, OpenAI, or Ollama (see env).
    Falls back to truncated text if all providers fail.
    """
    raw = long_text.strip()
    if len(raw) < MIN_CHARS_TO_SUMMARIZE:
        return _strip_markdown_for_speech(raw[:4500])

    prompt = _build_prompt(raw)
    last_error: str | None = None

    for name, fn in _provider_order():
        try:
            out = fn(prompt)
            if out:
                return _strip_markdown_for_speech(out)[:2500]
        except Exception as e:
            last_error = str(e)
            continue

    hint = (
        "Set ANTHROPIC_API_KEY or OPENAI_API_KEY, or run Ollama at OLLAMA_HOST."
    )
    if last_error:
        hint = f"{hint} Last error: {last_error[:200]}"
    return _fallback_on_error(raw, hint)
