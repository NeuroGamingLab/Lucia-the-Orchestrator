#!/usr/bin/env python3
"""
Run a single task in a full OpenClaw container (sub-agent).
Invoked by MasterClaw as the entrypoint for the OpenClaw image.
Reads task from /tasks/<JOB_ID>/input.json, starts OpenClaw gateway, POSTs task
to /v1/chat/completions, writes result to /tasks/<JOB_ID>/output.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

TASKS_ROOT = Path(os.environ.get("TASKS_ROOT", "/tasks"))
GATEWAY_PORT = 18789
GATEWAY_URL = f"http://127.0.0.1:{GATEWAY_PORT}"
WAIT_FOR_PORT_SECONDS = 120
REQUEST_TIMEOUT = 600


def wait_for_gateway():
    """Wait for OpenClaw gateway to listen on GATEWAY_PORT."""
    for _ in range(WAIT_FOR_PORT_SECONDS):
        try:
            with httpx.Client(timeout=2.0) as client:
                r = client.get(f"{GATEWAY_URL}/")
            if r.status_code in (200, 404):
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("OpenClaw gateway did not become ready in time")


def main():
    job_id = os.environ.get("JOB_ID")
    if not job_id:
        sys.exit(1)

    job_dir = TASKS_ROOT / job_id
    input_path = job_dir / "input.json"
    output_path = job_dir / "output.json"

    if not input_path.exists():
        output_path.write_text(
            json.dumps({"status": "failed", "error": "input.json not found"})
        )
        sys.exit(1)

    try:
        payload = json.loads(input_path.read_text())
    except Exception as e:
        output_path.write_text(
            json.dumps({"status": "failed", "error": str(e)})
        )
        sys.exit(1)

    task = payload.get("task", "")
    context = payload.get("context", "")
    timeout_seconds = int(payload.get("timeout_seconds", 300))
    if not task:
        output_path.write_text(
            json.dumps({"status": "failed", "error": "task is required"})
        )
        sys.exit(1)

    user_content = task
    if context:
        user_content = f"Context:\n{context}\n\nTask:\n{task}"

    # Start OpenClaw gateway in background (same process tree so we can kill it)
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
    # OpenClaw image typically runs gateway as main process; try common entrypoints
    try:
        proc = subprocess.Popen(
            ["openclaw", "gateway"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={**os.environ, "OPENCLAW_HOST": "0.0.0.0"},
        )
    except FileNotFoundError:
        try:
            proc = subprocess.Popen(
                ["openclaw"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env={**os.environ, "OPENCLAW_HOST": "0.0.0.0"},
            )
        except FileNotFoundError:
            output_path.write_text(
                json.dumps({
                    "status": "failed",
                    "error": "openclaw binary not found in container",
                })
            )
            sys.exit(1)

    try:
        wait_for_gateway()
    except RuntimeError as e:
        proc.terminate()
        proc.wait(timeout=5)
        output_path.write_text(
            json.dumps({"status": "failed", "error": str(e)})
        )
        sys.exit(1)

    url = f"{GATEWAY_URL}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "x-openclaw-agent-id": "main",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {
        "model": "openclaw",
        "messages": [{"role": "user", "content": user_content}],
        "stream": False,
    }

    try:
        with httpx.Client(timeout=float(timeout_seconds)) as client:
            r = client.post(url, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        output_path.write_text(
            json.dumps({"status": "failed", "error": str(e)}, indent=2)
        )
        proc.terminate()
        proc.wait(timeout=10)
        sys.exit(1)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    choices = data.get("choices", [])
    text = choices[0].get("message", {}).get("content", "") if choices else ""
    output_path.write_text(
        json.dumps({
            "status": "completed",
            "result": {"output": text, "model": "openclaw"},
        }, indent=2)
    )


if __name__ == "__main__":
    main()
