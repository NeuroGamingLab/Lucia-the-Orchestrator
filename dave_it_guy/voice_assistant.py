"""
Dave voice assistant — wake word "Dave" → speech-to-text → MasterClaw API.

Requires optional deps: pip install "dave-it-guy[voice]"
On macOS you may need: brew install portaudio
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from dave_it_guy.voice_session_memory import (
    VoiceSessionMemory,
    apply_default_job_id_for_status,
    apply_preference_to_need_task,
    try_session_memory_command,
)

# Word-boundary wake: "Dave", "Hey Dave", not "adventure"
_WAKE_RE = re.compile(
    r"\b(?:hey\s+)?dave\b",
    re.IGNORECASE,
)

# Command patterns (after wake + optional punctuation)
_LIGHT_RE = re.compile(
    r"^(?:lightweight|light|worker|one)\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
# Full OpenClaw sub-agent container (same API as "full openclaw" / command two)
_FULL_RE = re.compile(
    r"^(?:full\s+openclaw|openclaw|full\s+container|full|two|container|"
    r"sub\s+agent|sub-agent|subagent|new\s+agent|new\s+container|spawn|sp)\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
# Bare phrases (no task in same utterance) → prompt for full OpenClaw task
# ("container" / "CONTAINER" also handled below in bare single-word keywords)
_FULL_BARE = re.compile(
    r"^(?:sub\s+agent|sub-agent|subagent|new\s+agent|new\s+container|spawn|sp)$",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"^(?:status|job\s+status)(?:\s+([a-f0-9\-]{8,}))?\s*$",
    re.IGNORECASE,
)
_LIST_RE = re.compile(
    r"^(?:list|list\s+jobs|jobs)\s*$",
    re.IGNORECASE,
)
_CLEANUP_RE = re.compile(
    r"^(?:cleanup|clean\s+up|delete\s+all|clear\s+tasks|clear\s+list|four\s+b)\s*$",
    re.IGNORECASE,
)
_EXIT_RE = re.compile(
    r"^(?:exit|quit|stop)\s*$",
    re.IGNORECASE,
)

# TUI-style menu: "command one" … "command five" (same as options 1–5)
_CMD_NUM_RE = re.compile(
    r"^(?:command|option)\s+(?P<num>one|two|three|four|five|[1-5])\b\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_WORD_TO_NUM = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}

# Short replies (no API) — gratitude, hello, small talk after a result
_CONVERSATIONAL_THANKS = re.compile(
    r"^(?:thank\s+you|thanks(?:\s+so\s+much)?|thx|ty|cheers|much\s+appreciated|appreciate\s+it|i\s+appreciate\s+it)"
    r"[\s!.,]*$",
    re.IGNORECASE,
)
_CONVERSATIONAL_GREETING = re.compile(
    r"^(?:hi|hello|hey(?:\s+there)?|good\s+(?:morning|afternoon|evening))[\s!.,]*$",
    re.IGNORECASE,
)
_CONVERSATIONAL_POSITIVE = re.compile(
    r"^(?:nice|cool|awesome|great|perfect|amazing|wonderful|excellent|love\s+it|good\s+job|well\s+done|nice\s+work)"
    r"[\s!.,]*$",
    re.IGNORECASE,
)
_CONVERSATIONAL_BYE = re.compile(
    r"^(?:bye|goodbye|see\s+you|talk\s+to\s+you\s+later|later)[\s!.,]*$",
    re.IGNORECASE,
)

_CONVERSATIONAL_REPLIES: dict[str, list[str]] = {
    "thanks": [
        "You're welcome — happy to help.",
        "Anytime. What's next?",
        "Glad it helped!",
    ],
    "greeting": [
        "Hey! Say what you'd like to run after **Dave**.",
        "Hi there — I'm listening.",
    ],
    "positive": [
        "Nice — want to run another task?",
        "Love to hear it. Need anything else?",
    ],
    "goodbye": [
        "Later! Say **Dave** when you're back.",
        "See you — I'll be here.",
    ],
}


def _parse_conversational(after_wake: str) -> ParsedVoiceCommand | None:
    """Thanks, hi, cool, bye — no MasterClaw call."""
    s = after_wake.strip()
    if not s:
        return None
    if _CONVERSATIONAL_THANKS.match(s):
        return ParsedVoiceCommand(
            kind="conversational",
            conversational_tone="thanks",
            raw_after_wake=s,
        )
    if _CONVERSATIONAL_GREETING.match(s):
        return ParsedVoiceCommand(
            kind="conversational",
            conversational_tone="greeting",
            raw_after_wake=s,
        )
    if _CONVERSATIONAL_POSITIVE.match(s):
        return ParsedVoiceCommand(
            kind="conversational",
            conversational_tone="positive",
            raw_after_wake=s,
        )
    if _CONVERSATIONAL_BYE.match(s):
        return ParsedVoiceCommand(
            kind="conversational",
            conversational_tone="goodbye",
            raw_after_wake=s,
        )
    return None


def _parse_command_number(after_wake: str) -> ParsedVoiceCommand | None:
    """If text is 'command four', 'option two …', etc., map to TUI menu 1–5."""
    m = _CMD_NUM_RE.match(after_wake.strip())
    if not m:
        return None
    raw = m.group("num").lower()
    rest = (m.group("rest") or "").strip()
    try:
        n = _WORD_TO_NUM.get(raw) if raw in _WORD_TO_NUM else int(raw)
    except ValueError:
        return None
    if n not in (1, 2, 3, 4, 5):
        return None
    src = after_wake.strip()

    if n == 1:
        if rest:
            return ParsedVoiceCommand(kind="lightweight", task=rest, raw_after_wake=src)
        return ParsedVoiceCommand(kind="need_task_light", raw_after_wake=src)

    if n == 2:
        if rest:
            return ParsedVoiceCommand(kind="full", task=rest, raw_after_wake=src)
        return ParsedVoiceCommand(kind="need_task_full", raw_after_wake=src)

    if n == 3:
        # Optional job id after "command three …" (UUID or fragment from STT)
        if rest:
            return ParsedVoiceCommand(kind="status", job_id=rest, raw_after_wake=src)
        return ParsedVoiceCommand(kind="status", job_id=None, raw_after_wake=src)

    if n == 4:
        return ParsedVoiceCommand(kind="list", raw_after_wake=src)

    if n == 5:
        return ParsedVoiceCommand(kind="exit", raw_after_wake=src)

    return None


@dataclass
class ParsedVoiceCommand:
    """Result of parsing transcript after wake word."""

    kind: Literal[
        "lightweight",
        "full",
        "status",
        "list",
        "cleanup",
        "exit",
        "need_task_light",
        "need_task_full",
        "conversational",
        "unknown",
        "memory_hint",
    ]
    task: str | None = None
    job_id: str | None = None
    raw_after_wake: str = ""
    conversational_tone: Literal["thanks", "greeting", "positive", "goodbye"] | None = None
    memory_hint: str | None = None


def extract_after_wake(transcript: str) -> str | None:
    """
    Return text after the wake phrase 'Dave' / 'Hey Dave'.
    None if wake word not present as a whole word.
    """
    s = transcript.strip()
    m = _WAKE_RE.search(s)
    if not m:
        return None
    return s[m.end() :].strip(" ,!:").strip()


def normalize_task_instruction(transcript: str) -> str:
    """
    Use full STT text as the task. If the user still said 'Dave …', strip the wake
    and keep only the instruction part.
    """
    s = transcript.strip()
    if not s:
        return s
    after = extract_after_wake(s)
    if after is not None and after.strip():
        return after.strip()
    return s


def resolve_voice_command(after_wake: str, mem: VoiceSessionMemory) -> ParsedVoiceCommand:
    """Apply session-memory phrases, then normal parsing, then status/job defaults."""
    m = try_session_memory_command(after_wake, mem)
    cmd = m if m is not None else parse_voice_command(after_wake)
    cmd = apply_default_job_id_for_status(cmd, mem)
    cmd = apply_preference_to_need_task(cmd, mem)
    return cmd


def parse_voice_command(after_wake: str) -> ParsedVoiceCommand:
    """Map spoken text (already after 'Dave') to a command."""
    s = after_wake.strip()
    if not s:
        return ParsedVoiceCommand(kind="unknown", raw_after_wake=s)

    cmd_num = _parse_command_number(s)
    if cmd_num is not None:
        return cmd_num

    conv = _parse_conversational(s)
    if conv is not None:
        return conv

    if _EXIT_RE.match(s):
        return ParsedVoiceCommand(kind="exit", raw_after_wake=s)

    if _LIST_RE.match(s):
        return ParsedVoiceCommand(kind="list", raw_after_wake=s)

    if _CLEANUP_RE.match(s):
        return ParsedVoiceCommand(kind="cleanup", raw_after_wake=s)

    sm = _STATUS_RE.match(s)
    if sm:
        jid = sm.group(1)
        return ParsedVoiceCommand(kind="status", job_id=jid, raw_after_wake=s)

    lm = _LIGHT_RE.match(s)
    if lm:
        task = lm.group(1).strip()
        if task:
            return ParsedVoiceCommand(kind="lightweight", task=task, raw_after_wake=s)
        return ParsedVoiceCommand(kind="need_task_light", raw_after_wake=s)

    fm = _FULL_RE.match(s)
    if fm:
        task = fm.group(1).strip()
        if task:
            return ParsedVoiceCommand(kind="full", task=task, raw_after_wake=s)
        return ParsedVoiceCommand(kind="need_task_full", raw_after_wake=s)

    # Bare keywords without task: "lightweight" / "full" / "sub agent" alone
    low = s.lower()
    if low in ("lightweight", "light", "worker", "one"):
        return ParsedVoiceCommand(kind="need_task_light", raw_after_wake=s)
    if low in ("full", "openclaw", "full openclaw", "container", "two"):
        return ParsedVoiceCommand(kind="need_task_full", raw_after_wake=s)
    if _FULL_BARE.match(s.strip()):
        return ParsedVoiceCommand(kind="need_task_full", raw_after_wake=s)
    if low.startswith("status"):
        return ParsedVoiceCommand(kind="status", job_id=None, raw_after_wake=s)

    return ParsedVoiceCommand(kind="unknown", raw_after_wake=s)


def masterclaw_base(url: str) -> str:
    return url.rstrip("/")


def api_create_task(
    base_url: str,
    *,
    task: str,
    context: str | None,
    use_full_openclaw: bool,
    interactive: bool,
    model: str = "llama3.2",
    timeout_seconds: int = 300,
) -> dict:
    payload = {
        "task": task,
        "context": context or None,
        "model": model,
        "timeout_seconds": timeout_seconds,
        "use_full_openclaw": use_full_openclaw,
        "interactive": interactive,
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.post(f"{masterclaw_base(base_url)}/subagent", json=payload)
        r.raise_for_status()
        return r.json()


def api_get_status(base_url: str, job_id: str) -> dict:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(f"{masterclaw_base(base_url)}/subagent/{job_id.strip()}")
        r.raise_for_status()
        return r.json()


def api_list_jobs(base_url: str) -> dict:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(f"{masterclaw_base(base_url)}/subagent")
        r.raise_for_status()
        return r.json()


def api_cleanup(base_url: str) -> dict:
    with httpx.Client(timeout=120.0) as client:
        r = client.delete(f"{masterclaw_base(base_url)}/subagent")
        r.raise_for_status()
        return r.json()


def api_health(base_url: str) -> bool:
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{masterclaw_base(base_url)}/health")
            return r.status_code == 200
    except Exception:
        return False


# Longer windows when we're only waiting for task text (no wake word required).
TASK_LISTEN_TIMEOUT = 22.0
TASK_PHRASE_TIME_LIMIT = 90.0
# Slightly longer pause before end-of-phrase (seconds) for task dictation vs main loop.
TASK_PAUSE_THRESHOLD = 1.35
TASK_LISTEN_MAX_ATTEMPTS = 24
TASK_LISTEN_MAX_CHUNKS = 15


def _is_done_only_utterance(text: str) -> bool:
    """Single segment that means 'finish' (no task content)."""
    s = text.strip().lower().rstrip(".!?")
    if not s:
        return False
    if s == "done":
        return True
    if s in ("ok done", "okay done", "i'm done", "im done", "that's done", "thats done"):
        return True
    if re.match(r"^(?:ok|okay)\s+done$", s):
        return True
    return False


def _ends_with_done_word(text: str) -> bool:
    """True if the utterance ends with the word 'done' as a terminator."""
    return bool(re.search(r"\bdone\b\s*[.!?]*\s*$", text.strip(), re.IGNORECASE))


def _strip_trailing_done(text: str) -> str:
    """Remove a trailing 'done' phrase (keep prior words)."""
    return re.sub(r"\s*\bdone\b\s*[.!?]*\s*$", "", text.strip(), flags=re.IGNORECASE).strip()


def listen_once_transcript(timeout: float = 12.0, phrase_time_limit: float = 25.0) -> str | None:
    """Capture one utterance and return text, or None on failure."""
    import speech_recognition as sr

    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.adjust_for_ambient_noise(source, duration=0.35)
        try:
            audio = r.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        except sr.WaitTimeoutError:
            return None

    try:
        return r.recognize_google(audio)
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        raise RuntimeError(f"Speech recognition service error: {e}") from e


def listen_task_instruction_multipart(
    *,
    console: Any | None = None,
    emit: Callable[[str], None] | None = None,
    print_heard: bool = True,
    pause_threshold: float = TASK_PAUSE_THRESHOLD,
    timeout_per_chunk: float = TASK_LISTEN_TIMEOUT,
    phrase_limit_per_chunk: float = TASK_PHRASE_TIME_LIMIT,
    max_chunks: int = TASK_LISTEN_MAX_CHUNKS,
    max_attempts: int = TASK_LISTEN_MAX_ATTEMPTS,
) -> str | None:
    """
    Capture task text across multiple utterances until the user says **done** (or a
    segment ends with '… done'), or until a listen timeout with text already captured.

    Uses a higher *pause_threshold* so short breaths are less likely to cut a segment.

    Pass *emit* for plain-text lines (e.g. OpenCV overlay), or *console* for Rich
    (voice CLI). One of *emit* or *console* is required.
    """
    import speech_recognition as sr

    def _out(msg: str) -> None:
        if emit is not None:
            emit(msg)
        elif console is not None:
            console.print(msg)
        else:
            raise ValueError("listen_task_instruction_multipart requires emit= or console=")

    r = sr.Recognizer()
    r.pause_threshold = pause_threshold

    chunks: list[str] = []
    attempts = 0

    with sr.Microphone() as source:
        r.adjust_for_ambient_noise(source, duration=0.4)
        while attempts < max_attempts and len(chunks) < max_chunks:
            attempts += 1
            try:
                audio = r.listen(
                    source,
                    timeout=timeout_per_chunk,
                    phrase_time_limit=phrase_limit_per_chunk,
                )
            except sr.WaitTimeoutError:
                if chunks:
                    break
                return None

            try:
                text = r.recognize_google(audio)
            except sr.UnknownValueError:
                if chunks:
                    _out(
                        "Could not understand that bit — say done to finish, or repeat."
                    )
                continue
            except sr.RequestError as e:
                raise RuntimeError(f"Speech recognition service error: {e}") from e

            text = (text or "").strip()
            if not text:
                continue

            if print_heard:
                _out(f"Heard (segment {len(chunks) + 1}): {text}")

            if _is_done_only_utterance(text):
                if chunks:
                    break
                _out("Say your task first, then done when finished.")
                continue

            if _ends_with_done_word(text):
                body = _strip_trailing_done(text)
                if body:
                    chunks.append(body)
                break

            chunks.append(text)
            if len(chunks) >= max_chunks:
                _out("Maximum task segments reached — sending the combined text.")
                break
            _out("Say more to continue, or say done when finished.")

    if not chunks:
        return None
    return " ".join(chunks)


def run_voice_loop(
    base_url: str,
    *,
    allow_cleanup: bool = False,
    once: bool = False,
    interactive_full: bool = False,
    print_heard: bool = True,
    speak: bool = False,
    speak_summarize: bool = False,
    chat_fallback: bool = True,
) -> None:
    """Main loop: listen → transcribe → wake 'Dave' → parse → MasterClaw API."""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    try:
        import speech_recognition as sr  # noqa: F401
    except ImportError as e:
        console.print(
            "[red]Missing voice dependencies.[/red] Install with:\n"
            "  [bold]pip install 'dave-it-guy[voice]'[/bold]\n"
            "On macOS you may also need: [bold]brew install portaudio[/bold]"
        )
        raise SystemExit(1) from e

    if not api_health(base_url):
        console.print(
            f"[red]MasterClaw not reachable at {base_url}[/red]\n"
            "Start the stack: [bold]dave-it-guy deploy openclaw[/bold] "
            "and ensure [bold]dave-it-guy-masterclaw[/bold] is running."
        )
        raise SystemExit(1)

    from dave_it_guy.masterclaw_tui import poll_until_done
    from dave_it_guy.voice_tts import (
        format_cleanup_for_tts,
        format_jobs_for_tts,
        format_status_panel_for_tts,
        prepare_spoken_job_result,
        speak_text,
    )

    def _tts_line(msg: str) -> None:
        speak_text(msg, enabled=speak)

    def _tts_job_result(data: dict) -> None:
        if speak_summarize:
            console.print(
                "[dim]Speech: long completed results are summarized by Ollama first "
                "(the panel above stays full length).[/dim]"
            )
        text = prepare_spoken_job_result(data, summarize=speak_summarize)
        speak_text(text, enabled=speak)

    console.print(
        Panel(
            "[bold]Dave voice assistant[/bold]\n\n"
            "Wake word: [cyan]Dave[/cyan] (say [italic]Hey Dave[/italic] or start with [italic]Dave[/italic])\n\n"
            "Examples:\n"
            "• [dim]Dave lightweight …[/dim] or [dim]Dave command one …[/dim] (lightweight worker)\n"
            "• [dim]Dave full openclaw …[/dim], [dim]Dave command two …[/dim], [dim]Dave sub agent …[/dim], "
            "[dim]Dave new agent …[/dim], [dim]Dave new container …[/dim], "
            "[dim]Dave container …[/dim] / [dim]CONTAINER …[/dim], "
            "[dim]Dave spawn …[/dim] / [dim]Dave sp …[/dim] "
            "(full OpenClaw sub-agent container)\n"
            "• [dim]Dave command three[/dim] (status) or [dim]Dave command three[/dim] + job id\n"
            "• [dim]Dave command four[/dim] (list jobs) · [dim]Dave command five[/dim] (exit)\n"
            "• [dim]Dave list[/dim] · [dim]Dave status[/dim] · [dim]Dave exit[/dim]\n"
            "• [dim]Dave thanks[/dim] / [dim]Dave cool[/dim] — short replies (no task)\n"
            "• [dim]Session memory (this run):[/dim] [dim]repeat last[/dim] · "
            "[dim]remember I prefer lightweight[/dim] / [dim]full openclaw[/dim] · "
            "[dim]remind me after this job to …[/dim] · [dim]forget session[/dim]\n"
            + (
                "[dim]Unrecognized commands can open [bold]chat mode[/bold] via LLM "
                "(disable with [bold]--no-chat-fallback[/bold]).[/dim]\n\n"
                if chat_fallback
                else "[dim]Chat fallback for unknown commands is off.[/dim]\n\n"
            )
            + (
                "[yellow]Cleanup via voice is disabled.[/yellow] Use [bold]dave-it-guy voice --allow-cleanup[/bold] "
                "and say [dim]Dave cleanup[/dim] or [dim]Dave clear list[/dim].\n\n"
                if not allow_cleanup
                else "[yellow]Dave cleanup[/yellow] or [yellow]Dave clear list[/yellow] will delete all sub-agent jobs and containers.\n\n"
            )
            + (
                "\n[dim]Text-to-speech: macOS [bold]say[/bold] uses your [bold]system default[/bold] voice "
                "(original behavior). Set [bold]DAVE_TTS_VOICE[/bold] or [bold]--tts-voice[/bold] for a specific voice. "
                "Press [bold]any key[/bold] to interrupt speech.[/dim]\n"
                + (
                    "\n[dim]Long task results will be [bold]summarized by Ollama[/bold] before speech "
                    "([bold]--summarize-speech[/bold]; needs Ollama at OLLAMA_HOST).[/dim]\n"
                    if speak and speak_summarize
                    else ""
                )
                if speak
                else ""
            ),
            title="Listening",
            border_style="blue",
        )
    )

    from dave_it_guy.voice_chat import chat_with_dave, is_chat_exit_phrase

    mem = VoiceSessionMemory()
    forced_after: str | None = None
    chat_history: list[dict] = []
    in_chat_mode = False

    def _chat_ex(hist: list[dict], msg: str) -> tuple[list[dict], str]:
        extra = mem.build_chat_system_extra()
        return chat_with_dave(hist, msg, system_extra=extra)

    while True:
        cmd: ParsedVoiceCommand | None = None
        after = ""

        if forced_after is not None:
            after = forced_after
            forced_after = None
            cmd = resolve_voice_command(after, mem)
        elif in_chat_mode:
            console.print(
                Panel(
                    "[bold]Chat mode[/bold]\n"
                    "[dim]Speak without the wake word, or say [bold]Dave[/bold] plus a command. "
                    "Say [bold]exit chat[/bold], [bold]done[/bold], or [bold]quit[/bold] to leave.[/dim]",
                    title="Dave",
                    border_style="magenta",
                )
            )
            console.print("\n[cyan]Listening…[/cyan] [dim](chat — no wake word required)[/dim]")
            try:
                text = listen_once_transcript()
            except RuntimeError as e:
                console.print(f"[red]{e}[/red]")
                if once:
                    break
                continue

            if not text:
                console.print("[yellow]Could not understand audio. Try again.[/yellow]")
                if once:
                    break
                continue

            if print_heard:
                console.print(f"[dim]Heard:[/dim] {text}")

            if is_chat_exit_phrase(text):
                in_chat_mode = False
                chat_history.clear()
                console.print("[dim]Left chat mode.[/dim]")
                if once:
                    break
                continue

            aw = extract_after_wake(text)
            if aw is not None:
                c2 = resolve_voice_command(aw, mem)
                if c2.kind != "unknown":
                    in_chat_mode = False
                    chat_history.clear()
                    after = aw
                    cmd = c2
                else:
                    try:
                        chat_history, reply = _chat_ex(chat_history, aw)
                    except Exception as e:
                        console.print(f"[red]Chat failed: {e}[/red]")
                        if once:
                            break
                        continue
                    console.print(Panel(reply, title="Dave", border_style="green"))
                    mem.record_last_panel("Dave (chat)", reply)
                    speak_text(reply, enabled=speak)
                    if once:
                        break
                    continue
            else:
                try:
                    chat_history, reply = _chat_ex(chat_history, text.strip())
                except Exception as e:
                    console.print(f"[red]Chat failed: {e}[/red]")
                    if once:
                        break
                    continue
                console.print(Panel(reply, title="Dave", border_style="green"))
                mem.record_last_panel("Dave (chat)", reply)
                speak_text(reply, enabled=speak)
                if once:
                    break
                continue
        else:
            console.print("\n[cyan]Listening…[/cyan] [dim](speak now)[/dim]")
            try:
                text = listen_once_transcript()
            except RuntimeError as e:
                console.print(f"[red]{e}[/red]")
                if once:
                    break
                continue

            if not text:
                console.print("[yellow]Could not understand audio. Try again.[/yellow]")
                if once:
                    break
                continue

            if print_heard:
                console.print(f"[dim]Heard:[/dim] {text}")

            after = extract_after_wake(text)
            if after is None:
                console.print(
                    "[yellow]Say the wake word [bold]Dave[/bold] first, then your command.[/yellow]"
                )
                if once:
                    break
                continue

            cmd = resolve_voice_command(after, mem)
            if cmd.kind == "unknown" and chat_fallback:
                try:
                    chat_history, reply = _chat_ex([], after)
                except Exception as e:
                    console.print(f"[red]Chat fallback failed: {e}[/red]")
                    console.print(
                        "[yellow]Unknown command.[/yellow] Try: [dim]Dave lightweight …[/dim], "
                        "[dim]Dave full openclaw …[/dim], [dim]Dave list[/dim], [dim]Dave status[/dim]. "
                        "Set API keys / Ollama for conversational help, or use [bold]--no-chat-fallback[/bold]."
                    )
                    if once:
                        break
                    continue
                console.print(Panel(reply, title="Dave", border_style="green"))
                mem.record_last_panel("Dave (chat)", reply)
                speak_text(reply, enabled=speak)
                in_chat_mode = True
                if once:
                    break
                continue
            if cmd.kind == "unknown":
                console.print(
                    "[yellow]Unknown command.[/yellow] Try: [dim]Dave lightweight …[/dim], "
                    "[dim]Dave full openclaw …[/dim], [dim]Dave list[/dim], [dim]Dave status[/dim]."
                )
                if once:
                    break
                continue

        if cmd is None:
            continue

        if cmd.kind == "exit":
            console.print("[dim]Goodbye.[/dim]")
            break

        if cmd.kind == "memory_hint":
            hint = (cmd.memory_hint or "").strip()
            if hint:
                console.print(Panel(hint, title="Session memory", border_style="dim"))
                mem.record_last_panel("Session memory", hint.replace("**", ""))
                speak_text(hint.replace("**", ""), enabled=speak)
            mem.note_command("memory hint")
            if once:
                break
            continue

        if cmd.kind == "conversational":
            tone = cmd.conversational_tone or "thanks"
            replies = _CONVERSATIONAL_REPLIES.get(
                tone, _CONVERSATIONAL_REPLIES["thanks"]
            )
            msg = random.choice(replies)
            console.print(Panel(msg, title="Dave", border_style="green"))
            mem.record_last_panel("Dave", msg)
            speak_text(msg, enabled=speak)
            if once:
                break
            continue

        if cmd.kind == "unknown":
            console.print(
                "[yellow]Unknown command.[/yellow] Try: [dim]Dave lightweight …[/dim], "
                "[dim]Dave full openclaw …[/dim], [dim]Dave list[/dim], [dim]Dave status[/dim]."
            )
            if once:
                break
            continue

        if cmd.kind == "cleanup":
            if not allow_cleanup:
                console.print(
                    "[yellow]Cleanup skipped.[/yellow] Re-run with [bold]--allow-cleanup[/bold] to enable."
                )
            else:
                try:
                    result = api_cleanup(base_url)
                    console.print(Panel(str(result), title="Cleanup", border_style="green"))
                    mem.record_last_panel("Cleanup", str(result))
                    speak_text(format_cleanup_for_tts(result), enabled=speak)
                except Exception as e:
                    console.print(f"[red]Cleanup failed: {e}[/red]")
                    speak_text(f"Cleanup failed: {e}", enabled=speak)
            if once:
                break
            continue

        if cmd.kind == "list":
            try:
                data = api_list_jobs(base_url)
                ids = data.get("job_ids", [])
                console.print(Panel("\n".join(ids) or "(no jobs)", title="Jobs", border_style="cyan"))
                mem.record_last_panel("Jobs", "\n".join(ids) or "(no jobs)")
                speak_text(format_jobs_for_tts(ids), enabled=speak)
            except Exception as e:
                console.print(f"[red]List failed: {e}[/red]")
                speak_text(f"List jobs failed: {e}", enabled=speak)
            if once:
                break
            continue

        if cmd.kind == "status":
            jid = cmd.job_id
            if not jid:
                extra = listen_once_transcript(timeout=8.0, phrase_time_limit=15.0)
                if extra:
                    jid = extra.strip()
                    console.print(f"[dim]Job ID:[/dim] {jid}")
            if not jid:
                console.print("[yellow]Say [bold]Dave status[/bold] followed by job ID, or one more utterance with the ID.[/yellow]")
                if once:
                    break
                continue
            try:
                data = api_get_status(base_url, jid)
                console.print(Panel(str(data), title="Status", border_style="cyan"))
                mem.record_terminal_job_panel(jid, data)
                speak_text(format_status_panel_for_tts(data), enabled=speak)
            except Exception as e:
                console.print(f"[red]Status failed: {e}[/red]")
                speak_text(f"Status failed: {e}", enabled=speak)
            if once:
                break
            continue

        if cmd.kind in ("need_task_light", "need_task_full"):
            kind_label = "lightweight" if cmd.kind == "need_task_light" else "full OpenClaw"
            console.print(
                Panel(
                    f"[bold]Task mode[/bold] — describe what the [cyan]{kind_label}[/cyan] worker should do.\n\n"
                    "[dim]You do not need to say “Dave” again. You can speak in [bold]several parts[/bold]; "
                    "after each part, say more or say [bold]done[/bold] when finished. "
                    "You can also end one long sentence with “… done”. "
                    "Pauses are less likely to cut you off early on this step.[/dim]",
                    title="Follow-up",
                    border_style="magenta",
                )
            )
            console.print(
                "[magenta bold]Listening for task…[/magenta bold] "
                "[dim](multipart — say [bold]done[/bold] to finish)[/dim]"
            )
            task2 = listen_task_instruction_multipart(
                console=console,
                print_heard=print_heard,
            )
            if not task2:
                console.print("[yellow]No task heard. Try again from the main prompt with [bold]Dave[/bold].[/yellow]")
                if once:
                    break
                continue
            task_norm = normalize_task_instruction(task2)
            if print_heard:
                console.print(f"[dim]Task text (combined):[/dim] {task_norm}")
            use_full = cmd.kind == "need_task_full"
            cmd = ParsedVoiceCommand(
                kind="full" if use_full else "lightweight",
                task=task_norm,
                raw_after_wake=task_norm,
            )

        if cmd.kind == "lightweight" and cmd.task:
            try:
                ctx = mem.build_api_context()
                out = api_create_task(
                    base_url,
                    task=cmd.task,
                    context=ctx,
                    use_full_openclaw=False,
                    interactive=False,
                )
                console.print(Panel(str(out), title="Job created (lightweight)", border_style="green"))
                mem.record_last_panel("Job created (lightweight)", str(out))
                jid = out.get("job_id")
                if jid:
                    mem.note_job_created(jid, cmd.task or "", use_full_openclaw=False)
                    mem.note_command(f"lightweight job {jid[:8]}")
                    console.print("[cyan]Waiting for task to finish…[/cyan]")

                    def _on_terminal_light(d: dict) -> None:
                        mem.record_terminal_job_panel(jid, d)
                        mem.on_job_terminal(
                            jid,
                            d,
                            speak=lambda t: speak_text(t, enabled=speak),
                            enabled=speak,
                        )

                    poll_until_done(
                        base_url,
                        jid,
                        tts_speak=_tts_line if speak else None,
                        tts_job_result=_tts_job_result if speak else None,
                        on_terminal=_on_terminal_light,
                    )
            except Exception as e:
                console.print(f"[red]Failed: {e}[/red]")
                speak_text(f"Failed to create job: {e}", enabled=speak)
            if once:
                break
            continue

        if cmd.kind == "full" and cmd.task:
            try:
                ctx = mem.build_api_context()
                out = api_create_task(
                    base_url,
                    task=cmd.task,
                    context=ctx,
                    use_full_openclaw=True,
                    interactive=interactive_full,
                )
                console.print(Panel(str(out), title="Job created (full OpenClaw)", border_style="green"))
                mem.record_last_panel("Job created (full OpenClaw)", str(out))
                jid = out.get("job_id")
                if jid:
                    mem.note_job_created(jid, cmd.task or "", use_full_openclaw=True)
                    mem.note_command(f"full openclaw job {jid[:8]}")
                    if interactive_full:
                        console.print(
                            "[cyan]Interactive container running — waiting for initial task result…[/cyan]"
                        )
                    else:
                        console.print("[cyan]Waiting for task to finish…[/cyan]")

                    def _on_terminal_full(d: dict) -> None:
                        mem.record_terminal_job_panel(jid, d)
                        mem.on_job_terminal(
                            jid,
                            d,
                            speak=lambda t: speak_text(t, enabled=speak),
                            enabled=speak,
                        )

                    poll_until_done(
                        base_url,
                        jid,
                        tts_speak=_tts_line if speak else None,
                        tts_job_result=_tts_job_result if speak else None,
                        on_terminal=_on_terminal_full,
                    )
            except Exception as e:
                console.print(f"[red]Failed: {e}[/red]")
                speak_text(f"Failed to create job: {e}", enabled=speak)
            if once:
                break
            continue

        if once:
            break
