"""
Session-only voice memory for dave-it-guy voice — in-process, discarded on exit.

Tracks last job, task text, lightweight vs full, simple preferences, and optional
follow-up reminders when a job completes. No disk persistence.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Total context prepended to new sub-agent tasks (preferences + last panel).
_MAX_TOTAL_CONTEXT_CHARS = 32000
# Stored plain text for the last substantive terminal panel (job result, chat, etc.).
_MAX_STORED_PANEL_CHARS = 48000
_MAX_FOLLOWUPS = 8
_RECENT_MAX = 10


def format_subagent_job_plain(job_id: str, data: dict[str, Any]) -> str:
    """Plain text matching the Sub-agent status panel (no Rich markup)."""
    status = data.get("status", "?")
    result = data.get("result")
    error = data.get("error")
    lines = [f"Job: {job_id}", f"Status: {status}"]
    if result and isinstance(result, dict):
        output = result.get("output", "")
        if output:
            lines.append("\nResult:")
            lines.append(str(output))
    if error:
        lines.append(f"\nError: {error}")
    return "\n".join(lines)


def _tail_fit_for_context(text: str, max_len: int) -> str:
    """Keep the end of *text* so follow-ups about options at the bottom still work."""
    if max_len < 80 or len(text) <= max_len:
        return text
    marker = "[Earlier output truncated from the start for size limit.]\n\n"
    m = len(marker)
    if max_len <= m + 40:
        return text[-max_len:]
    return marker + text[-(max_len - m) :]

# Repeat last task (same session)
_REPEAT_RE = re.compile(
    r"^(?:repeat(?:\s+last)?|run\s+it\s+again|do\s+it\s+again|same\s+(?:task|again)|once\s+more|rerun|run\s+again)\s*$",
    re.IGNORECASE,
)
_REPEAT_THAT_RE = re.compile(
    r"^(?:repeat|rerun|run)\s+(?:that|it|the\s+last\s+one|previous)\s*$",
    re.IGNORECASE,
)

# "Remember I prefer …" (this session only)
_REMEMBER_LIGHT = re.compile(
    r"^remember(?:\s+that)?\s+i\s+prefer\s+(?:lightweight|light|worker|one)\s*$",
    re.IGNORECASE,
)
_REMEMBER_FULL = re.compile(
    r"^remember(?:\s+that)?\s+i\s+prefer\s+(?:full(?:\s+openclaw)?|openclaw|container|two)\s*$",
    re.IGNORECASE,
)

# After next job completes, speak this reminder
_FOLLOWUP_A = re.compile(
    r"^(?:after\s+(?:this\s+)?job\s+(?:finishes\s+)?"
    r"(?:remind\s+me\s+to|tell\s+me\s+to)\s+)(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_FOLLOWUP_B = re.compile(
    r"^(?:remind\s+me\s+after\s+(?:this\s+)?job\s+(?:finishes\s+)?(?:to\s+)?)(.+)$",
    re.IGNORECASE | re.DOTALL,
)

_CLEAR_SESSION = re.compile(
    r"^(?:forget|clear)\s+session(?:\s+memory)?\s*$",
    re.IGNORECASE,
)


@dataclass
class VoiceSessionMemory:
    """Mutable session state for one `dave-it-guy voice` process."""

    last_job_id: str | None = None
    last_task_text: str | None = None
    last_use_full_openclaw: bool | None = None
    prefer_full_openclaw: bool | None = None
    # Last substantive Rich panel, as plain text (for the next sub-agent task / chat).
    last_panel_plain: str | None = None
    # Next job created will attach this follow-up text (one shot)
    _followup_after_next_job: str | None = None
    # job_id -> reminder message (spoken when that job reaches terminal state)
    pending_reminders: dict[str, str] = field(default_factory=dict)
    recent_notes: list[str] = field(default_factory=list)

    def note_command(self, summary: str) -> None:
        s = summary.strip()
        if not s:
            return
        self.recent_notes.append(s[:200])
        if len(self.recent_notes) > _RECENT_MAX:
            self.recent_notes = self.recent_notes[-_RECENT_MAX:]

    def note_job_created(self, job_id: str, task: str, use_full_openclaw: bool) -> None:
        self.last_job_id = job_id.strip()
        self.last_task_text = task.strip()[:2000]
        self.last_use_full_openclaw = use_full_openclaw
        if self._followup_after_next_job:
            msg = self._followup_after_next_job.strip()
            if msg and len(self.pending_reminders) < _MAX_FOLLOWUPS:
                self.pending_reminders[job_id] = msg[:500]
            self._followup_after_next_job = None

    def clear(self) -> None:
        self.last_job_id = None
        self.last_task_text = None
        self.last_use_full_openclaw = None
        self.prefer_full_openclaw = None
        self.last_panel_plain = None
        self._followup_after_next_job = None
        self.pending_reminders.clear()
        self.recent_notes.clear()

    def record_last_panel(self, title: str, body: str) -> None:
        """Store plain text for the last panel shown (truncated for memory size)."""
        t = (title or "Output").strip() or "Output"
        b = (body or "").strip()
        if not b:
            return
        combined = f"=== {t} ===\n{b}"
        if len(combined) > _MAX_STORED_PANEL_CHARS:
            combined = (
                combined[: _MAX_STORED_PANEL_CHARS - 48].rstrip()
                + "\n... [truncated for session memory]"
            )
        self.last_panel_plain = combined

    def record_terminal_job_panel(self, job_id: str, data: dict[str, Any]) -> None:
        """After a job completes or fails — same content as the Sub-agent status panel."""
        self.record_last_panel("Sub-agent job result", format_subagent_job_plain(job_id, data))

    def build_api_context(self) -> str | None:
        """Optional context string for MasterClaw / worker (prepended to task)."""
        parts: list[str] = []
        if self.prefer_full_openclaw is True:
            parts.append("User preference (this voice session): prefer full OpenClaw when unspecified.")
        elif self.prefer_full_openclaw is False:
            parts.append("User preference (this voice session): prefer lightweight worker when unspecified.")
        if self.last_task_text and self.last_job_id:
            t = self.last_task_text[:400]
            parts.append(f"Previous task in this session (job {self.last_job_id[:8]}…): {t}")
        meta = "\n".join(parts) if parts else ""

        panel_block = ""
        if self.last_panel_plain:
            header = (
                "---\n"
                "Previous output shown to the user in this voice session "
                '(use for references like "the above"):\n'
                "---\n"
            )
            body = self.last_panel_plain
            room_total = max(0, _MAX_TOTAL_CONTEXT_CHARS - len(meta) - (2 if meta else 0))
            room_body = max(0, room_total - len(header))
            if room_body > 0:
                if len(body) > room_body:
                    body = _tail_fit_for_context(body, room_body)
                panel_block = header + body

        if not meta and not panel_block:
            return None
        combined = "\n\n".join(x for x in (meta, panel_block) if x)
        return combined[:_MAX_TOTAL_CONTEXT_CHARS]

    def build_chat_system_extra(self) -> str | None:
        """Extra lines appended to chat fallback system prompt."""
        lines: list[str] = []
        if self.last_job_id:
            lines.append(f"Last sub-agent job ID this session: {self.last_job_id}")
        if self.last_task_text:
            lines.append(f"Last task text (truncated): {self.last_task_text[:300]}")
        if self.prefer_full_openclaw is True:
            lines.append("User said they prefer full OpenClaw for ambiguous cases.")
        elif self.prefer_full_openclaw is False:
            lines.append("User said they prefer lightweight worker for ambiguous cases.")
        if self.recent_notes:
            lines.append("Recent voice commands: " + "; ".join(self.recent_notes[-5:]))
        if self.last_panel_plain:
            lp = self.last_panel_plain
            if len(lp) > 6000:
                lp = "[... earlier text omitted ...]\n" + lp[-6000:]
            lines.append(
                'Last output shown to the user in the terminal (for "the above"):\n' + lp
            )
        if not lines:
            return None
        return "\n".join(lines)

    def on_job_terminal(
        self,
        job_id: str,
        data: dict,
        *,
        speak: Callable[[str], None] | None,
        enabled: bool,
    ) -> None:
        """After poll sees completed/failed — remind if we have a pending line for this job."""
        jid = job_id.strip()
        msg = self.pending_reminders.pop(jid, None)
        if not msg:
            return
        status = (data.get("status") or "").lower()
        if status != "completed":
            # Put back if failed so user can retry? Simpler: drop on failure
            return
        line = f"Follow-up reminder: {msg}"
        if speak and enabled:
            speak(line)


def try_session_memory_command(
    after_wake: str,
    mem: VoiceSessionMemory,
) -> Any:
    """
    If the utterance (after wake word) is a session-memory phrase, return a command
    to handle it; otherwise None (caller uses parse_voice_command).
    """
    from dave_it_guy.voice_assistant import ParsedVoiceCommand

    s = after_wake.strip()
    if not s:
        return None

    if _CLEAR_SESSION.match(s):
        mem.clear()
        return ParsedVoiceCommand(
            kind="memory_hint",
            memory_hint="Session memory cleared for this run.",
            raw_after_wake=s,
        )

    if _REMEMBER_LIGHT.match(s):
        mem.prefer_full_openclaw = False
        return ParsedVoiceCommand(
            kind="memory_hint",
            memory_hint="Saved for this session: prefer **lightweight** worker when unspecified.",
            raw_after_wake=s,
        )

    if _REMEMBER_FULL.match(s):
        mem.prefer_full_openclaw = True
        return ParsedVoiceCommand(
            kind="memory_hint",
            memory_hint="Saved for this session: prefer **full OpenClaw** when unspecified.",
            raw_after_wake=s,
        )

    m = _FOLLOWUP_A.match(s) or _FOLLOWUP_B.match(s)
    if m:
        rest = (m.group(1) or "").strip()
        if not rest:
            return ParsedVoiceCommand(
                kind="memory_hint",
                memory_hint="Say what to remind you about after the next job completes.",
                raw_after_wake=s,
            )
        mem._followup_after_next_job = rest
        return ParsedVoiceCommand(
            kind="memory_hint",
            memory_hint="Got it. After the **next** job you start finishes, I'll remind you.",
            raw_after_wake=s,
        )

    if _REPEAT_RE.match(s) or _REPEAT_THAT_RE.match(s):
        if not mem.last_task_text:
            return ParsedVoiceCommand(
                kind="memory_hint",
                memory_hint="No previous task in this session to repeat. Say **Dave lightweight** or **Dave command two** first.",
                raw_after_wake=s,
            )
        use_full = mem.last_use_full_openclaw
        if use_full is None:
            use_full = bool(mem.prefer_full_openclaw)
        if use_full is None:
            use_full = False
        return ParsedVoiceCommand(
            kind="full" if use_full else "lightweight",
            task=mem.last_task_text,
            raw_after_wake=s,
        )

    return None


def apply_default_job_id_for_status(cmd: Any, mem: VoiceSessionMemory) -> Any:
    """If status has no job id, use last_job_id when available."""
    from dataclasses import replace

    if cmd.kind != "status" or cmd.job_id:
        return cmd
    if not mem.last_job_id:
        return cmd
    return replace(cmd, job_id=mem.last_job_id)


def apply_preference_to_need_task(cmd: Any, mem: VoiceSessionMemory) -> Any:
    """If user has need_task_* and a preference, map to concrete lightweight/full."""
    from dataclasses import replace

    if cmd.kind == "need_task_light" and mem.prefer_full_openclaw is True:
        return replace(cmd, kind="need_task_full")
    if cmd.kind == "need_task_full" and mem.prefer_full_openclaw is False:
        return replace(cmd, kind="need_task_light")
    return cmd
