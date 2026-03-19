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

## Run the CLI from Docker (localhost)

Build the image from this repo, then use your host’s Docker socket so `deploy` can start the stack on **localhost** (same as `pip install` + `dave-it-guy deploy`).

```bash
# From the repo root
docker build -t dave-it-guy:local .

# Smoke test (no Docker socket needed)
docker run --rm dave-it-guy:local list

# Deploy OpenClaw on localhost (mount socket + persist ~/.dave_it_guy)
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

The image includes `docker.io` and `docker-compose` so `docker compose` / `docker-compose` work against the host daemon. Image size is large because the package depends on PyTorch / sentence-transformers for workspace tooling.

**If you see `FileNotFoundError: ... 'docker'`** when running `dave-it-guy` on the host (e.g. from Cursor): your shell’s `PATH` may not include the Docker CLI. Run `which docker` in that same terminal; if empty, add Docker Desktop’s bin to PATH (often `/usr/local/bin` or `/Applications/Docker.app/Contents/Resources/bin/docker`) or run deploy from Terminal.app/iTerm where Docker works. The CLI now also searches those paths automatically on macOS.

**Compose helper:** `docker compose -f docker-compose.cli.yml build` then  
`docker compose -f docker-compose.cli.yml run --rm dave-it-guy deploy openclaw` (override `command` as needed).

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
