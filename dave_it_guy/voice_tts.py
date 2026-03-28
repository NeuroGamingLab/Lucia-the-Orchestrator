"""
Text-to-speech helpers for Dave voice mode (optional).

Uses macOS `say` when available; falls back to `spd-say` / `espeak` on Linux.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

MAX_TTS_CHARS = 4500
_tts_unavailable_logged = False

# Hand-demo TTS runs in a background thread; track child proc so ESC / window close can kill it.
_hand_speech_lock = threading.Lock()
_hand_speech_proc: subprocess.Popen | None = None


def _sanitize_for_speech(text: str) -> str:
    """Plain text for `say`: strip markdown-ish noise and normalize quotes."""
    s = text.strip()
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"^#+\s*", "", s, flags=re.MULTILINE)
    s = re.sub(r"^\s*[-*]\s+", "", s, flags=re.MULTILINE)
    for a, b in (
        ("\u2019", "'"),
        ("\u2018", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2013", "-"),
        ("\u2014", "-"),
        ("\u2026", "..."),
        ("\u00a0", " "),
    ):
        s = s.replace(a, b)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:MAX_TTS_CHARS]


def _hand_demo_voice_name() -> str | None:
    """
    Voice for hand-interaction `say`, aligned with ``speak_text`` / ``dave-it-guy voice``:

    * If ``DAVE_TTS_VOICE`` is set in the environment → same rules as ``_macos_say_voice_name``
      (empty string → system default / no ``-v``).
    * Else → ``DAVE_HAND_TTS_VOICE`` if set (hand-only override).
    * Else → ``None`` (system default voice, same as voice CLI with no env).
    """
    if "DAVE_TTS_VOICE" in os.environ:
        raw = os.environ.get("DAVE_TTS_VOICE", "").strip()
        return raw if raw else None
    return (os.environ.get("DAVE_HAND_TTS_VOICE") or "").strip() or None


def _hand_demo_say_argv(say_exe: str, utterance: str) -> list[str]:
    """
    macOS ``say`` argv for hand demo: like ``speak_text`` (optional ``-v``, no default ``-r``).

    * Voice: ``_hand_demo_voice_name()`` — matches ``dave-it-guy voice`` when env matches.
    * Rate: only if ``DAVE_HAND_TTS_RATE`` is digits (optional; voice CLI has no ``-r``).
    """
    vn = _hand_demo_voice_name()
    rate_raw = (os.environ.get("DAVE_HAND_TTS_RATE") or "").strip()
    rate = rate_raw if rate_raw.isdigit() else None

    argv: list[str] = [say_exe]
    if vn:
        argv.extend(["-v", vn])
    if rate:
        argv.extend(["-r", rate])
    argv.append(utterance)
    return argv


def _run_hand_demo_speech(argv: list[str]) -> int:
    """
    Run TTS subprocess for hand demo; subprocess is killable via stop_hand_demo_speech().
    Unlike _run_speech_interruptible, always uses Popen so ESC/window close can terminate speech.
    """
    global _hand_speech_proc
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with _hand_speech_lock:
        if _hand_speech_proc is not None:
            _stop_proc(_hand_speech_proc)
        _hand_speech_proc = proc
    try:
        if sys.stdin.isatty():
            t = threading.Thread(target=_interrupt_listener, args=(proc,), daemon=True)
            t.start()
        return proc.wait() or 0
    finally:
        with _hand_speech_lock:
            if _hand_speech_proc is proc:
                _hand_speech_proc = None


def stop_hand_demo_speech() -> None:
    """Stop any in-progress hand-demo readout (call when quitting ESC/q or closing the window)."""
    global _hand_speech_proc
    with _hand_speech_lock:
        p = _hand_speech_proc
        if p is None:
            return
        _hand_speech_proc = None
    _stop_proc(p)


def speak_hand_demo_output(text: str, *, enabled: bool = True) -> bool:
    """
    Read aloud job/container output in the hand-interaction demo.

    Uses the same macOS ``say`` voice selection as ``speak_text`` / ``dave-it-guy voice`` by default
    (system default when no voice env). Optional ``DAVE_HAND_TTS_VOICE`` if ``DAVE_TTS_VOICE`` is
    not set; optional ``DAVE_HAND_TTS_RATE`` (digits only) to add ``-r`` words per minute.
    """
    if not enabled or not (text and text.strip()):
        return False
    utterance = _sanitize_for_speech(text)
    if not utterance:
        return False

    if sys.platform == "darwin":
        say = shutil.which("say")
        if say:
            argv = _hand_demo_say_argv(say, utterance)
            _run_hand_demo_speech(argv)
            return True

    for name in ("spd-say", "espeak"):
        exe = shutil.which(name)
        if exe:
            _run_hand_demo_speech([exe, utterance])
            return True
    return False


def _macos_say_voice_name() -> str | None:
    """
    Voice name for `say -v`, or None to omit `-v` (original behavior: macOS system default).

    * Env `DAVE_TTS_VOICE` unset → None (no `-v`, same as pre–voice-override code).
    * Env set to empty → None (system default).
    * Otherwise → that voice name (e.g. Tom, Daniel, Alex).
    """
    if "DAVE_TTS_VOICE" not in os.environ:
        return None
    raw = os.environ["DAVE_TTS_VOICE"]
    if not str(raw).strip():
        return None
    return str(raw).strip()


def _stop_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        proc.kill()


def _interrupt_listener(proc: subprocess.Popen) -> None:
    """While *proc* runs, any keypress on stdin stops speech (TTY only)."""
    try:
        if sys.platform == "win32":
            import msvcrt

            while proc.poll() is None:
                if msvcrt.kbhit():
                    msvcrt.getch()
                    break
                time.sleep(0.04)
        else:
            if not sys.stdin.isatty():
                return
            import select

            while proc.poll() is None:
                r, _, _ = select.select([sys.stdin], [], [], 0.2)
                if r:
                    try:
                        sys.stdin.read(1)
                    except OSError:
                        pass
                    break
    except Exception:
        pass
    finally:
        _stop_proc(proc)


def _run_speech_interruptible(argv: list[str]) -> int:
    """Run TTS subprocess; return exit code. Press any key (TTY) to interrupt."""
    if not sys.stdin.isatty():
        r = subprocess.run(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return r.returncode or 0

    proc = subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    t = threading.Thread(target=_interrupt_listener, args=(proc,), daemon=True)
    t.start()
    return proc.wait()


def speak_text(text: str, *, enabled: bool = True) -> bool:
    """
    Speak text aloud. Returns True if a TTS command ran, False if skipped or no engine.

    While speaking, **press any key** to interrupt (interactive TTY only).
    """
    global _tts_unavailable_logged

    if not enabled or not (text and text.strip()):
        return False

    utterance = text.strip()[:MAX_TTS_CHARS]

    if sys.platform == "darwin":
        say = shutil.which("say")
        if say:
            vn = _macos_say_voice_name()
            argv = [say, "-v", vn, utterance] if vn else [say, utterance]
            _run_speech_interruptible(argv)
            return True

    for name in ("spd-say", "espeak"):
        exe = shutil.which(name)
        if exe:
            _run_speech_interruptible([exe, utterance])
            return True

    if enabled and not _tts_unavailable_logged:
        print(
            "[dim]TTS: no speech engine found (macOS: `say`; Linux: `spd-say` or `espeak`).[/dim]"
        )
        _tts_unavailable_logged = True
    return False


def format_jobs_for_tts(job_ids: list[str]) -> str:
    """Plain text for reading the Jobs panel aloud."""
    if not job_ids:
        return "No jobs."
    if len(job_ids) == 1:
        return f"One job: {job_ids[0]}."
    if len(job_ids) <= 8:
        return f"{len(job_ids)} jobs: " + ", ".join(job_ids) + "."
    return (
        f"{len(job_ids)} jobs. First five: "
        + ", ".join(job_ids[:5])
        + ". Say Dave list for the full list."
    )


def format_job_result_for_tts(data: dict[str, Any]) -> str:
    """Short spoken summary of a subagent status response."""
    status = (data.get("status") or "").lower()
    if status == "failed":
        err = str(data.get("error") or "unknown error")[:400]
        return f"Task failed. {err}"

    if status == "completed":
        result = data.get("result") or {}
        out = str(result.get("output") or "").strip()
        if not out:
            return "Task completed."
        if len(out) > 600:
            out = out[:600] + " ... truncated."
        return f"Task completed. {out}"

    return f"Status: {status or 'unknown'}."


def format_status_panel_for_tts(data: dict[str, Any]) -> str:
    """Spoken summary for Dave status command."""
    return format_job_result_for_tts(data)


def format_cleanup_for_tts(data: dict[str, Any]) -> str:
    parts = [
        f"Removed {data.get('removed_containers', 0)} containers.",
        f"Removed {data.get('removed_task_dirs', 0)} task folders.",
    ]
    return "Cleanup done. " + " ".join(parts)


def prepare_spoken_job_result(data: dict[str, Any], *, summarize: bool = False) -> str:
    """
    Text for TTS after a sub-agent job completes or fails.

    If *summarize* is True and the completed output is long, call Ollama to compress
    for speech (see voice_summarize).
    """
    base = format_job_result_for_tts(data)
    if not summarize:
        return base

    status = (data.get("status") or "").lower()
    if status != "completed":
        return base

    result = data.get("result") or {}
    out = str(result.get("output") or "").strip()
    from dave_it_guy.voice_summarize import MIN_CHARS_TO_SUMMARIZE, summarize_for_voice

    if len(out) < MIN_CHARS_TO_SUMMARIZE:
        return base

    try:
        return summarize_for_voice(out)
    except Exception:
        return base
