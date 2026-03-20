# Dave IT Guy

**Deploy AI stacks with one command.**

Dave-IT-Guy delivers a **fully containerized** stack with **OpenClaw as its core engine** plus Ollama and Qdrant, in a single command. No host installs, no config archaeology. Everything runs in Docker; run locally or ship to the cloud. Same stack, anywhere.

**From single assistant to self-orchestrating system.**

`dave-it-guy` transforms from a single operator into **dave-the-MasterClaw**: a recursive orchestrator with one control plane that can spawn specialized OpenClaw runtimes on demand. In this model, the original TUI is no longer just a launcher for one stack; it becomes a parent agent that delegates work to child agents, each running as an independent OpenClaw container with its own execution lifecycle.

This is the shift from a single-instance assistant to a multi-instance self-orchestrating system: `dave-it-guy` becomes the “master of itself,” able to launch, supervise, and coordinate additional OpenClaw sub-agents, then either clean them up automatically or keep them alive for interactive continuation.

![Dave the MasterClaw architecture](https://raw.githubusercontent.com/NeuroGamingLab/KrakenWhip/feature/openclaw-external-orchestrator/dave-the-masterClaw-architecture-small.png)

## Quick Start

```bash
pip install dave-it-guy
dave-it-guy deploy openclaw
```

Then open the AI assistant:

```bash
docker exec -it dave-it-guy-openclaw openclaw tui
```

**Gateway:** http://localhost:18789 · **Qdrant:** http://localhost:6333/

Set your Anthropic API key when prompted (or add it later); Ollama is the fallback for local-only use.

## Run the CLI from Docker (localhost)

Build the image from this repo, then use your host’s Docker socket so `deploy` can start the stack on **localhost** (same as `pip install` + `dave-it-guy deploy`).

```bash
# From the repo root
docker build -t dave-it-guy:local .

# Smoke test (no Docker socket needed)
docker run --rm dave-it-guy:local list

# Deploy OpenClaw on localhost (mount socket + persist ~/.dave_it_guy)
# Note: this container runs as root *inside the container only* so it can
# access /var/run/docker.sock and write deployment state at /root/.dave_it_guy.
# Conceptually, Dave-The-MasterClaw is the orchestrator with Docker control, which is
# why it needs this level of access to launch and manage sub-agent containers.
docker run --rm -it \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$HOME/.dave_it_guy:/root/.dave_it_guy" \
  dave-it-guy:local deploy openclaw
```

- On **Windows (Docker Desktop)**, use `-v //var/run/docker.sock:/var/run/docker.sock` if the path above does not work.
- Pass `--skip-setup` and set `ANTHROPIC_API_KEY` (and optional `OPENAI_API_KEY`) in the environment if you want a non-interactive deploy:

  ```bash
  docker run --rm -it \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$HOME/.dave_it_guy:/root/.dave_it_guy" \
    -e ANTHROPIC_API_KEY="your-key" \
    dave-it-guy:local deploy openclaw --skip-setup --force
  ```

After deploy, open **http://localhost:18789** or run `docker exec -it dave-it-guy-openclaw openclaw tui` on the host.

## What You Get

- **OpenClaw (main runtime)** — AI agent gateway, tools, and TUI
- **MasterClaw (external orchestrator)** — launches and coordinates sub-agent jobs
- **Enhanced terminal UI** — `masterclaw-tui` to create, monitor, and manage sub-agents
- **Multi-instance execution** — run lightweight workers or full OpenClaw sub-agents per task
- **Ollama** — local/shared model backend for workers and fallback model path
- **Qdrant** — shared vector memory across main and sub-agent flows

Fully containerized: the whole stack runs in Docker, no installs on your host machine.

## For AI/ML scientists and engineers: why Dave IT Guy over other LLMs

- **Core is OpenClaw** — Dave IT Guy’s engine is OpenClaw: an assistant framework with multi-model support, tools, skills, and memory—deployed in one command.
- **Autonomous agent** — Multi-step tasks, tools, skills, and persistent RAG (Qdrant)—not just single-turn chat.
- **Your data, your infra** — Conversations and embeddings stay on your machine or cloud; no sending prompts to third-party chat APIs unless you opt in.
- **Multi-model, one agent** — Local (Ollama) + Anthropic/OpenAI in one config; A/B test models and use fallbacks without switching products.
- **Reproducible** — Config-as-code; version and rerun the same stack anywhere (laptop, lab, cloud).
- **Extensible** — Add workspace scripts and OpenClaw skills; plug in your evals, pipelines, and tools.

## Commands

```bash
dave-it-guy list              # Available stacks
dave-it-guy deploy openclaw   # Deploy
dave-it-guy masterclaw-tui    # Launch MasterClaw Enhanced terminal UI
dave-it-guy status openclaw   # Status
dave-it-guy logs openclaw     # Logs
dave-it-guy stop openclaw     # Stop stack (preserve data)
dave-it-guy destroy openclaw  # Remove stack
dave-it-guy doctor            # Diagnose issues
dave-it-guy version           # CLI version
```

## Pricing

**Free** — Local stacks. **Pro** — Cloud (Terraform), priority support.

## License

MIT.

---

Built by [NeuroGamingLab](https://github.com/NeuroGamingLab).
