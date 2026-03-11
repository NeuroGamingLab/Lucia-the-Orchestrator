# Contributing to KrakenWhip

## Dev Setup

**Always use the project's Python virtual environment (`.venv`)** for installing dependencies, running the CLI, tests, and tools.

```bash
# Clone the repo
git clone https://github.com/NeuroGamingLab/krakenwhip.git
cd krakenwhip

# Create and activate the virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

**From another terminal or without activating:** use the venv's binaries directly, e.g. `./.venv/bin/krakenwhip` or `./.venv/bin/pytest`.

## Running Tests

```bash
# With venv activated:
pytest -v
# Or: .venv/bin/pytest -v
```

## Linting & Formatting

```bash
# With venv activated:
ruff check .
ruff format .
```

## Type Checking

```bash
# With venv activated:
mypy krakenwhip/ --ignore-missing-imports
```

## Submitting Changes

1. Fork the [NeuroGamingLab/krakenwhip](https://github.com/NeuroGamingLab/krakenwhip) repo and create a feature branch
2. Make your changes
3. Ensure tests pass and linting is clean
4. Open a pull request against [NeuroGamingLab/krakenwhip](https://github.com/NeuroGamingLab/krakenwhip) on the `main` branch
