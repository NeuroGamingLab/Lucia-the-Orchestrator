"""Stack template registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Template registry — each entry describes an available stack
TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "openclaw",
        "description": "Full AI assistant — OpenClaw + Ollama + Qdrant",
        "services": ["openclaw", "ollama", "qdrant"],
        "tier": "free",
        "default_port": 18789,
    },
    {
        "name": "ollama",
        "description": "Standalone Ollama with Open WebUI",
        "services": ["ollama", "open-webui"],
        "tier": "free",
        "default_port": 3000,
    },
    {
        "name": "rag",
        "description": "RAG pipeline — Qdrant + embeddings + API",
        "services": ["qdrant", "embeddings", "rag-api"],
        "tier": "free",
        "default_port": 8080,
    },
]


def list_templates() -> list[dict[str, Any]]:
    """Return all available stack templates."""
    return TEMPLATES


def get_template(name: str) -> dict[str, Any] | None:
    """Look up a template by name."""
    for t in TEMPLATES:
        if t["name"] == name:
            return t
    return None


def get_template_dir(name: str) -> Path:
    """Get the filesystem path to a template's directory."""
    return Path(__file__).parent / name
