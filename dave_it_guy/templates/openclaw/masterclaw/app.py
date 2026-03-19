"""
MasterClaw — Orchestrator API for spinning off sub-agent containers.
Supports lightweight (Ollama-only) workers and full OpenClaw containers.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Docker SDK only used when running as API server (not in worker container)
try:
    import docker
except ImportError:
    docker = None

app = FastAPI(
    title="MasterClaw",
    description="Orchestrator for sub-agent OpenClaw tasks",
    version="0.2.0",
)

TASKS_ROOT = Path(os.environ.get("MASTERCLAW_TASKS_ROOT", "/tasks"))
SUPPORTED_MODELS = ("llama3.2", "llama3.1", "mistral", "qwen2.5")


class SubagentRequest(BaseModel):
    task: str
    context: str | None = None
    model: str = "llama3.2"
    timeout_seconds: int = 300
    use_full_openclaw: bool = False


class SubagentResponse(BaseModel):
    job_id: str
    status: str  # pending, running, completed, failed
    result: dict | None = None
    error: str | None = None


def _get_docker_client():
    if docker is None:
        raise HTTPException(status_code=503, detail="Docker SDK not installed")
    try:
        return docker.from_env()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Docker unavailable: {e}")


def _worker_image() -> str:
    return os.environ.get("MASTERCLAW_WORKER_IMAGE", "masterclaw-worker:latest")


def _network_name() -> str:
    return os.environ.get("MASTERCLAW_NETWORK", "dave-it-guy")


def _openclaw_image() -> str:
    return os.environ.get("MASTERCLAW_OPENCLAW_IMAGE", "ghcr.io/openclaw/openclaw:latest")


def _deploy_path() -> str | None:
    return os.environ.get("MASTERCLAW_DEPLOY_PATH")


def _openclaw_volume_name() -> str:
    return os.environ.get("MASTERCLAW_OPENCLAW_VOLUME", "openclaw_openclaw_data")


def _tasks_volume_name() -> str | None:
    """
    When spawning containers from inside Docker (via docker.sock), we cannot
    bind-mount paths that only exist inside the MasterClaw container.

    Instead, we mount the *named Docker volume* that is already attached to
    MasterClaw (subagent_tasks), so Docker Desktop accepts it on macOS.
    """
    return os.environ.get("MASTERCLAW_TASKS_VOLUME")


def _run_full_openclaw_job(job_id: str) -> None:
    """Background: wait for sub-OpenClaw gateway, POST task, write output, stop container."""
    job_dir = TASKS_ROOT / job_id
    output_path = job_dir / "output.json"
    container_name = f"openclaw-subagent-{job_id}"
    deploy_path = _deploy_path()
    if not deploy_path or not Path(deploy_path).is_dir():
        output_path.write_text(
            json.dumps({"status": "failed", "error": "MASTERCLAW_DEPLOY_PATH not set or invalid"})
        )
        return

    try:
        payload = json.loads((job_dir / "input.json").read_text())
    except Exception as e:
        output_path.write_text(json.dumps({"status": "failed", "error": str(e)}))
        return

    task = payload.get("task", "")
    context = payload.get("context", "")
    timeout_seconds = int(payload.get("timeout_seconds", 300))
    user_content = task
    if context:
        user_content = f"Context:\n{context}\n\nTask:\n{task}"

    client = docker.from_env()
    container = None
    try:
        container = client.containers.get(container_name)
    except docker.errors.NotFound:
        output_path.write_text(
            json.dumps({"status": "failed", "error": "Sub-OpenClaw container not found"})
        )
        return

    gateway_url = f"http://{container_name}:18789"
    for _ in range(min(120, timeout_seconds)):
        try:
            r = httpx.get(f"{gateway_url}/", timeout=2.0)
            if r.status_code in (200, 404):
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        _write_and_cleanup(container, output_path, "OpenClaw gateway did not become ready")
        return

    env_file = Path(deploy_path) / ".env"
    headers = {"Content-Type": "application/json", "x-openclaw-agent-id": "main"}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == "OPENCLAW_GATEWAY_TOKEN" and v.strip():
                    headers["Authorization"] = f"Bearer {v.strip()}"
                    break

    try:
        with httpx.Client(timeout=float(timeout_seconds)) as http:
            r = http.post(
                f"{gateway_url}/v1/chat/completions",
                headers=headers,
                json={
                    "model": "openclaw",
                    "messages": [{"role": "user", "content": user_content}],
                    "stream": False,
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        _write_and_cleanup(container, output_path, str(e))
        return

    choices = data.get("choices", [])
    text = choices[0].get("message", {}).get("content", "") if choices else ""
    output_path.write_text(
        json.dumps({"status": "completed", "result": {"output": text, "model": "openclaw"}}, indent=2)
    )
    try:
        container.stop(timeout=10)
        container.remove()
    except Exception:
        pass


def _write_and_cleanup(container, output_path: Path, error: str) -> None:
    output_path.write_text(json.dumps({"status": "failed", "error": error}))
    try:
        container.stop(timeout=10)
        container.remove()
    except Exception:
        pass


@app.post("/subagent", response_model=SubagentResponse)
def create_subagent(req: SubagentRequest):
    """Create a new sub-agent job. Use lightweight (Ollama) worker or full OpenClaw container."""
    job_id = str(uuid.uuid4())
    job_dir = TASKS_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    input_path = job_dir / "input.json"
    payload = {
        "task": req.task,
        "context": req.context or "",
        "model": req.model if req.model in SUPPORTED_MODELS else "llama3.2",
        "timeout_seconds": max(60, min(600, req.timeout_seconds)),
    }
    input_path.write_text(json.dumps(payload, indent=2))

    client = _get_docker_client()
    deploy_path = _deploy_path()
    network = _network_name()

    if req.use_full_openclaw and deploy_path and Path(deploy_path).is_dir():
        # Full OpenClaw: start container (default CMD = gateway), then background thread POSTs task
        container_name = f"openclaw-subagent-{job_id}"
        openclaw_volume = _openclaw_volume_name()
        deploy = Path(deploy_path)
        env_file_path = deploy / ".env"
        env_vars = {}
        if env_file_path.exists():
            for line in env_file_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env_vars[k.strip()] = v.strip()
        env_vars.setdefault("OPENCLAW_HOST", "0.0.0.0")
        env_vars.setdefault("OPENCLAW_CONFIG_PATH", "/app/config/openclaw.json")
        env_vars.setdefault("OLLAMA_HOST", os.environ.get("OLLAMA_HOST", "http://ollama:11434"))
        env_vars.setdefault("QDRANT_URL", os.environ.get("QDRANT_URL", "http://qdrant:6333/"))
        env_vars.setdefault("QDRANT_FALLBACK_URL", os.environ.get("QDRANT_FALLBACK_URL", "http://qdrant:6333/"))

        vol_map = {
            (
                _tasks_volume_name()
                or str(TASKS_ROOT)
            ): {"bind": "/tasks", "mode": "rw"},
            str(deploy / "config"): {"bind": "/app/config", "mode": "ro"},
            str(deploy / "workspace"): {"bind": "/home/node/.openclaw/workspace", "mode": "ro"},
        }
        try:
            client.volumes.get(openclaw_volume)
            vol_map[openclaw_volume] = {"bind": "/home/node/.openclaw", "mode": "rw"}
        except docker.errors.NotFound:
            pass
        try:
            client.containers.run(
                _openclaw_image(),
                name=container_name,
                detach=True,
                remove=False,
                network=network,
                volumes=vol_map,
                environment=env_vars,
            )
            threading.Thread(target=_run_full_openclaw_job, args=(job_id,), daemon=True).start()
        except Exception as e:
            (job_dir / "output.json").write_text(
                json.dumps({"status": "failed", "error": str(e)})
            )
            return SubagentResponse(job_id=job_id, status="failed", error=str(e))
        return SubagentResponse(job_id=job_id, status="running", result=None)
    else:
        # Lightweight worker (Ollama only)
        if req.use_full_openclaw and not deploy_path:
            (job_dir / "output.json").write_text(
                json.dumps({"status": "failed", "error": "Full OpenClaw requested but MASTERCLAW_DEPLOY_PATH not set"})
            )
            return SubagentResponse(job_id=job_id, status="failed", error="MASTERCLAW_DEPLOY_PATH not set")
        ollama_host = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
        try:
            client.containers.run(
                _worker_image(),
                detach=True,
                remove=True,
                network=network,
                volumes={
                    (_tasks_volume_name() or str(TASKS_ROOT)): {"bind": "/tasks", "mode": "rw"}
                },
                environment={
                    "JOB_ID": job_id,
                    "TASKS_ROOT": "/tasks",
                    "OLLAMA_HOST": ollama_host,
                },
                entrypoint=["python3", "/app/worker.py"],
            )
        except Exception as e:
            (job_dir / "output.json").write_text(
                json.dumps({"status": "failed", "error": str(e)})
            )
            return SubagentResponse(job_id=job_id, status="failed", error=str(e))
        return SubagentResponse(job_id=job_id, status="running", result=None)


@app.get("/subagent/{job_id}", response_model=SubagentResponse)
def get_subagent_status(job_id: str):
    """Get status and result of a sub-agent job."""
    job_dir = TASKS_ROOT / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Job not found")

    output_path = job_dir / "output.json"
    if not output_path.exists():
        return SubagentResponse(job_id=job_id, status="pending", result=None)

    try:
        data = json.loads(output_path.read_text())
    except Exception:
        return SubagentResponse(
            job_id=job_id,
            status="failed",
            error="Invalid output.json",
        )

    status = data.get("status", "unknown")
    return SubagentResponse(
        job_id=job_id,
        status=status,
        result=data.get("result"),
        error=data.get("error"),
    )


@app.get("/subagent")
def list_subagent_jobs():
    """List recent sub-agent job IDs (from task directories)."""
    if not TASKS_ROOT.is_dir():
        return {"job_ids": []}
    job_ids = [d.name for d in TASKS_ROOT.iterdir() if d.is_dir() and (d / "input.json").exists()]
    job_ids.sort(reverse=True)
    return {"job_ids": job_ids[:50]}


@app.get("/health")
def health():
    return {"status": "ok", "service": "masterclaw"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)
