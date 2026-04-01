"""
Ollama LLaVA vision (``/api/chat`` with ``images``) for webcam frames.

Requires a running Ollama server and a pulled vision model, e.g. ``ollama pull llava``.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import cv2
import httpx

_DEFAULT_MAX_DIM = 960


def frame_bgr_to_jpeg_b64(frame, *, quality: int = 82) -> str:
    """Encode BGR OpenCV frame to base64 JPEG string."""
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def downscale_frame_max_dim(frame, max_dim: int = _DEFAULT_MAX_DIM):
    """Resize so longest edge is at most *max_dim* (keeps aspect ratio)."""
    h, w = frame.shape[:2]
    m = max(h, w)
    if m <= max_dim:
        return frame
    scale = max_dim / float(m)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    return cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)


def ollama_base_url() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def ollama_vision_chat(
    *,
    prompt: str,
    image_b64: str,
    base_url: str | None = None,
    model: str | None = None,
    timeout_sec: float = 180.0,
) -> str:
    """
    Non-streaming ``POST /api/chat`` with one user message containing ``images``.
    See https://github.com/ollama/ollama/blob/main/docs/api.md
    """
    url = (base_url or ollama_base_url()) + "/api/chat"
    m = model or os.environ.get("DAVE_HAND_LLAVA_MODEL", "llava")
    payload: dict[str, Any] = {
        "model": m,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image_b64],
            },
        ],
        "stream": False,
    }
    with httpx.Client(timeout=timeout_sec) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    msg = data.get("message") or {}
    text = (msg.get("content") or "").strip()
    if not text and isinstance(data.get("response"), str):
        text = data["response"].strip()
    return text
