"""Stack deployment engine."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax

from dave_it_guy.templates import get_template_dir

console = Console()

DEPLOY_DIR = Path.home() / ".dave_it_guy" / "deployments"


def _remove_tree(path: Path) -> None:
    """
    Remove a directory tree. On POSIX, use rm -rf so removal matches the shell and
    avoids shutil.rmtree edge cases on macOS (e.g. OSError Errno 66 ENOTEMPTY on busy
    or permission-quirky trees under ~/.dave_it_guy).
    """
    if not path.exists():
        return
    resolved = path.resolve()
    if sys.platform == "win32":
        shutil.rmtree(resolved)
        return
    subprocess.run(["rm", "-rf", str(resolved)], check=True)


def _docker_cli_path() -> str | None:
    """Return path to docker if found (PATH + common locations, incl. Docker Desktop on macOS)."""
    found = shutil.which("docker")
    if found:
        return found
    for p in (
        "/usr/bin/docker",
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
        "/Applications/Docker.app/Contents/Resources/bin/docker",
    ):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _resolve_docker_cli() -> str:
    """Find docker executable or print help and raise FileNotFoundError."""
    p = _docker_cli_path()
    if p:
        return p
    console.print(
        "\n[red]❌ Docker CLI not found.[/red]\n"
        "[yellow]Dave IT Guy needs the `docker` command.[/yellow]\n\n"
        "• [bold]macOS:[/bold] Install Docker Desktop, start it, then run from a terminal where "
        "`which docker` works. If you use Cursor/VS Code, the integrated terminal may not see "
        "the same PATH as iTerm — add Docker to PATH or open Docker Desktop → Settings → "
        "Advanced → ensure CLI tools are available.\n"
        "• [bold]Linux:[/bold] Install `docker.io` or Docker CE CLI; add your user to the "
        "`docker` group if needed.\n"
        "• [bold]Docker image:[/bold] Rebuild with the repo Dockerfile (includes docker.io).\n"
    )
    raise FileNotFoundError("docker")


def _resolve_docker_compose_v1() -> str | None:
    """Standalone docker-compose binary if present."""
    found = shutil.which("docker-compose")
    if found:
        return found
    for p in ("/usr/bin/docker-compose", "/usr/local/bin/docker-compose"):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def deploy_stack(name: str, template: dict[str, Any], options: dict[str, Any]) -> None:
    """Deploy a stack from a template."""
    dry_run = options.get("dry_run", False)
    deploy_path = DEPLOY_DIR / name

    if deploy_path.exists() and not options.get("force") and not dry_run:
        console.print(f"[yellow]⚠️  Stack '{name}' already deployed at {deploy_path}[/yellow]")
        console.print(
            "Use [bold]--force[/bold] to overwrite, or "
            "[bold]dave-it-guy destroy {name}[/bold] first."
        )
        return

    mode_label = "DRY RUN" if dry_run else "Dave IT Guy"
    console.print(Panel(f"[bold cyan]Deploying stack: {name}[/bold cyan]", subtitle=mode_label))

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
            # Some services (e.g. MasterClaw) use `build:`. `docker compose pull` will still
            # try to pull their `image:` tag, which fails if not built yet.
            # `--ignore-pull-failures` keeps deploy robust across build-vs-pull services.
            try:
                _docker_compose(deploy_path, ["pull", "--ignore-pull-failures"])
            except subprocess.CalledProcessError:
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
                console.print("[dim]   Check status with: dave-it-guy status openclaw[/dim]")
                console.print("[dim]   View logs with:   dave-it-guy logs openclaw[/dim]\n")

            # Step 4a (openclaw only): Ensure openclaw container is actually running before skill install
            if name == "openclaw":
                task4a = progress.add_task("Ensuring OpenClaw is running...", total=None)
                if _ensure_openclaw_running(deploy_path):
                    progress.update(task4a, description="✅ OpenClaw container running")
                else:
                    progress.update(
                        task4a,
                        description="⚠️  OpenClaw may still be starting (check: dave-it-guy status openclaw)",
                    )

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
            f"  [bold]dave-it-guy deploy {name} --force[/bold]",
            title="🏁 Dry Run Summary",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[bold green]✅ Stack '{name}' is running![/bold green]\n\n"
            f"  📍 Gateway:  http://localhost:{port}\n"
            f"  📁 Data:     {deploy_path}\n"
            f"  📋 Logs:     [dim]dave-it-guy logs {name}[/dim]\n"
            f"  🛑 Stop:     [dim]dave-it-guy stop {name}[/dim]\n"
            f"  🗑️  Destroy:  [dim]dave-it-guy destroy {name}[/dim]",
            title="Deployed",
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

    _remove_tree(deploy_path)
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
                "[dim]No stacks deployed yet. Run [bold]dave-it-guy deploy <stack>[/bold] "
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


def sync_openclaw_scheduler_script() -> bool:
    """
    Copy template scheduler script into deployed OpenClaw workspace.
    Returns True on success, False if source/target missing.
    """
    template_scheduler = (
        get_template_dir("openclaw") / "workspace" / "simple_scheduler.py"
    )
    deployed_scheduler = (
        DEPLOY_DIR / "openclaw" / "workspace" / "simple_scheduler.py"
    )
    if not template_scheduler.exists():
        console.print(f"[red]❌ Template not found: {template_scheduler}[/red]")
        return False
    if not deployed_scheduler.parent.exists():
        console.print(
            "[red]❌ OpenClaw deployment workspace not found. Deploy openclaw first.[/red]"
        )
        return False
    shutil.copy2(template_scheduler, deployed_scheduler)
    console.print(
        Panel(
            "[bold green]✅ Synced scheduler script[/bold green]\n\n"
            f"Source: [dim]{template_scheduler}[/dim]\n"
            f"Target: [dim]{deployed_scheduler}[/dim]",
            title="OpenClaw Workspace Sync",
            border_style="green",
        )
    )
    return True


def _render_templates(name: str, deploy_path: Path, options: dict[str, Any]) -> None:
    """Render Jinja2 templates into the deployment directory."""
    template_dir = get_template_dir(name)

    env = Environment(loader=FileSystemLoader(str(template_dir)))

    # Build template context
    env_vars = options.get("env_vars", {})
    context = {
        "gateway_port": options.get("port") or 18789,
        "ollama_port": options.get("ollama_port"),
        "masterclaw_port": options.get("masterclaw_port") or 8090,
        "compose_project_name": name,
        "deploy_path": str(deploy_path.resolve()),
        "gpu": options.get("gpu", False),
        "env_vars": env_vars,
        "qdrant_primary_url": options.get("qdrant_primary_url") or "http://16.52.188.82:6333/",
        "qdrant_fallback_url": options.get("qdrant_fallback_url") or "http://qdrant:6333/",
    }

    # Render docker-compose
    compose_template = env.get_template("docker-compose.yml.j2")
    compose_content = compose_template.render(**context)
    (deploy_path / "docker-compose.yml").write_text(compose_content)

    # Write .env file with secrets (not baked into compose)
    if env_vars:
        env_lines = ["# Dave IT Guy — Generated environment config", ""]
        for key, value in env_vars.items():
            # Security: prevent injection of extra env lines via newlines in key/value
            k = str(key).replace("\n", "").replace("\r", "").strip()
            v = str(value).replace("\n", " ").replace("\r", " ").strip()
            if k and "=" not in k:
                env_lines.append(f"{k}={v}")
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
            _remove_tree(config_dst)
        shutil.copytree(config_src, config_dst)
        # Render config .j2 templates (e.g. openclaw Control UI allowedOrigins)
        if name == "openclaw":
            config_tpl = template_dir / "config" / "openclaw.json.j2"
            if config_tpl.exists():
                t = env.get_template("config/openclaw.json.j2")
                (config_dst / "openclaw.json").write_text(t.render(**context))

    # Copy workspace directory for openclaw (e.g. simple_search.py for web search)
    if name == "openclaw":
        workspace_src = template_dir / "workspace"
        workspace_dst = deploy_path / "workspace"
        if workspace_src.exists():
            if workspace_dst.exists():
                _remove_tree(workspace_dst)
            shutil.copytree(workspace_src, workspace_dst)
        # Copy MasterClaw orchestrator (Dockerfile + app + worker) for build
        masterclaw_src = template_dir / "masterclaw"
        masterclaw_dst = deploy_path / "masterclaw"
        if masterclaw_src.exists():
            if masterclaw_dst.exists():
                _remove_tree(masterclaw_dst)
            shutil.copytree(masterclaw_src, masterclaw_dst)
        # Copy scripts for full-OpenClaw sub-agent (run_openclaw_task.py)
        scripts_src = template_dir / "scripts"
        scripts_dst = deploy_path / "scripts"
        if scripts_src.exists():
            if scripts_dst.exists():
                _remove_tree(scripts_dst)
            shutil.copytree(scripts_src, scripts_dst)


def _ensure_openclaw_running(deploy_path: Path) -> bool:
    """Poll until openclaw container is running; optionally start it once. Returns True if running."""
    container = "dave-it-guy-openclaw"
    poll_interval = 3
    first_wait = 90
    second_wait = 30

    def _is_running() -> bool:
        r = subprocess.run(
            [_resolve_docker_cli(), "inspect", "--format", "{{.State.Running}}", container],
            capture_output=True,
            text=True,
        )
        return r.returncode == 0 and r.stdout.strip().lower() == "true"

    for _ in range(0, first_wait, poll_interval):
        if _is_running():
            return True
        time.sleep(poll_interval)

    # One attempt to start the service (e.g. if it was Created but not started)
    try:
        _docker_compose(deploy_path, ["start", "openclaw"])
    except subprocess.CalledProcessError:
        pass
    for _ in range(0, second_wait, poll_interval):
        if _is_running():
            return True
        time.sleep(poll_interval)
    return False


def _install_openclaw_memory_qdrant_skill() -> bool:
    """Install memory-qdrant skill via GitHub tarball (no TTY; playbooks CLI needs interactive)."""
    container = "dave-it-guy-openclaw"
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
                [_resolve_docker_cli(), "exec", container, "sh", "-c", install_script],
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
    docker_bin = _resolve_docker_cli()
    # Try 'docker compose' (v2 plugin) first, fall back to 'docker-compose' (standalone)
    cmd = [docker_bin, "compose", *args]
    try:
        result = subprocess.run(cmd, cwd=path, capture_output=True, text=True)
    except FileNotFoundError as e:
        console.print(f"\n[red]❌ Could not run Docker: {e}[/red]")
        raise

    if result.returncode != 0:
        # Check if it's a "compose not found" vs actual compose error
        stderr = result.stderr or ""
        if "is not a docker command" in stderr or "docker: 'compose'" in stderr:
            # Try standalone docker-compose
            dc = _resolve_docker_compose_v1()
            if dc:
                cmd = [dc, *args]
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
    """Pre-pull Ollama models. Model names are sanitized to safe chars (no shell injection)."""
    safe_model_re = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
    for model in models:
        model = (model or "").strip()
        if not model or not safe_model_re.match(model):
            console.print(f"  [dim]Skipping invalid model name: {model!r}[/dim]")
            continue
        console.print(f"  📥 Pulling {model}...")
        subprocess.run(
            [_resolve_docker_cli(), "exec", "dave-it-guy-ollama", "ollama", "pull", model],
            check=True,
        )
