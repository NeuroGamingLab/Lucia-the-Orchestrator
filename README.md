# 🐙 KrakenWhip

**Deploy AI stacks with one command — OpenClaw and more, containerized by KrakenWhip.**

KrakenWhip takes the pain out of AI infrastructure: **one command** spins up OpenClaw (and Ollama, Qdrant) **fully containerized**—no host installs, no config archaeology. Run it locally with Docker Compose or ship it to the cloud with Terraform. Same stack, same containers, anywhere.

## Overview

KrakenWhip is a Python CLI that deploys pre-built **AI stacks** so you can run OpenClaw, Ollama, Qdrant, and similar tools without wiring Docker and config by hand.

- **Local:** Docker Compose stacks under `~/.krakenwhip/deployments/<stack>/` — all services run in containers and are exposed on localhost (no host installs).
- **Cloud (Pro):** Terraform-based deploys (Azure supported; AWS, GCP, DigitalOcean planned)

**Tech stack:** Typer + Rich for the CLI, Jinja2 for templates, `docker compose` (with fallback to standalone `docker-compose`). Cloud: Terraform with Azure provider. Python 3.9+.

**How it works:** Templates live in `krakenwhip/templates/` (e.g. `openclaw`, `ollama`, `rag`). Each has a `docker-compose.yml.j2`, optional `config/` and `env.example`. The deploy flow creates the deployment dir, renders templates, runs `docker compose pull` and `up`, and can pre-pull Ollama models. Use `krakenwhip doctor` to check Docker, disk, and ports.

**Highlights:** Clear separation (CLI, deploy engine, templates, doctor), good UX (progress, dry-run, panels), CI on Python 3.10–3.12 with ruff and pytest. OpenClaw stack is the primary, fully supported path.

**Current limitations:** Pro/cloud path requires a license key (check not yet implemented). Cloud deploy exits after the license prompt — Terraform is not invoked from the CLI yet. The `ollama` and `rag` templates are in the registry but may be incomplete.

## Quick Start

1. **Install** KrakenWhip:

   ```bash
   pip install krakenwhip
   ```

   Verify: `krakenwhip version` (or `python3 -m krakenwhip version` if the script is not on your PATH). If you see “command not found: krakenwhip”, either add your Python script directory to `PATH` (e.g. `~/Library/Python/3.9/bin` on macOS for a user install) or run all commands as `python3 -m krakenwhip <command>`.

2. **Deploy** the OpenClaw stack (containers for OpenClaw, Ollama, Qdrant):

   ```bash
   krakenwhip deploy openclaw
   # or: python3 -m krakenwhip deploy openclaw
   ```

   Gateway: **http://localhost:18789** · Qdrant: **http://localhost:6333**

3. **Open the OpenClaw TUI** (interactive terminal UI):

   ```bash
   docker exec -it krakenwhip-openclaw openclaw tui
   ```

   Use the same container name you deployed (e.g. if you deployed as `my-stack`, the container is `krakenwhip-my-stack`).

4. **Optional — Deploy to cloud (Pro):** `krakenwhip deploy openclaw --cloud azure`

The OpenClaw stack uses **Anthropic (Claude)** as the default model when `ANTHROPIC_API_KEY` is set (prompted during setup or in `~/.krakenwhip/deployments/openclaw/.env`). **Ollama (Llama 3.2)** is configured as fallback when the key is missing or for local-only use.

## What You Get

### `krakenwhip deploy openclaw`

A complete AI assistant stack:
- **OpenClaw** — personal AI agent framework (gateway on port **18789** by default)
- **Ollama** — local LLM inference (Llama, Mistral, Qwen, etc.); **default** is Anthropic (Claude) when `ANTHROPIC_API_KEY` is set, with Ollama as fallback
- **Qdrant** — vector database for memory (API and dashboard on port **6333**)

**Important:** OpenClaw, Ollama, and Qdrant all **run inside Docker containers**. Nothing is installed directly on your host. Container ports are mapped to **localhost**, so you use the stack at `http://localhost:18789` (gateway) and `http://localhost:6333` (Qdrant)—containers on the inside, localhost on the outside.

All pre-configured, networked, and ready to go. Deployment files and secrets live in `~/.krakenwhip/deployments/openclaw/` (including `.env` and `config/`). To change ports or re-run compose, use the same directory:

```bash
cd ~/.krakenwhip/deployments/openclaw
# Optional: edit docker-compose.yml or .env, then:
docker compose up -d
```

## ASCII Architecture Diagram

OpenClaw stack deployed by KrakenWhip — all services run in containers; you access them via localhost.

