# 🐙 KrakenWhip

**Deploy AI stacks with one command.**

One command spins up OpenClaw with Ollama and Qdrant—fully containerized. No host installs, no config archaeology. Run locally or ship to the cloud. Same stack, anywhere.

## Quick Start

```bash
pip install krakenwhip
krakenwhip deploy openclaw
```

Then open the AI assistant:

```bash
docker exec -it krakenwhip-openclaw openclaw tui
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
krakenwhip list              # Available stacks
krakenwhip deploy openclaw   # Deploy
krakenwhip status openclaw   # Status
krakenwhip logs openclaw     # Logs
krakenwhip doctor            # Diagnose issues
```

## Pricing

**Free** — Local stacks. **Pro** — Cloud (Terraform), priority support.

## License

MIT.

---

Built by [NeuroGamingLab](https://github.com/NeuroGamingLab).
