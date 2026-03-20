"""
MasterClaw TUI — interactive terminal UI that talks to the MasterClaw API.
Create sub-agent tasks (lightweight or full OpenClaw), poll status, view results.
"""

from __future__ import annotations

import sys
from typing import Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt
from rich.table import Table

console = Console()

DEFAULT_URL = "http://localhost:8090"
POLL_INTERVAL = 2
MAX_POLL_SECONDS = 600


def _api(base_url: str) -> str:
    return base_url.rstrip("/")


def create_task(base_url: str, use_full_openclaw: bool, interactive: bool = False) -> Optional[str]:
    """Create a sub-agent job. Returns job_id or None."""
    task = Prompt.ask("[bold cyan]Task[/bold cyan]")
    if not task.strip():
        console.print("[yellow]Task cannot be empty.[/yellow]")
        return None
    context = Prompt.ask(
        "[dim]Context (optional)[/dim]",
        default="",
        show_default=False,
    )
    model = "llama3.2"
    if not use_full_openclaw:
        model = Prompt.ask(
            "[dim]Ollama model[/dim]",
            default="llama3.2",
        )
    payload = {
        "task": task.strip(),
        "context": context.strip() or None,
        "model": model,
        "timeout_seconds": 300,
        "use_full_openclaw": use_full_openclaw,
        "interactive": interactive,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(f"{_api(base_url)}/subagent", json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return None
    job_id = data.get("job_id")
    status = data.get("status", "")
    if not job_id:
        console.print("[red]No job_id in response.[/red]")
        return None
    console.print(Panel(
        f"[green]Job created:[/green] [bold]{job_id}[/bold]\nStatus: {status}",
        title="Sub-agent",
        border_style="green",
    ))
    return job_id


def get_status(base_url: str, job_id: Optional[str] = None) -> None:
    """Get status (and result) for a job."""
    if not job_id:
        job_id = Prompt.ask("[bold cyan]Job ID[/bold cyan]")
    if not job_id.strip():
        return
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{_api(base_url)}/subagent/{job_id.strip()}")
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return
    status = data.get("status", "?")
    result = data.get("result")
    error = data.get("error")
    lines = [f"Job: [bold]{job_id}[/bold]", f"Status: [cyan]{status}[/cyan]"]
    if result:
        output = result.get("output", "")
        if output:
            lines.append("\n[green]Result:[/green]")
            lines.append(output)
    if error:
        lines.append(f"\n[red]Error: {error}[/red]")
    console.print(Panel("\n".join(lines), title="Sub-agent status", border_style="cyan"))


def list_jobs(base_url: str) -> None:
    """List recent job IDs."""
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{_api(base_url)}/subagent")
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return
    job_ids = data.get("job_ids", [])
    if not job_ids:
        console.print("[dim]No jobs yet.[/dim]")
        return
    table = Table(title="Recent jobs")
    table.add_column("Job ID", style="cyan")
    for j in job_ids[:20]:
        table.add_row(j)
    console.print(table)


def poll_until_done(base_url: str, job_id: str) -> None:
    """Poll job until completed/failed and show result."""
    import time
    deadline = time.monotonic() + MAX_POLL_SECONDS
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{_api(base_url)}/subagent/{job_id}")
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            return
        status = data.get("status", "")
        if status == "completed":
            console.print("[green]Completed.[/green]")
            get_status(base_url, job_id)
            return
        if status == "failed":
            console.print("[red]Failed.[/red]")
            get_status(base_url, job_id)
            return
        console.print(f"  Status: [dim]{status}[/dim] ...")
        time.sleep(POLL_INTERVAL)
    console.print("[yellow]Timed out waiting for result.[/yellow]")


def main(url: str = DEFAULT_URL) -> None:
    """Run the MasterClaw TUI loop."""
    console.print(Panel(
        f"[bold]MasterClaw TUI[/bold]\nAPI: [cyan]{url}[/cyan]",
        border_style="blue",
    ))
    while True:
        console.print()
        console.print("  [bold]1[/bold] Create task (lightweight worker)")
        console.print("  [bold]2[/bold] Create task (full OpenClaw container)")
        console.print("  [bold]3[/bold] Get job status")
        console.print("  [bold]4[/bold] List jobs")
        console.print("  [bold]5[/bold] Exit")
        choice = IntPrompt.ask("[bold]Choice (1-5)[/bold]", default=1)
        if choice == 5:
            console.print("[dim]Bye.[/dim]")
            break
        if choice == 1:
            job_id = create_task(url, use_full_openclaw=False)
            if job_id and Prompt.ask("Poll until done?", choices=["y", "n"], default="y") == "y":
                poll_until_done(url, job_id)
        elif choice == 2:
            keep_running = (
                Prompt.ask(
                    "Option B: keep full OpenClaw container running for attach?",
                    choices=["y", "n"],
                    default="n",
                )
                == "y"
            )
            job_id = create_task(url, use_full_openclaw=True, interactive=keep_running)
            if job_id and not keep_running:
                if Prompt.ask("Poll until done?", choices=["y", "n"], default="y") == "y":
                    poll_until_done(url, job_id)
            elif job_id and keep_running:
                container_name = f"openclaw-subagent-{job_id}"
                console.print(
                    Panel(
                        f"Job created (interactive).\nJob ID: [bold]{job_id}[/bold]\n"
                        f"Attach: docker exec -it {container_name} openclaw tui\n"
                        f"Status: use option 3 to check progress later.",
                        title="Sub-agent interactive",
                        border_style="green",
                    )
                )
        elif choice == 3:
            get_status(url)
        elif choice == 4:
            list_jobs(url)
        else:
            console.print("[yellow]Choose 1–5.[/yellow]")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    main(url)
