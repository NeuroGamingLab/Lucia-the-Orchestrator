#!/usr/bin/env python3
"""
Simple Qdrant READ/WRITE script for OpenClaw.
Copied to /home/node/.openclaw/workspace by Dave IT Guy deploy.

Commands:
  list                    – list collections
  upsert <coll> <text> [id]  – write a text into a collection (id optional, UUID if omitted)
  search <coll> <query> [limit]  – semantic search in a collection (limit default 10)

Uses QDRANT_URL (primary) and QDRANT_FALLBACK_URL from env. Tries primary first, then fallback if primary is unreachable. Embeddings: sentence-transformers (all-MiniLM-L6-v2).
Dave IT Guy deploy installs qdrant-client and sentence-transformers in the container; if missing, run:
  pip install qdrant-client sentence-transformers
Outputs JSON to stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from typing import Optional

# Security: restrict collection names to avoid injection / path issues
COLLECTION_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")
MAX_TEXT_LENGTH = 1_000_000  # 1M chars to avoid DoS from huge payloads
SEARCH_LIMIT_MAX = 100

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams
except ImportError:
    QdrantClient = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

QDRANT_PRIMARY_URL = os.environ.get("QDRANT_URL", "http://16.52.188.82:6333/")
QDRANT_FALLBACK_URL = os.environ.get("QDRANT_FALLBACK_URL", "http://localhost:6333/")
EMBED_MODEL = "all-MiniLM-L6-v2"
VECTOR_SIZE = 384

_model = None


def _get_client():
    if QdrantClient is None:
        raise RuntimeError(
            "qdrant_client not installed; run 'pip install qdrant-client'"
        )
    for url in (QDRANT_PRIMARY_URL, QDRANT_FALLBACK_URL):
        try:
            client = QdrantClient(url=url.rstrip("/") or url)
            client.get_collections()  # verify connection
            return client
        except Exception:
            continue
    raise RuntimeError(
        f"Qdrant unreachable at primary {QDRANT_PRIMARY_URL!r} and fallback {QDRANT_FALLBACK_URL!r}"
    )


def _get_embedder():
    global _model
    if SentenceTransformer is None:
        raise RuntimeError(
            "sentence_transformers not installed; run 'pip install sentence-transformers'"
        )
    if _model is None:
        _model = SentenceTransformer(EMBED_MODEL)
    return _model


def _ensure_collection(client, collection: str):
    try:
        client.get_collection(collection)
    except Exception:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def _validate_collection(name: str) -> None:
    if not name or not COLLECTION_NAME_RE.match(name):
        raise ValueError(
            "Collection name must be 1–128 chars, alphanumeric, hyphen, underscore only"
        )


def _point_id(raw: Optional[str]) -> str:
    """Return a Qdrant-valid point ID (UUID string only; integers like 1 are rejected by some setups)."""
    if not raw:
        return str(uuid.uuid4())
    raw = raw.strip()
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError):
        return str(uuid.uuid4())


def cmd_list():
    client = _get_client()
    cols = client.get_collections().collections
    names = [c.name for c in cols]
    return {"command": "list", "collections": names, "count": len(names)}


def cmd_upsert(collection: str, text: str, point_id: Optional[str] = None):
    _validate_collection(collection)
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"Text length exceeds maximum ({MAX_TEXT_LENGTH} chars)")
    client = _get_client()
    _ensure_collection(client, collection)
    embedder = _get_embedder()
    vector = embedder.encode(text, normalize_embeddings=True).tolist()
    pid = _point_id(point_id)
    client.upsert(
        collection_name=collection,
        points=[PointStruct(id=pid, vector=vector, payload={"text": text})],
    )
    return {"command": "upsert", "collection": collection, "id": pid, "status": "ok"}


def cmd_search(collection: str, query: str, limit: int = 10):
    _validate_collection(collection)
    limit = max(1, min(SEARCH_LIMIT_MAX, limit))
    client = _get_client()
    embedder = _get_embedder()
    qvec = embedder.encode(query, normalize_embeddings=True).tolist()
    hits = client.search(
        collection_name=collection,
        query_vector=qvec,
        limit=limit,
    )
    results = [
        {
            "id": str(h.id),
            "score": float(h.score),
            "payload": dict(h.payload) if h.payload else {},
        }
        for h in hits
    ]
    return {"command": "search", "collection": collection, "results": results}


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: simple_qdrant.py list | upsert <coll> <text> [id] | search <coll> <query> [limit]",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd = sys.argv[1].lower()
    out = None

    if cmd == "list":
        if len(sys.argv) != 2:
            print("Usage: simple_qdrant.py list", file=sys.stderr)
            sys.exit(1)
        out = cmd_list()
    elif cmd == "upsert":
        if len(sys.argv) < 4:
            print(
                "Usage: simple_qdrant.py upsert <collection> <text> [id]",
                file=sys.stderr,
            )
            sys.exit(1)
        coll, text = sys.argv[2], sys.argv[3]
        point_id = sys.argv[4] if len(sys.argv) > 4 else None
        out = cmd_upsert(coll, text, point_id)
    elif cmd == "search":
        if len(sys.argv) < 4:
            print(
                "Usage: simple_qdrant.py search <collection> <query> [limit]",
                file=sys.stderr,
            )
            sys.exit(1)
        coll, query = sys.argv[2], sys.argv[3]
        try:
            limit = int(sys.argv[4]) if len(sys.argv) > 4 else 10
        except ValueError:
            limit = 10
        out = cmd_search(coll, query, limit)
    else:
        print(f"Unknown command: {cmd}. Use list, upsert, or search.", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
