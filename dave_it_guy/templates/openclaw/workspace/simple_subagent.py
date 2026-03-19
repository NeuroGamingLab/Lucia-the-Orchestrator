#!/usr/bin/env python3
"""
Request a sub-agent task via MasterClaw orchestrator.
MasterClaw spins off a worker container that uses Ollama to complete the task.

Usage (from OpenClaw workspace):
  python3 simple_subagent.py "Your task here"
  python3 simple_subagent.py "Summarize this" "Context: ..."
  python3 simple_subagent.py "Explain X" "" llama3.1

Outputs JSON to stdout:
  {"job_id": "...", "status": "completed", "result": {"output": "...", "model": "..."}}
  or {"status": "failed", "error": "..."}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

try:
    import httpx
except ImportError:
    httpx = None

MASTERCLAW_URL = os.environ.get("MASTERCLAW_URL", "http://masterclaw:8090")
POLL_INTERVAL = 2
MAX_WAIT_SECONDS = 600


def main():
    parser = argparse.ArgumentParser(
        description="Run a sub-agent task via MasterClaw (Ollama worker container)"
    )
    parser.add_argument("task", help="Task for the sub-agent to perform")
    parser.add_argument(
        "context",
        nargs="?",
        default="",
        help="Optional context (e.g. previous conversation or document)",
    )
    parser.add_argument(
        "model",
        nargs="?",
        default="llama3.2",
        help="Ollama model (default: llama3.2)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Max seconds for the sub-agent to run (default 300)",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Return immediately with job_id; do not poll for result",
    )
    parser.add_argument(
        "--full-openclaw",
        action="store_true",
        help="Use a full OpenClaw container (tools, memory) instead of lightweight Ollama worker",
    )
    args = parser.parse_args()

    if httpx is None:
        print(json.dumps({"status": "error", "error": "httpx not installed"}))
        sys.exit(1)

    base = MASTERCLAW_URL.rstrip("/")
    payload = {
        "task": args.task,
        "context": args.context or None,
        "model": args.model,
        "timeout_seconds": args.timeout,
        "use_full_openclaw": args.full_openclaw,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(f"{base}/subagent", json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        sys.exit(1)

    job_id = data.get("job_id")
    if not job_id:
        print(json.dumps({"status": "error", "error": "No job_id in response"}))
        sys.exit(1)

    if args.no_wait:
        print(json.dumps({"job_id": job_id, "status": "running"}))
        return

    # Poll for completion
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{base}/subagent/{job_id}")
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            print(json.dumps({"job_id": job_id, "status": "error", "error": str(e)}))
            sys.exit(1)

        status = data.get("status", "")
        if status == "completed":
            print(json.dumps(data))
            return
        if status == "failed":
            print(json.dumps({"job_id": job_id, "status": "failed", "error": data.get("error", "unknown")}))
            sys.exit(1)

        time.sleep(POLL_INTERVAL)

    print(json.dumps({"job_id": job_id, "status": "timeout", "error": "Polling exceeded max wait"}))
    sys.exit(1)


if __name__ == "__main__":
    main()
