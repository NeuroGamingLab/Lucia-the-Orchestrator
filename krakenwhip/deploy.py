"""Stack deployment engine."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax

from krakenwhip.templates import get_template_dir

console = Console()

DEPLOY_DIR = Path.home() / ".krakenwhip" / "deployments"


def deploy_stack(name: str, template: dict[str, Any], options: dict[str, Any]) -> None:
    """Deploy a stack from a template."""
    dry_run = options.get("dry_run", False)
    deploy_path = DEPLOY_DIR / name

    if deploy_path.exists() and not options.get("force") and not dry_run:
        console.print(f"[yellow]⚠️  Stack '{name}' already deployed at {deploy_path}[/yellow]")
        console.print(
            "Use [bold]--force[/bold] to overwrite, or "
            "[bold]krakenwhip destroy {name}[/bold] first."
        )
        return

    mode_label = "DRY RUN" if dry_run else "KrakenWhip"
    console.print(Panel(f"[bold cyan]🐙 Deploying stack: {name}[/bold cyan]", subtitle=mode_label))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # Step 1: Prepare deployment directory
        task = progress.add_task("Preparing deployment directory...", total=None)
        deploy_path.mkdir(parents=True, exist_ok=True)
        progress.update(task, description="✅ Deployment directory ready")

        # Step 2: Render templates
        task2 = progress.add_task("Rendering stack configuration...", total=None)
        _render_templates(name, deploy_path, options)
        progress.update(task2, description="✅ Configuration rendered")

        if dry_run:
            progress.update(
                task2,
                description="✅ Configuration rendered (dry run — stopping here)",
            )
        else:
            # Step 3: Pull images
            task3 = progress.add_task("Pulling container images...", total=None)
            _docker_compose(deploy_path, ["pull"])
            progress.update(task3, description="✅ Images pulled")

            # Step 4: Start services
            task4 = progress.add_task("Starting services...", total=None)
            detach = options.get("detach", True)
            up_args = (
                ["up", "-d", "--wait", "--wait-timeout", "60"] if detach else ["up"]
            )
            try:
                _docker_compose(deploy_path, up_args)
                progress.update(task4, description="✅ Services started")
            except subprocess.CalledProcessError:
                progress.update(
                    task4,
                    description="⚠️  Services started (some may still be initializing)",
                )
                console.print(
                    "\n[yellow]⚠️  Some services may need a moment to become healthy.[/yellow]"
                )
                console.print("[dim]   Check status with: krakenwhip status openclaw[/dim]")
                console.print("[dim]   View logs with:   krakenwhip logs openclaw[/dim]\n")

            # Step 4b: Install memory-qdrant skill for OpenClaw (so Qdrant conversation storage works)
            if name == "openclaw":
                task4b = progress.add_task("Installing memory-qdrant skill...", total=None)
                if _install_openclaw_memory_qdrant_skill():
                    progress.update(task4b, description="✅ memory-qdrant skill installed")
                else:
                    progress.update(
                        task4b,
                        description="⚠️  memory-qdrant install skipped or failed (run manually if needed)",
                    )

            # Step 5: Pre-pull models if requested
            models = options.get("models", [])
            if models:
                task5 = progress.add_task("Pulling AI models...", total=None)
                _pull_models(models)
                progress.update(task5, description=f"✅ {len(models)} model(s) pulled")

    # Print output
    port = options.get("port") or template.get("default_port", 18789)
    console.print()

    if dry_run:
        # Show rendered docker-compose.yml
        compose_file = deploy_path / "docker-compose.yml"
        compose_content = compose_file.read_text()
        console.print(Panel(
            Syntax(compose_content, "yaml", theme="monokai", line_numbers=True),
            title="📄 Generated docker-compose.yml",
            subtitle=str(compose_file),
            border_style="cyan",
        ))

        # Show .env.example if it exists
        env_file = deploy_path / ".env.example"
        if env_file.exists():
            console.print(Panel(
                Syntax(env_file.read_text(), "bash", theme="monokai"),
                title="📄 .env.example",
                border_style="dim",
            ))

        # Show what would happen next
        console.print(Panel(
            f"[bold cyan]🔍 Dry run complete![/bold cyan]\n\n"
            f"  📁 Files rendered to: {deploy_path}\n"
            f"  🔧 Gateway port:     {port}\n"
            f"  🖥️  GPU enabled:      {'Yes' if options.get('gpu') else 'No'}\n"
            f"  📦 Services:         {', '.join(template.get('services', []))}\n\n"
            f"  [dim]To deploy for real, run:[/dim]\n"
            f"  [bold]krakenwhip deploy {name} --force[/bold]",
            title="🏁 Dry Run Summary",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[bold green]✅ Stack '{name}' is running![/bold green]\n\n"
            f"  📍 Gateway:  http://localhost:{port}\n"
            f"  📁 Data:     {deploy_path}\n"
            f"  📋 Logs:     [dim]krakenwhip logs {name}[/dim]\n"
            f"  🛑 Stop:     [dim]krakenwhip stop {name}[/dim]\n"
            f"  🗑️  Destroy:  [dim]krakenwhip destroy {name}[/dim]",
            title="🐙 Deployed",
            border_style="green",
        ))


def stop_stack(name: str) -> None:
    """Stop a running stack."""
    deploy_path = DEPLOY_DIR / name
    if not deploy_path.exists():
        console.print(f"[red]❌ Stack '{name}' not found.[/red]")
        return

    console.print(f"⏹️  Stopping stack '{name}'...")
    _docker_compose(deploy_path, ["stop"])
    console.print(f"[green]✅ Stack '{name}' stopped.[/green]")


def destroy_stack(name: str, remove_volumes: bool = False) -> None:
    """Destroy a stack completely."""
    deploy_path = DEPLOY_DIR / name
    if not deploy_path.exists():
        console.print(f"[red]❌ Stack '{name}' not found.[/red]")
        return

    console.print(f"🗑️  Destroying stack '{name}'...")
    down_args = ["down"]
    if remove_volumes:
        down_args.append("-v")
    _docker_compose(deploy_path, down_args)

    shutil.rmtree(deploy_path)
    console.print(f"[green]✅ Stack '{name}' destroyed.[/green]")


def stack_status(name: str | None = None) -> None:
    """Show status of deployed stacks."""
    if name:
        deploy_path = DEPLOY_DIR / name
        if not deploy_path.exists():
            console.print(f"[red]❌ Stack '{name}' not found.[/red]")
            return
        _docker_compose(deploy_path, ["ps"])
    else:
        if not DEPLOY_DIR.exists():
            console.print(
                "[dim]No stacks deployed yet. Run [bold]krakenwhip deploy <stack>[/bold] "
                "to get started.[/dim]"
            )
            return

        for stack_dir in DEPLOY_DIR.iterdir():
            if stack_dir.is_dir() and (stack_dir / "docker-compose.yml").exists():
                console.print(f"\n[bold cyan]📦 {stack_dir.name}[/bold cyan]")
                _docker_compose(stack_dir, ["ps"])


def stack_logs(name: str, follow: bool = False, tail: int = 50, service: str | None = None) -> None:
    """Show stack logs."""
    deploy_path = DEPLOY_DIR / name
    if not deploy_path.exists():
        console.print(f"[red]❌ Stack '{name}' not found.[/red]")
        return

    args = ["logs", f"--tail={tail}"]
    if follow:
        args.append("-f")
    if service:
        args.append(service)

    _docker_compose(deploy_path, args)


def _render_templates(name: str, deploy_path: Path, options: dict[str, Any]) -> None:
    """Render Jinja2 templates into the deployment directory."""
    template_dir = get_template_dir(name)

    env = Environment(loader=FileSystemLoader(str(template_dir)))

    # Build template context
    env_vars = options.get("env_vars", {})
    context = {
        "gateway_port": options.get("port") or 18789,
        "ollama_port": options.get("ollama_port"),
        "gpu": options.get("gpu", False),
        "env_vars": env_vars,
    }

    # Render docker-compose
    compose_template = env.get_template("docker-compose.yml.j2")
    compose_content = compose_template.render(**context)
    (deploy_path / "docker-compose.yml").write_text(compose_content)

    # Write .env file with secrets (not baked into compose)
    if env_vars:
        env_lines = ["# KrakenWhip — Generated environment config", ""]
        for key, value in env_vars.items():
            env_lines.append(f"{key}={value}")
        env_lines.append("")
        (deploy_path / ".env").write_text("\n".join(env_lines))

    # Copy env example
    env_example = template_dir / "env.example"
    if env_example.exists():
        shutil.copy2(env_example, deploy_path / ".env.example")

    # Copy config directory if it exists
    config_src = template_dir / "config"
    config_dst = deploy_path / "config"
    if config_src.exists():
        if config_dst.exists():
            shutil.rmtree(config_dst)
        shutil.copytree(config_src, config_dst)
        # Render config .j2 templates (e.g. openclaw Control UI allowedOrigins)
        if name == "openclaw":
            config_tpl = template_dir / "config" / "openclaw.json.j2"
            if config_tpl.exists():
                t = env.get_template("config/openclaw.json.j2")
                (config_dst / "openclaw.json").write_text(t.render(**context))


def _install_openclaw_memory_qdrant_skill() -> bool:
    """Install memory-qdrant skill via GitHub tarball (no TTY; playbooks CLI needs interactive)."""
    container = "krakenwhip-openclaw"
    skill_dir = "/home/node/.openclaw/skills/memory-qdrant"
    # Single non-interactive install: curl tarball and extract into skills dir
    install_script = (
        f'test -d {skill_dir} && exit 0; '
        'mkdir -p /home/node/.openclaw/skills && '
        'curl -sLf https://github.com/zuiho-kai/openclaw-memory-qdrant/archive/refs/heads/master.tar.gz | tar xz -C /tmp && '
        f'mv /tmp/openclaw-memory-qdrant-master {skill_dir} && exit 0 || exit 1'
    )
    time.sleep(5)
    for attempt in range(3):
        try:
            result = subprocess.run(
                ["docker", "exec", container, "sh", "-c", install_script],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                return True
            if result.stderr:
                console.print(f"[dim]memory-qdrant install (attempt {attempt + 1}): {result.stderr.strip()[:200]}[/dim]")
            if attempt < 2:
                time.sleep(10)
        except subprocess.TimeoutExpired:
            console.print("[dim]memory-qdrant install timed out (will retry)[/dim]")
            if attempt < 2:
                time.sleep(10)
        except (FileNotFoundError, OSError) as e:
            console.print(f"[dim]memory-qdrant install: {e!s}[/dim]")
            return False
    return False


def _docker_compose(path: Path, args: list[str]) -> subprocess.CompletedProcess:
    """Run docker compose command."""
    # Try 'docker compose' (v2 plugin) first, fall back to 'docker-compose' (standalone)
    cmd = ["docker", "compose", *args]
    result = subprocess.run(cmd, cwd=path, capture_output=True, text=True)

    if result.returncode != 0:
        # Check if it's a "compose not found" vs actual compose error
        stderr = result.stderr or ""
        if "is not a docker command" in stderr or "docker: 'compose'" in stderr:
            # Try standalone docker-compose
            cmd = ["docker-compose", *args]
            result = subprocess.run(cmd, cwd=path, capture_output=True, text=True)

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            console.print("\n[red]❌ Docker Compose failed:[/red]")
            console.print(f"[dim]{error_msg.strip()}[/dim]")

            if "permission denied" in error_msg.lower() or "connect" in error_msg.lower():
                console.print(
                    "\n[yellow]💡 Tip: Make sure Docker is running and you have "
                    "permission to use it.[/yellow]"
                )
                console.print("[dim]   Try: sudo usermod -aG docker $USER (then log out/in)[/dim]")
            elif "not found" in error_msg.lower() or "No such file" in error_msg.lower():
                console.print("\n[yellow]💡 Tip: Docker Compose not found. Install it:[/yellow]")
                console.print("[dim]   https://docs.docker.com/compose/install/[/dim]")

            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )

    # Print stdout if any (for logs, ps, etc.)
    if result.stdout:
        console.print(result.stdout, end="")

    return result


def _pull_models(models: list[str]) -> None:
    """Pre-pull Ollama models."""
    for model in models:
        console.print(f"  📥 Pulling {model}...")
        subprocess.run(
            ["docker", "exec", "krakenwhip-ollama", "ollama", "pull", model],
            check=True,
        )
