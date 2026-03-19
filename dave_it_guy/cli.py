"""Dave IT Guy CLI — main entry point."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from dave_it_guy import __version__
from dave_it_guy.deploy import deploy_stack, destroy_stack, stack_logs, stack_status, stop_stack
from dave_it_guy.doctor import run_doctor
from dave_it_guy.masterclaw_tui import main as masterclaw_tui_main
from dave_it_guy.templates import get_template, list_templates

app = typer.Typer(
    name="dave-it-guy",
    help="Deploy AI stacks with one command.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


@app.command()
def deploy(
    stack: str = typer.Argument(help="Stack template to deploy (e.g. 'openclaw')"),
    cloud: Optional[str] = typer.Option(
        None, "--cloud", "-c", help="Cloud provider (azure, aws, gcp). Requires Pro."
    ),
    config_file: Optional[str] = typer.Option(
        None, "--config", "-f", help="Custom config file override"
    ),
    gpu: bool = typer.Option(False, "--gpu", help="Enable GPU passthrough for Ollama"),
    models: Optional[str] = typer.Option(
        None, "--models", "-m",
        help="Comma-separated models to pre-pull (e.g. 'llama3.1,mistral')",
    ),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Override default gateway port"),
    ollama_port: Optional[int] = typer.Option(
        None, "--ollama-port",
        help="Host port for Ollama API (default: not exposed). Use if 11434 is in use.",
    ),
    masterclaw_port: Optional[int] = typer.Option(
        None, "--masterclaw-port",
        help="Host port for MasterClaw orchestrator API (openclaw stack only; default 8090).",
    ),
    detach: bool = typer.Option(True, "--detach/--no-detach", "-d", help="Run in background"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing deployment"),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Render config only — don't pull images or start containers",
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", "-k",
        help="Anthropic API key (or set ANTHROPIC_API_KEY env var)",
    ),
    skip_setup: bool = typer.Option(False, "--skip-setup", help="Skip interactive setup prompts"),
    qdrant_primary_url: Optional[str] = typer.Option(
        None, "--qdrant-primary-url",
        help="Primary Qdrant URL (default: http://16.52.188.82:6333/). Used with fallback if unreachable.",
    ),
    qdrant_fallback_url: Optional[str] = typer.Option(
        None, "--qdrant-fallback-url",
        help="Fallback Qdrant URL when primary is unreachable (default: http://qdrant:6333/ in stack, or set via env).",
    ),
):
    """Deploy an AI stack locally or to the cloud."""
    if cloud:
        _check_pro_license()

    template = get_template(stack)
    if not template:
        console.print(f"[red]❌ Unknown stack: '{stack}'[/red]")
        console.print("Run [bold]dave-it-guy list[/bold] to see available stacks.")
        raise typer.Exit(1)

    # Interactive setup for required config
    env_vars = {}
    if not dry_run and not skip_setup:
        env_vars = _interactive_setup(stack, api_key)
    elif api_key:
        env_vars["ANTHROPIC_API_KEY"] = api_key

    options = {
        "cloud": cloud,
        "config_file": config_file,
        "gpu": gpu,
        "models": models.split(",") if models else [],
        "port": port,
        "ollama_port": ollama_port,
        "masterclaw_port": masterclaw_port,
        "detach": detach,
        "force": force,
        "dry_run": dry_run,
        "env_vars": env_vars,
        "qdrant_primary_url": qdrant_primary_url,
        "qdrant_fallback_url": qdrant_fallback_url,
    }

    deploy_stack(stack, template, options)


@app.command(name="list")
def list_stacks():
    """List available stack templates."""
    templates = list_templates()

    table = Table(title="🐙 Available Stacks", show_header=True, header_style="bold cyan")
    table.add_column("Stack", style="bold")
    table.add_column("Description")
    table.add_column("Services")
    table.add_column("Tier", justify="center")

    for t in templates:
        tier = "[green]Free[/green]" if t["tier"] == "free" else "[yellow]Pro[/yellow]"
        table.add_row(t["name"], t["description"], ", ".join(t["services"]), tier)

    console.print(table)


@app.command()
def status(
    stack: Optional[str] = typer.Argument(None, help="Stack name (shows all if omitted)"),
):
    """Check status of running stacks."""
    stack_status(stack)


@app.command()
def stop(
    stack: str = typer.Argument(help="Stack to stop"),
):
    """Stop a running stack (preserves data)."""
    stop_stack(stack)


@app.command()
def destroy(
    stack: str = typer.Argument(help="Stack to destroy"),
    volumes: bool = typer.Option(False, "--volumes", "-v", help="Also remove data volumes"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove a stack completely."""
    if not yes:
        confirm = typer.confirm(f"⚠️  Destroy stack '{stack}'? This cannot be undone.")
        if not confirm:
            raise typer.Abort()

    destroy_stack(stack, remove_volumes=volumes)


