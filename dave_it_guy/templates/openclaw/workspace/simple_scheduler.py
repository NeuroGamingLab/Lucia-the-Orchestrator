#!/usr/bin/env python3
"""
Simple scheduler for spawning one or more MasterClaw sub-agents on an interval.

Examples:
  # Every 5 minutes, run one lightweight worker and wait for result
  python3 simple_scheduler.py "Get today world news and summarize 5 bullets" --interval 300

  # Every 5 minutes, fan out 3 full OpenClaw sub-agents and wait for each result
  python3 simple_scheduler.py "Get today world news and summarize 5 bullets" \
    --interval 300 --agents-per-cycle 3 --full-openclaw

  # Fire-and-forget (do not poll results), run forever
  python3 simple_scheduler.py "Monitor sports headlines" --interval 300 --no-wait
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    import httpx
except ImportError:
    httpx = None

MASTERCLAW_URL = os.environ.get("MASTERCLAW_URL", "http://masterclaw:8090").rstrip("/")
OPENCLAW_GATEWAY_URL = os.environ.get("OPENCLAW_GATEWAY_URL", "http://openclaw:18789").rstrip("/")
POLL_INTERVAL = 2
MAX_WAIT_SECONDS = 900


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_log(path: str, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def create_job(
    task: str,
    context: str,
    model: str,
    timeout_seconds: int,
    use_full_openclaw: bool,
) -> str:
    payload = {
        "task": task,
        "context": context or None,
        "model": model,
        "timeout_seconds": timeout_seconds,
        "use_full_openclaw": use_full_openclaw,
        "interactive": False,
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(f"{MASTERCLAW_URL}/subagent", json=payload)
        r.raise_for_status()
        data = r.json()
    job_id = data.get("job_id")
    if not job_id:
        raise RuntimeError("No job_id in /subagent response")
    return job_id


def poll_job(job_id: str) -> dict:
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while time.monotonic() < deadline:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{MASTERCLAW_URL}/subagent/{job_id}")
            r.raise_for_status()
            data = r.json()
        status = data.get("status", "")
        if status in ("completed", "failed"):
            return data
        time.sleep(POLL_INTERVAL)
    return {
        "job_id": job_id,
        "status": "timeout",
        "error": f"Polling exceeded {MAX_WAIT_SECONDS}s",
    }


def _summarize_cycle_for_chat(cycle_record: dict) -> str:
    lines = [
        "Automated world news update",
        f"Cycle: {cycle_record.get('cycle')}",
        f"Timestamp (UTC): {cycle_record.get('ts')}",
        "",
    ]
    jobs = cycle_record.get("jobs", [])
    if not jobs:
        lines.append("- No jobs were run in this cycle.")
        return "\n".join(lines)
    for idx, job in enumerate(jobs, start=1):
        status = job.get("status", "unknown")
        lines.append(f"Job {idx} ({job.get('job_id', 'n/a')}): {status}")
        result = (job.get("result") or {}).get("output", "").strip()
        if result:
            lines.append(result)
        error = job.get("error")
        if error:
            lines.append(f"Error: {error}")
        lines.append("")
    return "\n".join(lines).strip()


def announce_to_tui(
    gateway_url: str,
    agent_id: str,
    announcement: str,
    timeout_seconds: int,
) -> dict:
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip()
    headers = {
        "Content-Type": "application/json",
        "x-openclaw-agent-id": agent_id,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    prompt = (
        "Post the following bulletin to chat exactly as-is. "
        "Do not add commentary.\n\n"
        f"{announcement}"
    )
    with httpx.Client(timeout=float(timeout_seconds)) as client:
        r = client.post(
            f"{gateway_url.rstrip('/')}/v1/chat/completions",
            headers=headers,
            json={
                "model": "openclaw",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
        )
        r.raise_for_status()
        data = r.json()
    text = (
        ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
    )
    return {"status": "posted", "output_preview": text[:400]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Schedule recurring MasterClaw sub-agent jobs.")
    parser.add_argument("task", help="Task text to send each cycle")
    parser.add_argument("--context", default="", help="Optional context sent with task")
    parser.add_argument("--model", default="llama3.2", help="Model hint (default: llama3.2)")
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Seconds between cycles (default: 300)",
    )
    parser.add_argument(
        "--agents-per-cycle",
        type=int,
        default=1,
        help="How many sub-agents to launch each cycle (default: 1)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="Number of cycles to run (0 = forever)",
    )
    parser.add_argument(
        "--full-openclaw",
        action="store_true",
        help="Use full OpenClaw sub-agent containers (default: lightweight workers)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Sub-agent timeout sent to MasterClaw (default: 300)",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Do not poll job completion; only submit jobs",
    )
    parser.add_argument(
        "--log-file",
        default="/home/node/.openclaw/workspace/scheduler.log",
        help="JSONL log output path",
    )
    parser.add_argument(
        "--announce-to-tui",
        action="store_true",
        help="Post each cycle summary to OpenClaw TUI chat via gateway",
    )
    parser.add_argument(
        "--announce-agent-id",
        default="main",
        help="OpenClaw agent thread id for announcements (default: main)",
    )
    parser.add_argument(
        "--gateway-url",
        default=OPENCLAW_GATEWAY_URL,
        help="OpenClaw gateway base URL (default from OPENCLAW_GATEWAY_URL env)",
    )
    parser.add_argument(
        "--gateway-timeout-seconds",
        type=int,
        default=60,
        help="Timeout for posting announcement to OpenClaw gateway",
    )
    args = parser.parse_args()

    if httpx is None:
        print(json.dumps({"status": "error", "error": "httpx not installed"}))
        sys.exit(1)

    interval = max(5, args.interval)
    agents_per_cycle = max(1, args.agents_per_cycle)
    cycle = 0

    print(
        json.dumps(
            {
                "status": "started",
                "masterclaw_url": MASTERCLAW_URL,
                "interval_seconds": interval,
                "agents_per_cycle": agents_per_cycle,
                "cycles": args.cycles,
                "full_openclaw": args.full_openclaw,
                "wait_for_results": not args.no_wait,
                "log_file": args.log_file,
                "announce_to_tui": args.announce_to_tui,
                "announce_agent_id": args.announce_agent_id,
                "gateway_url": args.gateway_url,
            },
            ensure_ascii=False,
        )
    )

    while True:
        cycle += 1
        cycle_started = now_iso()
        cycle_record = {
            "ts": cycle_started,
            "cycle": cycle,
            "status": "running",
            "jobs": [],
        }

        for i in range(agents_per_cycle):
            try:
                job_id = create_job(
                    task=args.task,
                    context=args.context,
                    model=args.model,
                    timeout_seconds=max(60, min(600, args.timeout_seconds)),
                    use_full_openclaw=args.full_openclaw,
                )
                job_record = {"job_id": job_id, "index": i + 1, "status": "submitted"}
                if not args.no_wait:
                    result = poll_job(job_id)
                    job_record["status"] = result.get("status", "unknown")
                    if result.get("result"):
                        job_record["result"] = result["result"]
                    if result.get("error"):
                        job_record["error"] = result["error"]
                cycle_record["jobs"].append(job_record)
            except Exception as e:
                cycle_record["jobs"].append(
                    {"index": i + 1, "status": "error", "error": str(e)}
                )

        cycle_record["status"] = "completed"
        if args.announce_to_tui:
            try:
                bulletin = _summarize_cycle_for_chat(cycle_record)
                cycle_record["announcement"] = announce_to_tui(
                    gateway_url=args.gateway_url,
                    agent_id=args.announce_agent_id,
                    announcement=bulletin,
                    timeout_seconds=max(10, args.gateway_timeout_seconds),
                )
            except Exception as e:
                cycle_record["announcement"] = {"status": "error", "error": str(e)}
        append_log(args.log_file, cycle_record)
        print(json.dumps(cycle_record, ensure_ascii=False))

        if args.cycles > 0 and cycle >= args.cycles:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
