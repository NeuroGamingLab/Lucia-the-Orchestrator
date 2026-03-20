# MasterClaw — Multi-Agent OpenClaw Ecosystem

This document describes the **MasterClaw** enhancement: an external orchestrator that runs sub-agent tasks either as lightweight Ollama workers or as **full OpenClaw containers**, plus a **TUI** that talks to MasterClaw so you can create and monitor tasks from the terminal.

The main OpenClaw agent can delegate work to MasterClaw via `simple_subagent.py`, enabling multi-agent workflows without giving OpenClaw direct Docker control.

---

## Architecture overview (ASCII)

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                    HOST (your machine)                                 │
│                                                                                         │
│  ┌──────────────────────┐                   ┌──────────────────────────────────────┐   │
│  │ MasterClaw TUI       │ HTTP (port 8090)  │ MasterClaw (container)               │   │
│  │ dave-it-guy          │ ─────────────────►│ • FastAPI: POST/GET /subagent         │   │
│  │ masterclaw-tui       │◄──────────────────│ • Docker socket (starts workers)      │   │
│  │ (Enhanced terminal UI)│  job_id, status  │ • Writes /tasks/<job_id>/*.json       │   │
│  └──────────────────────┘                   │ • Mounts deploy path read-only         │   │
│                                             │   (reads .env/config for sub-agents)   │   │
│                                             └───────────────┬──────────────────────┘   │
│                                                             │ docker run               │
│                                                             ▼                          │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐   │
│  │                 Docker network: openclaw_dave-it-guy                             │   │
│  │                                                                                   │   │
│  │  ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐  │   │
│  │  │ OpenClaw (main)      │    │ Ollama              │    │ Qdrant              │  │   │
│  │  │ dave-it-guy-openclaw │───►│ dave-it-guy-ollama  │    │ dave-it-guy-qdrant  │  │   │
│  │  │ :18789 gateway       │    │ :11434              │    │ :6333               │  │   │
│  │  │ workspace tools       │    │ LLM backend         │    │ vector memory       │  │   │
│  │  └──────────┬────────────┘    └─────────────────────┘    └─────────────────────┘  │   │
│  │             │ POST /subagent                                                     │   │
│  │             ▼                                                                     │   │
│  │  ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐  │   │
│  │  │ Lightweight worker  │    │ Full OpenClaw       │    │ Shared volumes       │  │   │
│  │  │ (worker.py)         │    │ sub-agent           │    │ • openclaw_data      │  │   │
│  │  │ one Ollama call     │    │ gateway :18789      │    │ • subagent_tasks     │  │   │
│  │  │ auto-removed        │    │ same config/workspace│   │ • qdrant_data        │  │   │
│  │  │ after completion    │    │ initial task posted  │    │ • ollama_models      │  │   │
│  │  │                     │    │ cleanup mode:        │    │                      │  │   │
│  │  │                     │    │ - default: remove    │    │                      │  │   │
│  │  │                     │    │ - Option B: keep-alive│   │                      │  │   │
│  │  └─────────────────────┘    └─────────────────────┘    └─────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

**Flow summary**

| Actor | Action |
|------|--------|
| **You** | Run `dave-it-guy masterclaw-tui` or use OpenClaw TUI/tooling. |
| **TUI** | POSTs to MasterClaw (lightweight or full OpenClaw), polls GET `/subagent/{id}`. |
| **OpenClaw** | Can call `simple_subagent.py ... --full-openclaw` to the same API. |
| **MasterClaw** | Writes `subagent_tasks/{job_id}/input.json`; starts worker or full OpenClaw sub-agent. |
| **Lightweight** | Runs one Ollama generation, writes `output.json`, container disappears. |
| **Full OpenClaw** | Starts gateway, posts initial task to `/v1/chat/completions`, writes `output.json`; then either auto-cleans up or keeps container running (Option B). |
| **Ollama / Qdrant** | Shared by main OpenClaw and all sub-agents. |

---

## Components

| Component | Role |
|-----------|------|
| **MasterClaw** | FastAPI service in container; only service with Docker socket; orchestrates workers/sub-agents. |
| **MasterClaw TUI** | Host terminal UI for creating tasks and checking status/results. |
| **OpenClaw (main)** | Primary agent (`:18789`), workspace tools, can delegate via `simple_subagent.py`. |
| **Lightweight worker** | Short-lived container using `worker.py`; one Ollama request per task. |
| **Full OpenClaw sub-agent** | Full OpenClaw container per task; can be auto-cleaned (default) or kept running (Option B). |
| **Ollama** | Shared LLM backend. |
| **Qdrant** | Shared vector memory/search backend. |

---

## Quick start

1. **Deploy the stack** (includes MasterClaw):

   ```bash
   dave-it-guy deploy openclaw
   ```

2. **Open MasterClaw TUI**:

   ```bash
   dave-it-guy masterclaw-tui
   # or: dave-it-guy masterclaw-tui --url http://localhost:8090
   ```

3. In TUI:
   - **1** — Create task (lightweight worker).
   - **2** — Create task (full OpenClaw), then choose:
     - `n` = default auto-cleanup behavior.
     - `y` = **Option B keep-alive** (container remains running for attach).
   - **3** — Get job status by ID.
   - **4** — List recent job IDs.
   - **5** — Exit.

4. If you choose **Option B** and keep container running:

   ```bash
   docker exec -it openclaw-subagent-<job_id> openclaw tui
   ```

   Cleanup later:

   ```bash
   docker rm -f openclaw-subagent-<job_id>
   ```

5. **From inside OpenClaw**:

   ```bash
   python3 /home/node/.openclaw/workspace/simple_subagent.py "Summarize X" "Context..." llama3.2
   python3 /home/node/.openclaw/workspace/simple_subagent.py "Research Y" "" "" --full-openclaw
   ```

---

## API (MasterClaw)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/subagent` | Create job. Body: `task`, `context?`, `model?`, `timeout_seconds?`, `use_full_openclaw?` (default false), `interactive?` (default false). |
| GET | `/subagent/{job_id}` | Job status/result (`pending` \| `running` \| `completed` \| `failed`). |
| GET | `/subagent` | List recent job IDs. |
| GET | `/health` | Liveness probe. |

---

## Security

- **Docker socket** is mounted only in MasterClaw; OpenClaw itself has no Docker socket.
- **Task payload** is plain task/context text; not executed as host shell code.
- **Secrets** stay in deployment `.env`; MasterClaw reads needed values (for example gateway token) for sub-agent calls.
- **Deploy path mount** is read-only in MasterClaw to access `.env` and OpenClaw config/workspace context.

---

## Scaling: how many sub-agents?

There is no hard-coded count limit in MasterClaw. Each job gets a unique container name: `openclaw-subagent-<job_id>`.

Practical limits are resource-based:

- Host CPU/RAM.
- Docker daemon/container scheduling overhead.
- Shared Ollama/Qdrant throughput under concurrency.

To enforce a cap, add admission logic in MasterClaw (for example reject when running full sub-agents >= N).

---

## Lightweight workers vs OpenClaw sub-agents

| Aspect | Lightweight workers | OpenClaw sub-agents |
|--------|----------------------|---------------------|
| **Image** | `dave-it-guy-masterclaw` | `ghcr.io/openclaw/openclaw` |
| **What runs** | `worker.py` + one Ollama `/api/generate` call | Full OpenClaw gateway + agent flow |
| **Capabilities** | Single completion, no OpenClaw tools/memory loop | Tools, memory, multi-step behavior |
| **Input handling** | Task/context converted to one prompt | Task/context posted to `/v1/chat/completions` |
| **Output** | Single model response | OpenClaw final response |
| **Lifecycle** | Always short-lived and removed | Default short-lived; **Option B** keep container alive |
| **Best use** | Fast/simple one-shot tasks | Complex or interactive sub-agent tasks |

**In short:** lightweight worker = one Ollama completion. Full sub-agent = full OpenClaw behavior per job.

---

## When is Ollama used?

- **Main OpenClaw + full OpenClaw sub-agents**: OpenClaw model config uses primary provider plus fallbacks; Ollama is typically fallback (unless configured otherwise).
- **Lightweight worker path**: always Ollama-only; if Ollama is unavailable, those jobs fail.

---

## Files and locations

| What | Where |
|------|-------|
| MasterClaw app + worker | `dave_it_guy/templates/openclaw/masterclaw/` |
| MasterClaw TUI | `dave_it_guy/masterclaw_tui.py` |
| OpenClaw workspace tool | `dave_it_guy/templates/openclaw/workspace/simple_subagent.py` |
| Optional full-task helper script | `dave_it_guy/templates/openclaw/scripts/run_openclaw_task.py` |
| Compose template | `dave_it_guy/templates/openclaw/docker-compose.yml.j2` |

This README and the ASCII diagram describe the current MasterClaw ecosystem, including Option B keep-alive interactive full sub-agents.