@app.command()
def logs(
    stack: str = typer.Argument(help="Stack to view logs for"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    tail: int = typer.Option(50, "--tail", "-n", help="Number of lines to show"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Specific service to view"),
):
    """View stack logs."""
    stack_logs(stack, follow=follow, tail=tail, service=service)


@app.command()
def doctor():
    """Diagnose common issues (Docker, ports, disk space)."""
    run_doctor()


@app.command()
def version():
    """Show Dave IT Guy version."""
    console.print(f"Dave IT Guy v{__version__}")


@app.command(name="masterclaw-tui")
def masterclaw_tui(
    url: Optional[str] = typer.Option(
        None, "--url", "-u",
        help="MasterClaw API URL (default: http://localhost:8090)",
    ),
):
    """Launch MasterClaw TUI to create sub-agent tasks and view results."""
    masterclaw_tui_main(url or "http://localhost:8090")


def _interactive_setup(stack: str, api_key: str | None = None) -> dict[str, str]:
    """Prompt for required configuration interactively."""
    import os

    env_vars: dict[str, str] = {}

    console.print()
    console.print(Panel("[bold]⚙️  Stack Configuration[/bold]", border_style="cyan"))
    console.print()

    if stack in ("openclaw",):
        # Anthropic API key
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if key:
            masked = key[:10] + "..." + key[-4:] if len(key) > 14 else "***"
            console.print(f"  🔑 Anthropic API key: [dim]{masked}[/dim]")
        else:
            console.print("  [bold]Anthropic API key[/bold] is required for OpenClaw to work.")
            console.print("  Get one at: [link=https://console.anthropic.com/]https://console.anthropic.com/[/link]")
            console.print()
            key = typer.prompt("  🔑 Enter your Anthropic API key", hide_input=True)

        if key:
            env_vars["ANTHROPIC_API_KEY"] = key

        # Optional: OpenAI key
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if not openai_key:
            add_openai = typer.confirm(
                "  Add OpenAI API key? (optional, for GPT models)", default=False
            )
            if add_openai:
                openai_key = typer.prompt("  🔑 Enter your OpenAI API key", hide_input=True)
        if openai_key:
            env_vars["OPENAI_API_KEY"] = openai_key

        # Optional: Gateway token
        set_token = typer.confirm(
            "  Set a gateway auth token? (recommended for security)", default=True
        )
        if set_token:
            import secrets
            default_token = secrets.token_urlsafe(24)
            token = typer.prompt("  🔒 Gateway token", default=default_token)
            env_vars["OPENCLAW_GATEWAY_TOKEN"] = token

    console.print()
    return env_vars


def _check_pro_license():
    """Verify Pro license for cloud features."""
    # TODO: Implement license key verification
    console.print(Panel(
        "[yellow]☁️  Cloud deploys require a Pro license.[/yellow]\n\n"
        "Get one at: [link=https://dave-it-guy.dev/pro]https://dave-it-guy.dev/pro[/link]\n\n"
        "Already have a key? Run: [bold]dave-it-guy auth login[/bold]",
        title="Pro Feature",
    ))
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
