#!/usr/bin/env python3
"""
Simple DuckDuckGo-based web search script for OpenClaw.

Usage:
  python simple_search.py "your query" [max_results]

Outputs JSON to stdout:
  {
    "engine": "duckduckgo",
    "results": [
      {"title": "...", "url": "...", "snippet": "..."},
      ...
    ]
  }
"""

import json
import sys

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None


def _norm(r: dict, url_key: str = "href") -> dict:
    """Normalize a result dict to {title, url, snippet}."""
    return {
        "title": r.get("title") or r.get("name") or "",
        "url": r.get(url_key) or r.get("url") or "",
        "snippet": r.get("body") or r.get("snippet") or "",
    }


def run_search(query: str, max_results: int = 10):
    if DDGS is None:
        raise RuntimeError(
            "duckduckgo_search not installed; run 'pip install duckduckgo-search'"
        )

    source = "text"
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append(_norm(r))
    if not results:
        try:
            with DDGS() as ddgs_news:
                for r in ddgs_news.news(query, max_results=max_results):
                    results.append(_norm(r))
                source = "news"
        except Exception:
            pass
    return {
        "engine": "duckduckgo",
        "source": source,
        "results": results,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: simple_search.py \"query\" [max_results]", file=sys.stderr)
        sys.exit(1)

    query = sys.argv[1]
    try:
        max_results = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    except ValueError:
        max_results = 10
    max_results = max(1, min(50, max_results))

    data = run_search(query, max_results)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
