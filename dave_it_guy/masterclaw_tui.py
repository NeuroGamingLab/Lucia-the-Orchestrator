"""
MasterClaw TUI — interactive terminal UI that talks to the MasterClaw API.
Create sub-agent tasks (lightweight or full OpenClaw), poll status, view results.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.table import Table

console = Console()
# Serialize Rich output when background thread prints completion panel.
_console_lock = threading.Lock()

DEFAULT_URL = "http://localhost:8090"
POLL_INTERVAL = 2
MAX_POLL_SECONDS = 600
# Cap result text in background panel for terminal usability.
MAX_RESULT_CHARS = 12000


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


def delete_all_jobs_and_subagents(base_url: str) -> None:
    """Delete all task records and all openclaw-subagent containers."""
    confirm = Prompt.ask(
        "[bold red]Option 4B: Delete ALL tasks and ALL sub-agent containers?[/bold red]",
        choices=["y", "n"],
        default="n",
    )
    if confirm != "y":
        console.print("[dim]Cancelled.[/dim]")
        return
    second_confirm = Prompt.ask(
        "[bold red]Type y again to confirm destructive cleanup[/bold red]",
        choices=["y", "n"],
        default="n",
    )
    if second_confirm != "y":
        console.print("[dim]Cancelled.[/dim]")
        return
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.delete(f"{_api(base_url)}/subagent")
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return
    body = (
        f"Removed containers: [bold]{data.get('removed_containers', 0)}[/bold]\n"
        f"Failed containers: [bold]{data.get('failed_containers', 0)}[/bold]\n"
        f"Removed task dirs: [bold]{data.get('removed_task_dirs', 0)}[/bold]\n"
        f"Failed task dirs: [bold]{data.get('failed_task_dirs', 0)}[/bold]"
    )
    console.print(Panel(body, title="Option 4B cleanup result", border_style="green"))


def poll_until_done(base_url: str, job_id: str) -> None:
    """Poll job until completed/failed and show result."""
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


def _poll_interactive_completion_background(base_url: str, job_id: str) -> None:
    """
    Poll MasterClaw until job completes or fails; print result in a second panel.
    Runs in a daemon thread so the main menu is not blocked.
    """
    deadline = time.monotonic() + MAX_POLL_SECONDS
    api = _api(base_url)
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{api}/subagent/{job_id}")
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            with _console_lock:
                console.print(
                    Panel(
                        f"Job: [bold]{job_id}[/bold]\n[red]Poll error: {e}[/red]",
                        title="Sub-agent result (background)",
                        border_style="red",
                    )
                )
            return
        status = data.get("status", "")
        if status == "completed":
            result = data.get("result") or {}
            output = result.get("output", "") or ""
            if len(output) > MAX_RESULT_CHARS:
                output = output[:MAX_RESULT_CHARS] + "\n\n[dim]… (truncated)[/dim]"
            body = (
                f"Job: [bold]{job_id}[/bold]\n"
                f"Status: [green]completed[/green]\n\n"
                f"[green]Result:[/green]\n{output}"
            )
            with _console_lock:
                console.print(
                    Panel(body, title="Sub-agent result (background)", border_style="green")
                )
            return
        if status == "failed":
            err = data.get("error") or "unknown error"
            with _console_lock:
                console.print(
                    Panel(
                        f"Job: [bold]{job_id}[/bold]\nStatus: [red]failed[/red]\n\n[red]{err}[/red]",
                        title="Sub-agent result (background)",
                        border_style="red",
                    )
                )
            return
        time.sleep(POLL_INTERVAL)
    with _console_lock:
        console.print(
            Panel(
                f"Job: [bold]{job_id}[/bold]\n[yellow]Timed out waiting for completion "
                f"({MAX_POLL_SECONDS}s). Use option 3 to check status.[/yellow]",
                title="Sub-agent result (background)",
                border_style="yellow",
            )
        )


def main(url: str = DEFAULT_URL) -> None:
    """Run the MasterClaw TUI loop."""
    console.print(Panel(
        f"[bold]MasterClaw TUI[/bold]\nAPI: [cyan]{url}[/cyan]",
        border_style="blue",
    ))
    while True:
        console.print()
        console.print("  [bold]0[/bold] Gesture interaction (full OpenClaw container)")
        console.print("  [bold]1[/bold] Create task (lightweight worker)")
        console.print("  [bold]2[/bold] Create task (full OpenClaw container)")
        console.print("  [bold]3[/bold] Get job status")
        console.print("  [bold]4[/bold] List jobs (4B: cleanup all tasks/sub-agents)")
        console.print("  [bold]5[/bold] Exit")
        choice = IntPrompt.ask("[bold]Choice (0-5)[/bold]", default=1)
        if choice == 5:
            console.print("[dim]Bye.[/dim]")
            break
        if choice == 0:
            try:
                import cv2  # noqa: F401
            except Exception as e:
                console.print(
                    Panel(
                        "Gesture interaction (full OpenClaw container) is not available.\n\n"
                        f"[red]{e}[/red]\n\n"
                        "[dim]Tip: install demo deps and run again:[/dim]\n"
                        '[dim]  pip install "dave-it-guy[hand]"[/dim]\n'
                        "[dim]You can also run: python3 -m dave_it_guy.examples.hand_interaction[/dim]",
                        title="Gesture interaction (full OpenClaw container)",
                        border_style="yellow",
                    )
                )
                continue
            log_dir = Path.home() / ".dave_it_guy" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "hand_interaction.log"
            log_f = open(log_path, "a", encoding="utf-8", buffering=1)
            log_f.write(
                f"\n--- hand_interaction {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n"
            )
            log_f.flush()
            popen_kw: dict = {
                "args": [sys.executable, "-m", "dave_it_guy.examples.hand_interaction"],
                "stdout": log_f,
                "stderr": subprocess.STDOUT,
            }
            if sys.platform != "win32":
                popen_kw["start_new_session"] = True
            try:
                proc = subprocess.Popen(**popen_kw)
                log_f.write(f"spawned pid={proc.pid}\n")
                log_f.flush()
            except Exception as e:
                try:
                    log_f.write(f"spawn failed: {e!s}\n")
                finally:
                    log_f.close()
                console.print(
                    Panel(
                        f"Could not start gesture interaction process.\n\n[red]{e}[/red]",
                        title="Gesture interaction (full OpenClaw container)",
                        border_style="red",
                    )
                )
                continue
            console.print(
                Panel(
                    "Gesture interaction (full OpenClaw container) started in the background "
                    "(separate process).\n\n"
                    f"[dim]Log file (errors + output):[/dim] [cyan]{log_path}[/cyan]\n"
                    "[dim]If no camera window appears, run: [/dim][bold]tail -f "
                    f"{log_path}[/bold]\n\n"
                    "[dim]macOS: grant [bold]Camera[/bold] (and sometimes [bold]Microphone[/bold]) "
                    "to your terminal app in System Settings → Privacy.\n"
                    "Or run in a separate terminal:[/dim]\n"
                    "[bold]python3 -m dave_it_guy.examples.hand_interaction[/bold]\n\n"
                    "[dim]Close the camera window or press q / ESC there. "
                    "You can keep using options 1–5 here.[/dim]",
                    title="Gesture interaction (full OpenClaw container)",
                    border_style="green",
                )
            )
            continue
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
                        f"[dim]Initial task result will appear in a second panel when ready "
                        f"(non-blocking).[/dim]",
                        title="Sub-agent interactive",
                        border_style="green",
                    )
                )
                threading.Thread(
                    target=_poll_interactive_completion_background,
                    args=(url, job_id),
                    daemon=True,
                    name=f"masterclaw-poll-{job_id[:8]}",
                ).start()
        elif choice == 3:
            get_status(url)
        elif choice == 4:
            list_jobs(url)
            if Prompt.ask("Run option 4B cleanup now?", choices=["y", "n"], default="n") == "y":
                delete_all_jobs_and_subagents(url)
        else:
            console.print("[yellow]Choose 0–5.[/yellow]")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    main(url)
