# 🐙 KrakenWhip

**Deploy AI stacks with one command.**

KrakenWhip takes the pain out of setting up AI infrastructure. One command gets you a production-ready stack — locally with Docker Compose, or in the cloud with Terraform.

## Overview

KrakenWhip is a Python CLI that deploys pre-built **AI stacks** so you can run OpenClaw, Ollama, Qdrant, and similar tools without wiring Docker and config by hand.

- **Local:** Docker Compose stacks under `~/.krakenwhip/deployments/<stack>/`
- **Cloud (Pro):** Terraform-based deploys (Azure supported; AWS, GCP, DigitalOcean planned)

**Tech stack:** Typer + Rich for the CLI, Jinja2 for templates, `docker compose` (with fallback to standalone `docker-compose`). Cloud: Terraform with Azure provider. Python 3.9+.

**How it works:** Templates live in `krakenwhip/templates/` (e.g. `openclaw`, `ollama`, `rag`). Each has a `docker-compose.yml.j2`, optional `config/` and `env.example`. The deploy flow creates the deployment dir, renders templates, runs `docker compose pull` and `up`, and can pre-pull Ollama models. Use `krakenwhip doctor` to check Docker, disk, and ports.

**Highlights:** Clear separation (CLI, deploy engine, templates, doctor), good UX (progress, dry-run, panels), CI on Python 3.10–3.12 with ruff and pytest. OpenClaw stack is the primary, fully supported path.

**Current limitations:** Pro/cloud path requires a license key (check not yet implemented). Cloud deploy exits after the license prompt — Terraform is not invoked from the CLI yet. The `ollama` and `rag` templates are in the registry but may be incomplete.

## Quick Start

```bash
pip install krakenwhip

# Deploy OpenClaw stack locally (free)
krakenwhip deploy openclaw
# Gateway: http://localhost:18789   Qdrant: http://localhost:6333

# Deploy to Azure (pro)
krakenwhip deploy openclaw --cloud azure
```

The OpenClaw stack uses **Ollama as the default model** (`ollama/llama3.2`). You can optionally provide an Anthropic API key during setup for Claude, or add it later to `~/.krakenwhip/deployments/openclaw/.env`.

## What You Get

### `krakenwhip deploy openclaw`

A complete AI assistant stack:
- **OpenClaw** — personal AI agent framework (gateway on port **18789** by default)
- **Ollama** — local LLM inference (Llama, Mistral, Qwen, etc.); **default model** is `ollama/llama3.2`
- **Qdrant** — vector database for memory (API and dashboard on port **6333**)

All pre-configured, networked, and ready to go. Deployment files and secrets live in `~/.krakenwhip/deployments/openclaw/` (including `.env` and `config/`). To change ports or re-run compose, use the same directory:

```bash
cd ~/.krakenwhip/deployments/openclaw
# Optional: edit docker-compose.yml or .env, then:
docker compose up -d
```

## Pricing

| Tier | What | Price |
|------|------|-------|
| **Free** | Local Docker Compose stacks | $0 |
| **Pro** | Cloud templates (Terraform), priority support | $15/mo or $29 one-time |

## Stack Templates

### Available Now
- `openclaw` — Full OpenClaw + Ollama + Qdrant

### Coming Soon
- `ollama` — Standalone Ollama with model management
- `rag` — RAG pipeline (Qdrant + embeddings API)
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

## Cloud Providers (Pro)

| Provider | Status |
|----------|--------|
| Azure | ✅ Available |
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
