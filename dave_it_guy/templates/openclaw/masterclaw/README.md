# MasterClaw — Orchestrator for sub-agent tasks

MasterClaw is a small API service that runs alongside the OpenClaw stack. It allows the main OpenClaw agent to delegate tasks to **worker containers** that use Ollama to complete a single task and return the result.

## Flow

1. OpenClaw (or any client) calls **POST /subagent** with `{ "task": "...", "context": "...", "model": "llama3.2" }`.
2. MasterClaw writes the task to a shared volume and starts a **worker container** (same image, different entrypoint) with Docker.
3. The worker reads the task, calls **Ollama** (`/api/generate`) to complete it, and writes the result to the shared volume.
4. The client polls **GET /subagent/{job_id}** until `status` is `completed` or `failed`, then reads `result` or `error`.

## Security

- Only the MasterClaw container has the Docker socket mounted; OpenClaw does not.
- Workers run with the same image, no arbitrary code from the task payload (task is passed as data, not executed as code).

## API

- **POST /subagent** — create a sub-agent job (body: `task`, `context?`, `model?`, `timeout_seconds?`). Returns `job_id`, `status: "running"`.
- **GET /subagent/{job_id}** — get status and result (`pending` | `running` | `completed` | `failed`).
- **GET /health** — liveness.

## OpenClaw workspace tool

From inside the OpenClaw container, the agent can run:

```bash
python3 /home/node/.openclaw/workspace/simple_subagent.py "Your task here" "Optional context" llama3.2
```

This script calls the MasterClaw API, polls for completion, and prints the result JSON. Requires `httpx` (installed via openclaw-pip-deps).
