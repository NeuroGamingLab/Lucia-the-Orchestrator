# Security

## Pre-push review (workspace scripts & deploy)

### Scope reviewed

- **simple_search.py** (workspace, `krakenwhip/simple_search.py`, `scripts/simple_search.py`)
- **simple_qdrant.py** (template workspace)
- **deploy.py** (template render, .env write, docker exec, model pull)
- **CLI** (stack name, options → deploy)

### Findings and mitigations

| Area | Risk | Mitigation |
|------|------|------------|
| **simple_search** | `max_results` from argv could be non-numeric or very large (DoM/abuse) | Coerce to int with try/except, clamp to 1–50 |
| **simple_qdrant** | Collection name from argv could be used in unsafe ways | Restrict to `[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}` |
| **simple_qdrant** | Huge `text` / `query` could cause memory DoS | Cap text length at 1M chars for upsert; search limit clamped to 1–100 |
| **simple_qdrant** | `limit` from argv could be invalid or excessive | `int()` with fallback, clamp to 1–100 |
| **deploy.py** | `.env` key/value with newlines could inject extra env vars | Strip/sanitize: no newlines in key, newlines in value replaced by space; key must not contain `=` |
| **deploy.py** | `--models` passed to `docker exec … ollama pull <model>` | No shell (list form); model name restricted to `[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}`; invalid names skipped with a message |

### What was already safe

- **No shell=True**: All `subprocess` calls use list form; no user input is passed to a shell.
- **Deploy path**: `deploy_path = DEPLOY_DIR / name` with `name` from a known template (validated by `get_template(stack)`).
- **Install script**: `_install_openclaw_memory_qdrant_skill` uses a fixed script string (no user input).
- **Point IDs**: `simple_qdrant` uses UUIDs only (validated via `_point_id()`).
- **QDRANT_URL**: From env (container/deployer-controlled); no user-controlled URL.

### Recommendations

- Keep dependencies (e.g. `duckduckgo-search`, `qdrant-client`, `sentence-transformers`) updated for security fixes.
- API keys and tokens are written to `~/.krakenwhip/deployments/<stack>/.env`; ensure that directory has appropriate permissions and is not in shared or version-controlled paths.
- For production, use a gateway token (OpenClaw) and restrict network exposure of the stack.

### Reporting vulnerabilities

Please report security issues privately (e.g. via maintainer contact or a private security advisory) rather than in public issue trackers.
