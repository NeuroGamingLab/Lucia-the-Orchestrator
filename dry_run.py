#!/usr/bin/env python3
"""Dave IT Guy Dry Run — render and preview templates without any dependencies.

Usage:
    python3 dry_run.py [stack] [--gpu] [--port PORT]

Examples:
    python3 dry_run.py openclaw
    python3 dry_run.py openclaw --gpu --port 19000
    python3 dry_run.py ollama
    python3 dry_run.py rag
    python3 dry_run.py all
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "dave_it_guy" / "templates"
OUTPUT_DIR = Path.home() / ".dave_it_guy" / "dry-run"

# Template registry
STACKS = {
    "openclaw": {
        "description": "Full AI assistant — OpenClaw + Ollama + Qdrant",
        "services": ["openclaw", "ollama", "qdrant"],
        "tier": "free",
        "default_port": 18789,
    },
    "ollama": {
        "description": "Standalone Ollama with Open WebUI",
        "services": ["ollama", "open-webui"],
        "tier": "free",
        "default_port": 3000,
    },
    "rag": {
        "description": "RAG pipeline — Qdrant + embeddings + API",
        "services": ["qdrant", "embeddings", "rag-api"],
        "tier": "free",
        "default_port": 8080,
    },
}


def simple_render(template_text: str, context: dict) -> str:
    """Minimal Jinja2-compatible renderer for simple templates.

    Handles: {{ var }}, {{ var | default('x') }}, {% if %}/{% endif %},
    {% for %}/{% endfor %}, and basic nesting.
    """
    result = template_text

    # Handle {{ var | default('value') }} patterns
    def replace_default(m):
        var = m.group(1).strip()
        default_val = m.group(2).strip().strip("'\"")
        return str(context.get(var, default_val))

    result = re.sub(
        r'\{\{\s*(\w+)\s*\|\s*default\([\'"]([^)]*?)[\'"]\)\s*\}\}',
        replace_default,
        result,
    )

    # Handle {{ var }} patterns
    def replace_var(m):
        var = m.group(1).strip()
        val = context.get(var, "")
        return str(val)

    result = re.sub(r'\{\{\s*(\w+)\s*\}\}', replace_var, result)

    # Handle {% if var %} ... {% endif %} and {% if var is not none %} ... {% endif %}
    def replace_if(m):
        condition = m.group(1).strip()  # e.g. "ollama_port" or "ollama_port is not none"
        body = m.group(2)
        if " is not none" in condition:
            var = condition.replace(" is not none", "").strip()
            if context.get(var) is not None:
                return body
        elif context.get(condition):
            return body
        return ""

    # Repeat to handle multiple if blocks
    for _ in range(10):
        new_result = re.sub(
            r'\{%[-\s]*if\s+([^%]+?)\s*[-\s]*%\}(.*?)\{%[-\s]*endif\s*[-\s]*%\}',
            replace_if,
            result,
            flags=re.DOTALL,
        )
        if new_result == result:
            break
        result = new_result

    # Handle {% for key, value in dict.items() %} ... {% endfor %}
    def replace_for_dict(m):
        key_var = m.group(1).strip()
        val_var = m.group(2).strip()
        dict_name = m.group(3).strip()
        body = m.group(4)
        d = context.get(dict_name, {})
        output = ""
        for k, v in d.items():
            line = body.replace("{{ " + key_var + " }}", str(k))
            line = line.replace("{{ " + val_var + " }}", str(v))
            output += line
        return output

    result = re.sub(
        r'\{%[-\s]*for\s+(\w+)\s*,\s*(\w+)\s+in\s+(\w+)\.items\(\)\s*[-\s]*%\}(.*?)\{%[-\s]*endfor\s*[-\s]*%\}',
        replace_for_dict,
        result,
        flags=re.DOTALL,
    )

    # Clean up any remaining whitespace-only lines from removed blocks
    lines = result.split("\n")
    cleaned = []
    for line in lines:
        if line.strip() == "":
            if cleaned and cleaned[-1].strip() == "":
                continue
        cleaned.append(line)

    return "\n".join(cleaned)


def dry_run(stack_name: str, gpu: bool = False, port: int | None = None) -> None:
    """Render a stack template and display the output."""
    if stack_name not in STACKS:
        print(f"❌ Unknown stack: '{stack_name}'")
        print(f"   Available: {', '.join(STACKS.keys())}")
        return

    info = STACKS[stack_name]
    template_file = TEMPLATE_DIR / stack_name / "docker-compose.yml.j2"
    env_file = TEMPLATE_DIR / stack_name / "env.example"

    if not template_file.exists():
        print(f"❌ Template not found: {template_file}")
        return

    effective_port = port or info["default_port"]

    # Build context
    context = {
        "gateway_port": str(effective_port),
        "gpu": gpu,
        "env_vars": {},
        "webui_port": "3000",
        "ollama_models": "",
        "embedding_model": "BAAI/bge-small-en-v1.5",
        "rag_api_port": "8080",
        "qdrant_collection": "documents",
        "ollama_port": None,  # dry_run doesn't expose Ollama by default
    }

    # Render
    template_text = template_file.read_text()
    rendered = simple_render(template_text, context)

    # Output
    print()
    print(f"{'='*60}")
    print(f"Dave IT Guy DRY RUN: {stack_name}")
    print(f"{'='*60}")
    print(f"  📦 Stack:      {info['description']}")
    print(f"  🔧 Services:   {', '.join(info['services'])}")
    print(f"  💰 Tier:       {info['tier']}")
    print(f"  🔌 Port:       {effective_port}")
    print(f"  🖥️  GPU:        {'Enabled' if gpu else 'Disabled'}")
    print(f"{'='*60}")
    print()

    # Show rendered docker-compose
    print(f"📄 docker-compose.yml (rendered from {template_file.name}):")
    print(f"{'─'*60}")
    print(rendered)
    print(f"{'─'*60}")

    # Show env example
    if env_file.exists():
        print()
        print("📄 .env.example:")
        print(f"{'─'*60}")
        print(env_file.read_text())
        print(f"{'─'*60}")

    # Save rendered output
    out_dir = OUTPUT_DIR / stack_name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "docker-compose.yml").write_text(rendered)
    if env_file.exists():
        import shutil
        shutil.copy2(env_file, out_dir / ".env.example")

    print()
    print(f"✅ Rendered files saved to: {out_dir}")
    print(f"   To deploy for real: dave-it-guy deploy {stack_name}" + (" --gpu" if gpu else ""))
    print()


def main():
    args = sys.argv[1:]
    gpu = "--gpu" in args
    args = [a for a in args if a != "--gpu"]

    port = None
    if "--port" in args:
        idx = args.index("--port")
        if idx + 1 < len(args):
            port = int(args[idx + 1])
            args = args[:idx] + args[idx+2:]

    stack = args[0] if args else "openclaw"

    if stack == "all":
        for name in STACKS:
            dry_run(name, gpu=gpu, port=port)
    else:
        dry_run(stack, gpu=gpu, port=port)


if __name__ == "__main__":
    main()
