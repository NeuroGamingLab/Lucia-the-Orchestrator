"""
LLM conversational fallback when Dave voice does not recognize a command.

Uses the same provider priority as voice_summarize (ANTHROPIC_API_KEY, OPENAI_API_KEY, Ollama).
"""

from __future__ import annotations

import os
from collections.abc import Callable

import httpx

DEFAULT_OLLAMA = "http://127.0.0.1:11434"
ANTHROPIC_API = "https://api.anthropic.com/v1/messages"

DEFAULT_SYSTEM_PROMPT = """You are Dave, the friendly voice assistant for MasterClaw (Docker sub-agent tasks).

The user said something that was NOT recognized as a valid voice command.

Respond in 2–5 short sentences. Be warm and clear. If you are unsure what they want, ask a brief clarifying question (e.g. lightweight worker vs full OpenClaw vs list jobs). Suggest they can say "Dave list" or "Dave command four" for examples.

Rules:
- You cannot run tasks or call APIs yourself; you only reply with words.
- Never tell the user you deleted jobs or ran cleanup unless they used the explicit cleanup command.
- Do not output markdown headings or bullet symbols; plain sentences only.
- Never ask for or repeat API keys or secrets.
"""


def is_chat_exit_phrase(transcript: str) -> bool:
    """Exit multi-turn chat mode."""
    t = transcript.strip().lower()
    if not t:
        return False
    exits = (
        "exit chat",
        "quit chat",
        "stop chat",
        "end chat",
        "leave chat",
        "done chatting",
    )
    if t in exits:
        return True
    if t in ("exit", "quit", "done", "stop", "goodbye", "bye"):
        return True
    return False


def _trim_history(messages: list[dict], *, max_messages: int = 12) -> list[dict]:
    if len(messages) <= max_messages:
        return messages
    return messages[-max_messages:]


def _anthropic_chat(system: str, messages: list[dict]) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise ValueError("no anthropic key")
    model = os.environ.get(
        "ANTHROPIC_VOICE_CHAT_MODEL",
        os.environ.get("ANTHROPIC_VOICE_SUMMARY_MODEL", "claude-3-5-haiku-20241022"),
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
                "system": system,
                "messages": messages,
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


def _openai_chat(system: str, messages: list[dict]) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise ValueError("no openai key")
    model = os.environ.get(
        "OPENAI_VOICE_CHAT_MODEL",
        os.environ.get("OPENAI_VOICE_SUMMARY_MODEL", "gpt-4o-mini"),
    )
    base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/chat/completions"
    full = [{"role": "system", "content": system}] + messages
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "messages": full,
                "temperature": 0.6,
            },
        )
        r.raise_for_status()
        data = r.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return str(msg.get("content") or "").strip()


def _ollama_chat(system: str, messages: list[dict]) -> str:
    host = os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA).rstrip("/")
    model = os.environ.get(
        "DAVE_VOICE_CHAT_MODEL",
        os.environ.get("DAVE_VOICE_SUMMARY_MODEL", "llama3.2"),
    )
    full = [{"role": "system", "content": system}] + messages
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            f"{host}/api/chat",
            json={
                "model": model,
                "messages": full,
                "stream": False,
            },
        )
        r.raise_for_status()
        data = r.json()
    msg = data.get("message") or {}
    return str(msg.get("content") or "").strip()


def _provider_order_chat() -> list[tuple[str, Callable[[str, list[dict]], str]]]:
    mode = (os.environ.get("DAVE_VOICE_SUMMARY_PROVIDER") or "auto").strip().lower()
    has_a = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    has_o = bool(os.environ.get("OPENAI_API_KEY", "").strip())

    anth = ("anthropic", _anthropic_chat)
    oai = ("openai", _openai_chat)
    oll = ("ollama", _ollama_chat)

    if mode == "anthropic":
        return [anth, oai, oll]
    if mode == "openai":
        return [oai, anth, oll]
    if mode == "ollama":
        return [oll, anth, oai]
    if has_a and has_o:
        return [anth, oai, oll]
    if has_a:
        return [anth, oll, oai]
    if has_o:
        return [oai, oll, anth]
    return [oll, anth, oai]


def _llm_chat_complete(system: str, messages: list[dict]) -> str:
    last_err: str | None = None
    for _name, fn in _provider_order_chat():
        try:
            out = fn(system, messages)
            if out:
                return out
        except Exception as e:
            last_err = str(e)
            continue
    raise RuntimeError(last_err or "all chat providers failed")


def chat_with_dave(
    history: list[dict],
    user_message: str,
    *,
    system_extra: str | None = None,
) -> tuple[list[dict], str]:
    """
    Append user message, call LLM, return (updated history including assistant, reply text).
    History items: {"role": "user"|"assistant", "content": str}.
    """
    system = (os.environ.get("DAVE_VOICE_CHAT_SYSTEM") or "").strip() or DEFAULT_SYSTEM_PROMPT
    if system_extra and system_extra.strip():
        system = f"{system}\n\n{system_extra.strip()}"
    new_h = list(history)
    new_h.append({"role": "user", "content": user_message.strip()})
    new_h = _trim_history(new_h, max_messages=12)
    reply = _llm_chat_complete(system, new_h)
    if not reply:
        raise RuntimeError("empty model reply")
    out_h = new_h + [{"role": "assistant", "content": reply}]
    return out_h, reply
