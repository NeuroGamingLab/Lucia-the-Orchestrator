# Dave IT Guy

**Deploy AI stacks with one command.**

One command spins up OpenClaw with Ollama and Qdrant—fully containerized. No host installs, no config archaeology. Run locally or ship to the cloud. Same stack, anywhere.

## Quick Start

```bash
pip install dave-it-guy
dave-it-guy deploy openclaw
```

Then open the AI assistant:

```bash
docker exec -it dave-it-guy-openclaw openclaw tui
```

**Gateway:** http://localhost:18789 · **Qdrant:** http://localhost:6333

Set your Anthropic API key when prompted (or add it later); Ollama is the fallback for local-only use.

## What You Get

- **OpenClaw** — AI agent gateway and TUI
- **Ollama** — local LLMs (Llama, Mistral, etc.)
- **Qdrant** — vector memory

Everything runs in Docker. Nothing on your host.

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
