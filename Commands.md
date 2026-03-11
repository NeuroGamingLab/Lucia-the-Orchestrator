# KrakenWhip & OpenClaw — Command Reference

All commands to set up, launch, and manage KrakenWhip and the OpenClaw stack. Use the project's virtual environment (`.venv`) when working from the repo.

---

## 1. Setup (first time)

### Create and activate virtual environment

```bash
# From the project root
cd krakenwhip   # or your repo path

python3 -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate   # Windows
```

### Install KrakenWhip (editable, with dev deps)

```bash
pip install -e ".[dev]"
```

### Verify install

```bash
krakenwhip version
krakenwhip list
```

---

## 2. Pre-deploy checks

### Run doctor (Docker, disk, ports)

```bash
krakenwhip doctor
```

### Dry-run (render config only, no containers)

```bash
krakenwhip deploy openclaw --dry-run
```

---

## 3. Launch KrakenWhip OpenClaw stack

### Deploy OpenClaw (interactive: prompts for API key, etc.)

```bash
krakenwhip deploy openclaw
```

- **Gateway:** http://localhost:18789  
- **Qdrant:** http://localhost:6333  

------------------------------------------------

### Deploy with common options

```bash
# Custom gateway port
krakenwhip deploy openclaw --port 19000

# Expose Ollama on host (if 11434 is in use)
krakenwhip deploy openclaw --ollama-port 11435

# GPU passthrough for Ollama
krakenwhip deploy openclaw --gpu

# Pre-pull models after start
krakenwhip deploy openclaw --models llama3.1,mistral

# Non-interactive (use env or flag for API key)
export ANTHROPIC_API_KEY=sk-ant-...
krakenwhip deploy openclaw --skip-setup

# Or pass API key on the command line
krakenwhip deploy openclaw --api-key sk-ant-... --skip-setup

# Overwrite existing deployment
krakenwhip deploy openclaw --force
```

### Combined example

```bash
krakenwhip deploy openclaw --port 18789 --gpu --models llama3.2:3b
```

---

## 4. Post-deploy: manage the stack

### Check status

```bash
krakenwhip status              # all stacks
krakenwhip status openclaw     # openclaw only
```

### View logs

```bash
krakenwhip logs openclaw
krakenwhip logs openclaw --follow
krakenwhip logs openclaw --tail 100 --service openclaw
```

### Stop stack (keeps data)

```bash
krakenwhip stop openclaw
```

### Start again (from deployment dir)

```bash
cd ~/.krakenwhip/deployments/openclaw
docker compose up -d
```

### Destroy stack (remove completely)

```bash
krakenwhip destroy openclaw
krakenwhip destroy openclaw --volumes   # also remove data volumes
krakenwhip destroy openclaw --yes      # skip confirmation
```

---

## 5. OpenClaw TUI and container access

### Launch OpenClaw TUI (terminal UI) inside the container

```bash
docker exec -it krakenwhip-openclaw openclaw tui
```

### Shell into OpenClaw container

```bash
docker exec -it krakenwhip-openclaw sh
```

### Ollama (pull model manually)

```bash
docker exec -it krakenwhip-ollama ollama pull llama3.2
docker exec -it krakenwhip-ollama ollama list
```

---

## 6. All KrakenWhip CLI commands (summary)

| Command | Description |
|---------|-------------|
| `krakenwhip version` | Show version |
| `krakenwhip list` | List available stacks |
| `krakenwhip doctor` | Diagnose Docker, disk, ports |
| `krakenwhip deploy openclaw [OPTIONS]` | Deploy OpenClaw stack |
| `krakenwhip status [openclaw]` | Container status |
| `krakenwhip logs openclaw [OPTIONS]` | View logs |
| `krakenwhip stop openclaw` | Stop stack |
| `krakenwhip destroy openclaw [--volumes] [--yes]` | Remove stack |

---

## 7. Using venv without activating

If you don't activate `.venv`, use the venv binaries directly:

```bash
.venv/bin/krakenwhip version
.venv/bin/krakenwhip deploy openclaw
.venv/bin/krakenwhip list
.venv/bin/krakenwhip doctor
```

---

## 8. Deployment directory

- **Path:** `~/.krakenwhip/deployments/openclaw/`
- **Contents:** `docker-compose.yml`, `.env`, `config/` (e.g. `openclaw.json`)
- To edit config or ports, change files there and run `docker compose up -d` in that directory.
