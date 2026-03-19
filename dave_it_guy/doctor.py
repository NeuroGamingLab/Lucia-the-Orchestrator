"""Dave IT Guy Doctor — diagnose common issues."""

from __future__ import annotations

import subprocess

from rich.console import Console
from rich.table import Table

from dave_it_guy.deploy import _docker_cli_path

console = Console()


def run_doctor() -> None:
    """Run diagnostic checks."""
    console.print("[bold]🩺 Dave IT Guy Doctor[/bold]\n")

    checks = [
        ("Docker installed", _check_docker),
        ("Docker Compose installed", _check_compose),
        ("Docker daemon running", _check_docker_running),
        ("Disk space (>5GB free)", _check_disk_space),
        ("Port 18789 available", lambda: _check_port(18789)),
        ("Port 11434 available", lambda: _check_port(11434)),
    ]

    table = Table(show_header=True, header_style="bold")
    table.add_column("Check", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Details")

    all_pass = True
    for name, check_fn in checks:
        try:
            ok, detail = check_fn()
            status = "[green]✅ Pass[/green]" if ok else "[red]❌ Fail[/red]"
            if not ok:
                all_pass = False
        except Exception as e:
            status = "[red]❌ Error[/red]"
            detail = str(e)
            all_pass = False

        table.add_row(name, status, detail)

    console.print(table)
    console.print()

    if all_pass:
        console.print("[bold green]All checks passed! Ready to deploy. 🐙[/bold green]")
    else:
        console.print(
            "[bold yellow]Some checks failed. Fix the issues above before deploying.[/bold yellow]"
        )


def _check_docker() -> tuple[bool, str]:
    docker_bin = _docker_cli_path()
    if not docker_bin:
        return (
            False,
            "Docker CLI not on PATH. Install Docker Desktop (macOS) or docker.io (Linux). "
            "On macOS, Cursor’s terminal may need the same PATH as iTerm (see Docker.app bin).",
        )
    result = subprocess.run([docker_bin, "--version"], capture_output=True, text=True)
    if result.returncode == 0:
        return True, result.stdout.strip()
    return False, result.stderr.strip() or "docker --version failed"


def _check_compose() -> tuple[bool, str]:
    docker_bin = _docker_cli_path()
    if not docker_bin:
        return False, "Docker not found (cannot check Compose)."
    result = subprocess.run([docker_bin, "compose", "version"], capture_output=True, text=True)
    if result.returncode == 0:
        return True, result.stdout.strip()
    return False, "Docker Compose not found. Install: https://docs.docker.com/compose/install/"


def _check_docker_running() -> tuple[bool, str]:
    docker_bin = _docker_cli_path()
    if not docker_bin:
        return False, "Docker not found."
    result = subprocess.run([docker_bin, "info"], capture_output=True, text=True)
    if result.returncode == 0:
        return True, "Docker daemon is running"
    return False, "Docker daemon not running. Start with: sudo systemctl start docker"


def _check_disk_space() -> tuple[bool, str]:
    import shutil as sh
    total, used, free = sh.disk_usage("/")
    free_gb = free / (1024**3)
    if free_gb >= 5:
        return True, f"{free_gb:.1f} GB free"
    return False, f"Only {free_gb:.1f} GB free. Need at least 5 GB."


def _check_port(port: int) -> tuple[bool, str]:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        result = s.connect_ex(("localhost", port))
        if result != 0:
            return True, f"Port {port} is available"
        return False, f"Port {port} is in use"
