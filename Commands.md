# Dave IT Guy & OpenClaw — Command Reference

All commands to set up, launch, and manage Dave IT Guy and the OpenClaw stack. Use the project's virtual environment (`.venv`) when working from the repo.

---

## 1. Setup (first time)

### Create and activate virtual environment

```bash
# From the project root
cd dave-it-guy   # or your repo path

python3 -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate   # Windows
```

### Install Dave IT Guy (editable, with dev deps)

```bash
pip install -e ".[dev]"
```

### Verify install

```bash
dave-it-guy version
dave-it-guy list
```

---

## 2. Pre-deploy checks

### Run doctor (Docker, disk, ports)

```bash
dave-it-guy doctor
```

### Dry-run (render config only, no containers)

```bash
dave-it-guy deploy openclaw --dry-run
```

---

## 3. Launch Dave IT Guy OpenClaw stack

### Deploy OpenClaw (interactive: prompts for API key, etc.)

```bash
dave-it-guy deploy openclaw
```

- **Gateway:** http://localhost:18789  
- **Qdrant:** http://localhost:6333  

------------------------------------------------

### Deploy with common options

```bash
# Custom gateway port
dave-it-guy deploy openclaw --port 19000

# Expose Ollama on host (if 11434 is in use)
dave-it-guy deploy openclaw --ollama-port 11435

# GPU passthrough for Ollama
dave-it-guy deploy openclaw --gpu

# Pre-pull models after start
dave-it-guy deploy openclaw --models llama3.1,mistral

# Non-interactive (use env or flag for API key)
export ANTHROPIC_API_KEY=sk-ant-...
dave-it-guy deploy openclaw --skip-setup

# Or pass API key on the command line
dave-it-guy deploy openclaw --api-key sk-ant-... --skip-setup

# Overwrite existing deployment
dave-it-guy deploy openclaw --force
```

### Combined example

```bash
dave-it-guy deploy openclaw --port 18789 --gpu --models llama3.2:3b
```

---

## 4. Post-deploy: manage the stack

### Check status

```bash
dave-it-guy status              # all stacks
dave-it-guy status openclaw     # openclaw only
```

### View logs

```bash
dave-it-guy logs openclaw
dave-it-guy logs openclaw --follow
dave-it-guy logs openclaw --tail 100 --service openclaw
```

### Stop stack (keeps data)

```bash
dave-it-guy stop openclaw
```

### Start again (from deployment dir)

```bash
cd ~/.dave_it_guy/deployments/openclaw
docker compose up -d
```

### Destroy stack (remove completely)

```bash
dave-it-guy destroy openclaw
dave-it-guy destroy openclaw --volumes   # also remove data volumes
dave-it-guy destroy openclaw --yes      # skip confirmation
```

---

## 5. OpenClaw TUI and container access

### Launch OpenClaw TUI (terminal UI) inside the container

```bash
docker exec -it dave-it-guy-openclaw openclaw tui
```

### Shell into OpenClaw container

```bash
docker exec -it dave-it-guy-openclaw sh
```

### Ollama (pull model manually)

```bash
docker exec -it dave-it-guy-ollama ollama pull llama3.2
docker exec -it dave-it-guy-ollama ollama list
```

---

## 6. All Dave IT Guy CLI commands (summary)

| Command | Description |
|---------|-------------|
| `dave-it-guy version` | Show version |
| `dave-it-guy list` | List available stacks |
| `dave-it-guy doctor` | Diagnose Docker, disk, ports |
| `dave-it-guy deploy openclaw [OPTIONS]` | Deploy OpenClaw stack |
| `dave-it-guy status [openclaw]` | Container status |
| `dave-it-guy logs openclaw [OPTIONS]` | View logs |
| `dave-it-guy stop openclaw` | Stop stack |
| `dave-it-guy destroy openclaw [--volumes] [--yes]` | Remove stack |

---

## 7. Using venv without activating

If you don't activate `.venv`, use the venv binaries directly:

```bash
.venv/bin/dave-it-guy version
.venv/bin/dave-it-guy deploy openclaw
.venv/bin/dave-it-guy list
.venv/bin/dave-it-guy doctor
```

---

## 8. Deployment directory

- **Path:** `~/.dave_it_guy/deployments/openclaw/`
- **Contents:** `docker-compose.yml`, `.env`, `config/` (e.g. `openclaw.json`)
- To edit config or ports, change files there and run `docker compose up -d` in that directory.
