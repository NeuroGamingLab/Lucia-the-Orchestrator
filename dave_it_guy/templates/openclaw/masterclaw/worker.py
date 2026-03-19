"""
MasterClaw worker — runs inside a container, reads task from shared volume,
calls Ollama to complete the task, writes result to output.json.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
TASKS_ROOT = Path(os.environ.get("TASKS_ROOT", "/tasks"))


def run_ollama_completion(prompt: str, model: str, timeout: int) -> str:
    """Call Ollama generate API and return the response text."""
    url = f"{OLLAMA_HOST.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    with httpx.Client(timeout=float(timeout)) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    return data.get("response", "").strip()


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
    model = payload.get("model", "llama3.2")
    timeout_seconds = int(payload.get("timeout_seconds", 300))

    if not task:
        output_path.write_text(
            json.dumps({"status": "failed", "error": "task is required"})
        )
        sys.exit(1)

    prompt = task
    if context:
        prompt = f"Context:\n{context}\n\nTask:\n{task}"

    try:
        response_text = run_ollama_completion(prompt, model, timeout_seconds)
        output_path.write_text(
            json.dumps({
                "status": "completed",
                "result": {"output": response_text, "model": model},
            }, indent=2)
        )
    except Exception as e:
        output_path.write_text(
            json.dumps({"status": "failed", "error": str(e)}, indent=2)
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
