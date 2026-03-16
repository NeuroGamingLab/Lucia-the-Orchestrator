# Dave IT Guy

**Deploy AI stacks with one command.**

Dave-IT-Guy delivers a **fully containerized** stack with **OpenClaw as its core engine** plus Ollama and Qdrant, in a single command. No host installs, no config archaeology. Everything runs in Docker; run locally or ship to the cloud. Same stack, anywhere.

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

## What You Get

- **OpenClaw** — AI agent gateway and TUI
- **Ollama** — local LLMs (Llama, Mistral, etc.)
- **Qdrant** — vector memory

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
dave-it-guy status openclaw   # Status
dave-it-guy logs openclaw     # Logs
dave-it-guy doctor            # Diagnose issues
```

## Pricing

**Free** — Local stacks. **Pro** — Cloud (Terraform), priority support.

## License

MIT.

---

Built by [NeuroGamingLab](https://github.com/NeuroGamingLab).
