# MasterClaw — Multi-Agent OpenClaw Ecosystem

This document describes the **MasterClaw** enhancement: an external orchestrator that lets you run sub-agent tasks either as lightweight Ollama workers or as **full OpenClaw containers**, plus a **TUI** that talks to MasterClaw so you can create and monitor tasks from the terminal. The main OpenClaw agent can also delegate work to MasterClaw via a workspace tool (`simple_subagent.py`), enabling a multi-agent workflow without giving OpenClaw direct access to Docker.

---

## Architecture overview (ASCII)

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                    HOST (your machine)                                    │
│                                                                                           │
│  ┌──────────────────────┐                    ┌──────────────────────────────────────┐   │
│  │  MasterClaw TUI       │  HTTP (port 8090)  │  MasterClaw (container)              │   │
│  │  dave-it-guy         │ ─────────────────► │  • FastAPI: POST/GET /subagent         │   │
│  │  masterclaw-tui      │ ◄───────────────── │  • Docker socket (starts workers)     │   │
│  │  (Rich terminal UI)  │   job_id, result    │  • Writes task → volume; runs        │   │
│  └──────────────────────┘                    │    containers; POSTs to sub-OpenClaw   │   │
│           │                                  └───────────────┬──────────────────────┘   │
│           │                                                   │                          │
│           │  same API                                        │  docker run               │
│           ▼                                                   ▼                          │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐   │
│  │                    Docker network: openclaw_dave-it-guy                          │   │
│  │                                                                                   │   │
│  │  ┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐ │   │
│  │  │  OpenClaw (main)     │     │  Ollama             │     │  Qdrant              │ │   │
│  │  │  dave-it-guy-openclaw│     │  dave-it-guy-ollama │     │  dave-it-guy-qdrant  │ │   │
│  │  │  :18789 (gateway)    │────►│  :11434             │     │  :6333               │ │   │
│  │  │  TUI / Control UI    │     │  (LLM API)          │     │  (vector store)      │ │   │
│  │  │  workspace tools     │     └─────────────────────┘     └─────────────────────┘ │   │
│  │  │  e.g. simple_        │              ▲                              ▲            │   │
│  │  │  subagent.py         │              │                              │            │   │
│  │  └──────────┬───────────┘              │                              │            │   │
│  │             │ POST /subagent           │                              │            │   │
│  │             │ (optional --full-openclaw)                               │            │   │
│  │             ▼                          │                              │            │   │
│  │  ┌─────────────────────┐              │                              │            │   │
│  │  │  MasterClaw         │              │                              │            │   │
│  │  │  (see above)        │              │                              │            │   │
│  │  └──────────┬──────────┘              │                              │            │   │
│  │             │                          │                              │            │   │
│  │             │  starts (detach)        │                              │            │   │
│  │             ▼                          │                              │            │   │
│  │  ┌─────────────────────┐     ┌────────┴────────┐     ┌───────────────┴──────────┐ │   │
│  │  │  Lightweight worker │     │  Full OpenClaw  │     │  Shared volumes           │ │   │
│  │  │  (masterclaw image, │     │  sub-agent      │     │  • openclaw_data          │ │   │
│  │  │   entrypoint        │     │  (openclaw      │     │    (skills, site-packages)│ │   │
│  │  │   worker.py)        │     │   image)        │     │  • subagent_tasks         │ │   │
│  │  │  • reads /tasks/    │     │  • gateway      │     │    (input.json,            │ │   │
│  │  │    job_id/input.json│     │    :18789       │     │     output.json per job)  │ │   │
│  │  │  • Ollama generate  │     │  • same config  │     │  • qdrant_data            │ │   │
│  │  │  • writes output    │     │    workspace    │     │  • ollama_models          │ │   │
│  │  └──────────┬──────────┘     │  • MasterClaw  │     └───────────────────────────┘ │   │
│  │             │                │    POSTs task   │                                  │   │
│  │             │                │    then stops   │                                  │   │
│  │             │                └────────┬───────┘                                  │   │
│  │             │                          │                                           │   │
│  │             └──────────────────────────┴──► Ollama, Qdrant (shared by all)        │   │
│  └──────────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

**Flow summary**

| Actor            | Action |
|-----------------|--------|
| **You**         | Run `dave-it-guy masterclaw-tui` or use OpenClaw TUI. |
| **TUI**         | POSTs task to MasterClaw (lightweight or full OpenClaw), polls GET /subagent/{id}. |
| **OpenClaw**    | Can call `simple_subagent.py "task" … --full-openclaw` → same API. |
| **MasterClaw**  | Writes task to `subagent_tasks/{job_id}/input.json`; starts worker or full OpenClaw container. |
| **Lightweight** | Worker container runs Ollama once, writes result to `output.json`. |
| **Full OpenClaw** | MasterClaw starts OpenClaw container (gateway), waits for it, POSTs task to `/v1/chat/completions`, writes result, stops container. |
| **Ollama / Qdrant** | Shared by main OpenClaw and all sub-agents. |

---

## Components

| Component | Role |
|-----------|------|
| **MasterClaw** | API service (FastAPI) in a container; only one with Docker socket; starts and coordinates workers. |
| **MasterClaw TUI** | Terminal UI on the host; talks to MasterClaw over HTTP to create tasks and view results. |
| **OpenClaw (main)** | Primary agent (gateway :18789, TUI, workspace tools); can call MasterClaw via `simple_subagent.py`. |
| **Lightweight worker** | Short-lived container (same image as MasterClaw, `worker.py`); one Ollama call per task. |
| **Full OpenClaw sub-agent** | Short-lived OpenClaw container; full agent (tools, memory); MasterClaw POSTs one task and reads the reply. |
| **Ollama** | Shared LLM backend. |
| **Qdrant** | Shared vector store (memory, search). |