```
                    ┌─────────────────────────────────────────────────────────┐
                    │  Host (localhost)                                        │
                    │                                                          │
  krakenwhip        │   :18789 (gateway)        :6333 (Qdrant API/dashboard)   │
  deploy openclaw   │         │                          │                     │
        │           │         ▼                          ▼                     │
        ▼           │  ┌──────────────┐            ┌──────────────┐          │
  ┌─────────────┐   │  │   Browser    │            │   Browser     │          │
  │ KrakenWhip  │   │  │   / Client  │            │   / Client   │          │
  │    CLI      │   │  └──────┬───────┘            └──────┬───────┘          │
  └──────┬──────┘   │         │                          │                     │
         │          │         │                          │                     │
         │          │  ┌──────┴──────────────────────────┴──────┐             │
         │          │  │  Docker bridge network (krakenwhip)     │             │
         │          │  │                                         │             │
         └──────────┼──┼──► ┌─────────────────┐                  │             │
                    │  │    │ krakenwhip-     │                  │             │
                    │  │    │ openclaw        │◄─── :18789       │             │
                    │  │    │ (gateway + UI)  │                  │             │
                    │  │    └────────┬────────┘                  │             │
                    │  │             │                           │             │
                    │  │      ┌──────┴──────┐                    │             │
                    │  │      ▼             ▼                    │             │
                    │  │  ┌─────────────────┐  ┌─────────────────┐            │
                    │  │  │ krakenwhip-     │  │ krakenwhip-      │◄──── :6333 │
                    │  │  │ ollama (LLM)    │  │ qdrant (vectors) │            │
                    │  │  └─────────────────┘  └─────────────────┘            │
                    │  └─────────────────────────────────────────┘             │
                    └─────────────────────────────────────────────────────────┘
```

- **KrakenWhip CLI** renders config and runs `docker compose up`; all services run **inside containers**.
- **OpenClaw** talks to Ollama and Qdrant over the internal network; only the gateway (18789) and Qdrant (6333) are exposed on localhost.

## Pricing

| Tier | What | Price |
|------|------|-------|
| **Free** | Local Docker Compose stacks | $0 |
| **Pro** | Cloud templates (Terraform), priority support | $15/mo or $29 one-time |

## Stack Templates

### Available Now
- `openclaw` — Full OpenClaw + Ollama + Qdrant
- `ollama` — LocalHost Ollama with model management
- `rag` — RAG pipeline (Qdrant + embeddings API)

### Coming Soon
- `langchain` — LangChain + vector store + API
- `comfyui` — ComfyUI + model cache

## Commands

```bash
krakenwhip deploy <stack>          # Deploy a stack locally
krakenwhip deploy <stack> --cloud <provider>  # Deploy to cloud (pro)
krakenwhip list                    # List available stacks
krakenwhip status [<stack>]         # Check running stacks
krakenwhip stop <stack>            # Stop a stack (preserves data)
krakenwhip destroy <stack>         # Remove a stack (use --volumes to remove data)
krakenwhip logs <stack>            # View stack logs
krakenwhip doctor                  # Diagnose common issues
```

**Deploy options** (e.g. for `openclaw`): `--port` / `-p` (gateway port), `--ollama-port` (expose Ollama on host), `--dry-run` (render config only), `--gpu`, `--models llama3.1,mistral`, `--api-key` / `-k` (Anthropic), `--skip-setup` (skip interactive prompts).

### Teardown (destroy and uninstall)

To remove the stack and the CLI:

```bash
krakenwhip destroy openclaw              # Stop containers and remove deployment files
krakenwhip destroy openclaw --volumes    # Also remove data volumes (Qdrant, Ollama)
pip uninstall krakenwhip                 # Uninstall the package
rm -rf ~/.krakenwhip                     # Optional: remove config directory
```

Use `python3 -m krakenwhip destroy openclaw` if `krakenwhip` is not on your PATH.

### Check Ollama and Qdrant

Use the **stack name you deployed** (e.g. `openclaw` if you ran `krakenwhip deploy openclaw`). Status and logs only work for stacks that are already deployed.

```bash
# If you deployed OpenClaw (includes Ollama + Qdrant)
krakenwhip status openclaw
krakenwhip logs openclaw --service ollama
krakenwhip logs openclaw --service qdrant

```

## Cloud Providers (Pro)

| Provider | Status |
|----------|--------|
| Azure | 🔜 Coming Soon |
| AWS | 🔜 Coming Soon |
| GCP | 🔜 Coming Soon |
| DigitalOcean | 🔜 Coming Soon |

## Requirements

- Python 3.9+
- Docker & Docker Compose (for local deploys)
- Terraform (for cloud deploys, auto-installed if missing)

## License

MIT — Free and open source.

Cloud templates require a Pro license key.

---

Built by [NeuroGamingLab](https://github.com/NeuroGamingLab) · *Dare to Dream, Inspire Leadership, and Spark Innovation Through Diverse Ideas.*
