"""
Opt-in analytics for the hand-interaction demo: unsupervised calibration of
cube hold duration (trigger threshold) from observed samples.

Enable with: DAVE_HAND_ML_CALIBRATION=1

Persists under ~/.dave/hand_calibration.json (samples capped). No network.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# Clamp learned threshold to a safe range (seconds)
_MIN_TRIGGER_HOLD = 0.55
_MAX_TRIGGER_HOLD = 3.0
# Need enough points to assume a stable distribution
_MIN_SAMPLES_REFIT = 12
_MAX_STORED_SAMPLES = 220
# Blend new suggestion with previous threshold (reduce jitter)
_SMOOTH = 0.35


def _persist_path() -> Path:
    root = Path.home() / ".dave"
    root.mkdir(parents=True, exist_ok=True)
    return root / "hand_calibration.json"


def _unsupervised_threshold_1d(samples: list[float]) -> float | None:
    """
    Split 1D samples into two groups (unsupervised): lower vs upper half by sorted split.
    Return a threshold between the two cluster means (short vs longer holds).

    If the distribution is not clearly split, returns None.
    """
    if len(samples) < _MIN_SAMPLES_REFIT:
        return None
    xs = sorted(samples)
    n = len(xs)
    mid = n // 2
    low = xs[:mid]
    high = xs[mid:]
    c0 = sum(low) / len(low)
    c1 = sum(high) / len(high)
    if c1 <= c0 + 0.02:
        return None
    t = (c0 + c1) / 2.0
    return max(_MIN_TRIGGER_HOLD, min(_MAX_TRIGGER_HOLD, t))


class HandCalibration:
    """
    Records hold durations at each trigger; periodically refits trigger_hold_seconds
    using a simple unsupervised 1D split (no labels).
    """

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled
        self._lock = threading.Lock()
        self._samples: list[float] = []
        self._trigger_hold: float | None = None
        if enabled:
            self._load()

    def get_trigger_hold_seconds(self, default: float) -> float:
        with self._lock:
            if self._trigger_hold is not None:
                return float(self._trigger_hold)
            return float(default)

    def record_trigger_hold_seconds(self, hold_seconds: float) -> None:
        if not self._enabled:
            return
        h = float(hold_seconds)
        if h <= 0 or h > 30.0:
            return
        with self._lock:
            self._samples.append(h)
            if len(self._samples) > _MAX_STORED_SAMPLES:
                self._samples = self._samples[-_MAX_STORED_SAMPLES :]
            n = len(self._samples)
        if n >= _MIN_SAMPLES_REFIT and n % 6 == 0:
            self._refit_and_persist()

    def _refit_and_persist(self) -> None:
        with self._lock:
            samples = list(self._samples)
            prev = self._trigger_hold
        suggested = _unsupervised_threshold_1d(samples)
        if suggested is None:
            return
        with self._lock:
            if prev is None:
                self._trigger_hold = suggested
            else:
                self._trigger_hold = (1.0 - _SMOOTH) * prev + _SMOOTH * suggested
            self._trigger_hold = max(
                _MIN_TRIGGER_HOLD, min(_MAX_TRIGGER_HOLD, float(self._trigger_hold))
            )
            out = {
                "version": 1,
                "trigger_hold_seconds": self._trigger_hold,
                "sample_count": len(self._samples),
            }
            try:
                path = _persist_path()
                path.write_text(json.dumps(out, indent=2), encoding="utf-8")
            except OSError:
                pass

    def _load(self) -> None:
        try:
            path = _persist_path()
            if not path.is_file():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            th = data.get("trigger_hold_seconds")
            if isinstance(th, (int, float)):
                self._trigger_hold = max(
                    _MIN_TRIGGER_HOLD, min(_MAX_TRIGGER_HOLD, float(th))
                )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    def flush(self) -> None:
        """Call on exit to persist a final refit."""
        if not self._enabled:
            return
        self._refit_and_persist()


def calibration_enabled_from_env() -> bool:
    v = os.environ.get("DAVE_HAND_ML_CALIBRATION")
    if v is None:
        return False
    return v.strip().lower() in ("1", "true", "yes", "on")