---

## Quick start

1. **Deploy the stack** (includes MasterClaw):

   ```bash
   dave-it-guy deploy openclaw
   ```

2. **Open the MasterClaw TUI** (from the host):

   ```bash
   dave-it-guy masterclaw-tui
   # or: dave-it-guy masterclaw-tui --url http://localhost:8090
   ```

3. In the TUI:
   - **1** — Create a task (lightweight Ollama worker).
   - **2** — Create a task (full OpenClaw container).
   - **3** — Get job status by ID.
   - **4** — List recent job IDs.

4. **From inside OpenClaw** (e.g. in a conversation or tool):

   ```bash
   python3 /home/node/.openclaw/workspace/simple_subagent.py "Summarize X" "Context..." llama3.2
   python3 /home/node/.openclaw/workspace/simple_subagent.py "Research Y" "" "" --full-openclaw
   ```

---

## API (MasterClaw)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/subagent` | Create job. Body: `task`, `context?`, `model?`, `timeout_seconds?`, **`use_full_openclaw?`** (default false). Returns `job_id`, `status`. |
| GET | `/subagent/{job_id}` | Status and result (`pending` \| `running` \| `completed` \| `failed`). |
| GET | `/subagent` | List recent job IDs. |
| GET | `/health` | Liveness. |

---

## Security

- **Docker socket** is only in the MasterClaw container; OpenClaw never has it.
- **Task payload** is data (task/context text), not executed code.
- **Secrets** stay in the deployment `.env`; MasterClaw reads it only to forward auth (e.g. gateway token) when POSTing to a sub-OpenClaw gateway.

---

## Scaling: how many sub-agents?

There is **no hard-coded limit** in MasterClaw. Each job gets a unique container name (`openclaw-subagent-<job_id>`) and you can launch as many as you request.

The real limit is **resources**:

- **Host** — CPU and RAM; each full OpenClaw container runs a full gateway and may use the shared LLM.
- **Docker** — The daemon and host can only run so many containers before creation fails or performance degrades.
- **Ollama / Qdrant** — Shared by all sub-agents; under heavy concurrency they can become the bottleneck.

So the number of concurrent sub-agents is **environment-dependent**. To enforce a cap (e.g. max N full-OpenClaw sub-agents), you would add logic in MasterClaw to reject new jobs when the count of running sub-agent containers reaches N.

---

## Lightweight workers vs OpenClaw sub-agents

| Aspect | **Default (lightweight) workers** | **OpenClaw sub-agents** |
|--------|-----------------------------------|--------------------------|
| **Image** | MasterClaw image (`dave-it-guy-masterclaw`) | OpenClaw image (`ghcr.io/openclaw/openclaw`) |
| **What runs** | `worker.py`: one call to Ollama `/api/generate` with the task text | Full OpenClaw: gateway + agent loop (tools, memory, multi-step) |
| **Capabilities** | Single LLM completion only. No tools, no Qdrant, no web search, no multi-turn. | Full agent: can use workspace tools (e.g. `simple_search`, `simple_qdrant`), memory-qdrant, multi-step reasoning, tool calls. |
| **Input** | Task + optional context string → sent as one prompt to Ollama | Same task/context → sent as a user message to OpenClaw’s `/v1/chat/completions` (full agent handles it) |
| **Output** | One text response from the model | Agent’s final reply (after any tool use and reasoning) |
| **Startup** | Fast (small Python container, one Ollama request) | Slower (start gateway, wait for ready, then one request) |
| **Resources** | Low (short-lived, no extra services) | Higher (full OpenClaw process; shares Ollama/Qdrant with the rest of the stack) |
| **Use case** | Simple, single-shot tasks: summarize this, rewrite this, answer this. | Complex tasks: “search the web and summarize,” “store this in Qdrant and then search,” multi-step or tool-using jobs. |

**In short:** default workers = one Ollama completion per task. OpenClaw sub-agents = a full OpenClaw agent in its own container for that task, with tools and memory.

---

## When is Ollama used?

- **Main OpenClaw and full OpenClaw sub-agents**  
  In the OpenClaw config, the default model setup is a **primary** (e.g. OpenAI) and **fallbacks** (e.g. Claude, then Ollama). For those agents, **Ollama is used only when the primary (and any earlier fallbacks) are not reachable or not working**. So Ollama is the fallback, not the default.

- **Default (lightweight) MasterClaw workers**  
  Those workers call **only Ollama** (`/api/generate`). They do not use OpenAI or Claude; **Ollama is the sole backend** for that path, not a fallback. If Ollama is down or unreachable, lightweight worker jobs will fail.

---

## Files and locations

| What | Where |
|------|--------|
| MasterClaw app + worker | `dave_it_guy/templates/openclaw/masterclaw/` (app.py, worker.py, Dockerfile) |
| MasterClaw TUI | `dave_it_guy/masterclaw_tui.py`; CLI: `dave-it-guy masterclaw-tui` |
| OpenClaw workspace tool | `dave_it_guy/templates/openclaw/workspace/simple_subagent.py` (copied into deployment) |
| Full-OpenClaw task script (optional) | `dave_it_guy/templates/openclaw/scripts/run_openclaw_task.py` |
| Compose (MasterClaw service, env, volumes) | `dave_it_guy/templates/openclaw/docker-compose.yml.j2` |

This README and the ASCII diagram give an overview of the entire MasterClaw multi-agent OpenClaw ecosystem.
