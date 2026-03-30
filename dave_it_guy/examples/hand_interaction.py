"""
Webcam demo: face (eyes, nose, expression) + hands: CLAW (U-gap, thumb–index
inward, or thumb–pinky inward), thumb up/down, and informal gestures
(OK, Peace, Point, Shaka, Pinch, Fist, Open palm).

Claw U is shown only after holding the pose continuously for _CLAW_HOLD_SECONDS
(default 5s); until then the UI shows progress (Claw hold: x / 5.0s).

Claw detection: if models/claw_mlp.joblib + claw_lstm.pt + claw_meta.json exist
(trained via collect_claw_data.py + train_claw_models.py), uses MLP+LSTM on the
**right hand if visible**, otherwise on the **left**; geometry heuristics apply to **either** hand.

Uses MediaPipe Tasks: Face Landmarker + Hand Landmarker. By default (``DAVE_HAND_FACE_ROI_FILTER``,
default on) the face model runs to obtain a face bounding box so hand detections overlapping the
face are dropped — a misdetected \"face hand\" does not drive CLAW, gestures, or the cube. Set
``DAVE_HAND_FACE_ROI_FILTER=0`` to skip the extra face pass (weaker heuristic-only filtering).
The face mesh is not drawn unless you enable face features. Hands use MediaPipe
default landmark dots + connection colors (same idea as Sample-Lucia-The-Master).

Cube hold → speech (multipart task capture, same idea as ``dave-it-guy voice`` full OpenClaw flow):

* **Cube (Sample-Lucia-The-Master style):** **fist** (landmarks, debounced) **turns the cube on**
  per hand. **While the cube is on, fist or open palm** keeps it alive.   **Open palm:** cube tracks
  the **palm pad**; the wireframe **spins continuously** (unwrapped in-plane angle, full 360°+), and
  **forward/back tilt** (wrist–finger depth or image lean) adjusts the **depth** of the wireframe.
  **fist:** position follows the hand bbox center (no palm-driven rotation). **Finger spread** resizes
  the cube (smoothed): more open
  fingers → larger, tighter curl → smaller (``DAVE_HAND_CUBE_FINGER_RESIZE=0`` disables). Open palm does
  **not** turn the cube on — only fist does (unless the triangle gate below applies).
  For **full OpenClaw** jobs, ``DAVE_HAND_VOICE_OPENCLAW_INTERACTIVE=1`` (default) creates a
  **persistent** sub-agent (``interactive: true``) and reuses it via ``POST /subagent/{id}/followup``
  when ``DAVE_HAND_VOICE_OPENCLAW_REUSE=1`` (default). Session id is shared with triangle interactive.
  After each turn completes, trigger state resets to **idle** so you can **hold the cube again**
  without opening your hand. ``DAVE_HAND_VOICE_OPENCLAW_NEW_SESSION=1`` always starts a new job.
  **Point gesture** (index extended, other fingers curled): after a short hold, stops TTS and
  cancels multipart listen — ``DAVE_HAND_POINT_INTERRUPTS_VOICE=0`` to disable.
  After each **interactive full OpenClaw** job completes, optional **next instruction** listen
  (``DAVE_HAND_POST_JOB_LISTEN=1``, default) waits for result TTS to finish, then multipart capture; say
  **terminate container** (or **stop the container** / **end session**) to clear the session id.
  Optional: ``DAVE_HAND_CUBE_REQUIRE_TRIANGLE=1`` — require a two-hand triangle pose first, then fist
  (face / expression never arms the cube).
* **Triangle / sphere** (two-hand pinch → sphere + optional OpenClaw): **off by default**
  (``DAVE_HAND_TRIANGLE_FEATURE=0``). Set ``DAVE_HAND_TRIANGLE_FEATURE=1`` to enable sphere mode and
  ``DAVE_HAND_TRIANGLE_INTERACTIVE`` (default on when feature is on) POSTs to MasterClaw; see
  ``DAVE_HAND_TRIANGLE_TASK``, ``DAVE_HAND_TRIANGLE_REUSE_SESSION``, ``DAVE_HAND_TRIANGLE_FOLLOWUP_TASK``.
  Each visible hand can show a sphere with the same **360° unwrap + pitch** rotation as the cube.
* ``DAVE_HAND_SIMPLE_LISTEN=1`` — one-shot listen instead of multipart (say done / segments).
* ``DAVE_HAND_SPEAK=0`` — disable TTS prompts and job readout.
* ``DAVE_HAND_SUMMARIZE_SPEECH=1`` — summarize long OpenClaw results for overlay + speech (Ollama).
* ``DAVE_HAND_ALLOW_CLEANUP=1`` — allow **cleanup** / **clear list** (destructive) instead of a task.
* ``DAVE_HAND_ML_CALIBRATION=1`` — unsupervised calibration of cube hold threshold from observed
  triggers; persists under ``~/.dave/hand_calibration.json``.
"""

from __future__ import annotations

import collections
import math
import os
import pathlib
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from types import SimpleNamespace

import cv2

try:
    import claw_features  # type: ignore
except Exception:
    claw_features = None

from mediapipe.framework.formats import landmark_pb2
from mediapipe.python._framework_bindings import image as _mp_image_binding
from mediapipe.python._framework_bindings import image_frame as _mp_image_frame
from mediapipe.python.solutions import drawing_styles, drawing_utils
from mediapipe.tasks.python.components.containers import category as category_lib
from mediapipe.tasks.python.core import base_options as base_options_lib
from mediapipe.tasks.python.vision import face_landmarker, hand_landmarker
from mediapipe.tasks.python.vision.core import vision_task_running_mode as running_mode_lib

from dave_it_guy.hand_calibration import HandCalibration, calibration_enabled_from_env


class _MpImageLib:
    """Shim: full MediaPipe wheels expose Image on tasks.python.vision.core.image; slim wheels do not."""

    Image = _mp_image_binding.Image
    ImageFormat = _mp_image_frame.ImageFormat


mp_image_lib = _MpImageLib()


def _tasks_landmarks_to_proto(lm_seq: list) -> landmark_pb2.NormalizedLandmarkList:
    """Tasks returns a list of NormalizedLandmark dataclasses; drawing_utils expects a protobuf list."""
    out = landmark_pb2.NormalizedLandmarkList()
    for lm in lm_seq:
        out.landmark.append(lm.to_pb2() if hasattr(lm, "to_pb2") else lm)
    return out


def _tasks_landmarks_to_proto_for_draw(lm_seq: list) -> landmark_pb2.NormalizedLandmarkList:
    """
    Same as :func:`_tasks_landmarks_to_proto`, but sets visibility/presence so
    ``mediapipe.python.solutions.drawing_utils.draw_landmarks`` does not drop every point.

    Tasks often leaves visibility/presence at 0; ``drawing_utils`` treats that as invisible
    (``< 0.5``), so no connection lines are drawn.
    """
    out = landmark_pb2.NormalizedLandmarkList()
    for lm in lm_seq:
        pb = lm.to_pb2() if hasattr(lm, "to_pb2") else lm
        elt = out.landmark.add()
        elt.CopyFrom(pb)
        elt.visibility = 1.0
        elt.presence = 1.0
    return out


def _task_connections_to_tuples(connections: list) -> list[tuple[int, int]]:
    """Tasks API uses Connection(start, end) dataclasses; drawing_utils expects list[tuple[int, int]]."""
    out: list[tuple[int, int]] = []
    for c in connections:
        if isinstance(c, tuple):
            out.append((int(c[0]), int(c[1])))
        else:
            out.append((int(c.start), int(c.end)))
    return out


def _hand_connection_styles_for_tasks(
    base: Mapping[tuple[int, int], drawing_utils.DrawingSpec],
) -> dict[tuple[int, int], drawing_utils.DrawingSpec]:
    """
    ``drawing_styles`` maps legacy palm edge ``(0, 5)``. Tasks Hand Landmarker uses ``(1, 5)``
    for the adjacent palm link — :func:`drawing_utils.draw_landmarks` would otherwise raise
    ``KeyError`` on that edge.
    """
    out = dict(base)
    hconn = hand_landmarker.HandLandmarksConnections
    for a, b in _task_connections_to_tuples(hconn.HAND_CONNECTIONS):
        t = (a, b)
        if t in out:
            continue
        if t == (1, 5) and (0, 5) in out:
            out[t] = out[(0, 5)]
            continue
        br = (b, a)
        if br in out:
            out[t] = out[br]
            continue
        out[t] = drawing_utils.DrawingSpec(color=(128, 128, 128), thickness=2)
    return out


# Models (auto-download next to this script if missing)
_FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)
_FACE_MODEL_NAME = "face_landmarker.task"
_HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)
_HAND_MODEL_NAME = "hand_landmarker.task"

# Feature flags
_ENABLE_FACE_DETECTION = False

# Blendshape-based expression thresholds (tune if needed)
_SMILE_MIN = 0.28
_FROWN_MIN = 0.22

# Thumb heuristic margins (normalized image coords; y grows downward)
_THUMB_BEND_MARGIN = 0.018
_THUMB_VS_INDEX_MARGIN = 0.012

# "Claw U" shape: thumb + index form a small gap U; other fingers loosely curled (either hand).
_CLAW_TI_MIN = 0.10  # min dist(thumb tip, index tip) / hand span
_CLAW_TI_MAX = 0.52  # max (still a U, not wide open)
_CLAW_INDEX_ANGLE_MIN = 100.0  # slight bend at index PIP
_CLAW_INDEX_ANGLE_MAX = 175.0
_CLAW_FINGER_CURLED_MAX = 168.0  # middle / ring / pinky bent at PIP
_CLAW_FINGER_CURLED_MIN = 70.0
_CLAW_MIN_CURLED_COUNT = 2  # at least two of three fingers loosely curled
# Second CLAW variant: thumb + index tips close, segments converge inward (tight pinch vs U-gap)
_CLAW_INWARD_MIN = 0.018  # min dist(thumb tip, index tip) / span
_CLAW_INWARD_MAX = 0.095  # below U-gap claw so distinct from rounded "U"
_CLAW_INWARD_DOT_MAX = 0.12  # max cos(angle) between distal thumb & index dirs (lower → more "inward")
# Thumb + pinky tips toward each other (inward), index/middle curled — also CLAW
_CLAW_TP_INWARD_MIN = 0.035
_CLAW_TP_INWARD_MAX = 0.20  # tighter than casual Shaka spread
_CLAW_TP_INWARD_DOT_MAX = 0.30  # thumb IP→tip vs pinky PIP→tip convergence
_CLAW_HOLD_SECONDS = 5.0  # must hold pose this long before "Claw U" is shown
_DT_CAP = 0.25  # ignore huge time jumps (e.g. window focus) when integrating hold
_DEBUG_FINGER_INTERVAL_SEC = 0.2  # terminal spam throttle for finger positions
_HAND_OVERLAY_LINES = 10  # left panel: CLAW / hands
_SPEECH_OVERLAY_LINES = 28  # right panel: speech + MasterClaw + job output (chunked lines)

# Per-hand cube debouncing (reduce flicker)
_CUBE_FIST_ON_FRAMES = 3  # require N consecutive fist frames to show cube
_CUBE_FIST_OFF_FRAMES = 16  # non-hold pose / lost hand frames before hiding cube
_CUBE_HOLD_MS_IF_LOST = 350  # keep drawing at last center during short tracking drops
# EMA smoothing for cube position (lower = stabler) and palm rotation (lower = less jitter).
# Palm position: higher alpha = cube follows palm movement faster (0.11 felt very laggy).
_CUBE_CENTER_EMA_PALM = 0.52
_CUBE_CENTER_EMA_FIST = 0.26
# Angle EMA: higher = follows palm rotation more closely (very low values feel sluggish).
_CUBE_ANGLE_EMA_PALM = 0.32
# Palm in-plane rotation: integrate angle deltas so the cube can spin past 360° without snapping.
_CUBE_ANGLE_UNWRAP_DELTA_EMA = 0.55  # low-pass on per-frame delta (reduces jitter)
# Forward/back tilt → back-face depth + vertical skew (pitch); z-scale is heuristic.
_CUBE_PITCH_Z_GAIN = 105.0
# Finger spread → cube size (mean wrist→fingertip dist in normalized space; fist low, open high).
_CUBE_SIZE_EMA = 0.30
_CUBE_SPREAD_LO = 0.085
_CUBE_SPREAD_HI = 0.295
_CUBE_SCALE_MIN = 0.58
_CUBE_SCALE_MAX = 1.42
# Brief MediaPipe dropout while rotating — don't count toward cube-off immediately.
_CUBE_UNSEEN_GRACE_FRAMES = 15
_CUBE_TRIGGER_HOLD_SECONDS = 1.25  # after cube is ON for this long, trigger voice→full OpenClaw
_CUBE_TRIGGER_COOLDOWN_SECONDS = 4.0  # don't re-trigger immediately
# Optional two-hand triangle gate (``DAVE_HAND_CUBE_REQUIRE_TRIANGLE=1``): index tips + thumb apex
# held N frames, then a short window where fist can start the cube debounce.
_CUBE_TRIANGLE_ON_FRAMES = 10
_CUBE_TRIANGLE_ARM_SECONDS = 5.0
# Two-hand triangle pinch → sphere + optional OpenClaw (gated by DAVE_HAND_TRIANGLE_FEATURE).
_TRIANGLE_SPHERE_STREAK_FRAMES = 10
_SPHERE_ARM_SECONDS = 120.0
_SPHERE_PALM_ON_FRAMES = 5
_SPHERE_OFF_FRAMES = 18
_SPHERE_UNSEEN_GRACE_FRAMES = 15
_INTERACTIVE_OC_COOLDOWN_SEC = 12.0
# Index finger "point" held ~this many frames → stop TTS + cancel in-progress listen.
_POINT_INTERRUPT_STREAK_FRAMES = 8
# Poll MasterClaw for job result (same idea as masterclaw_tui.poll_until_done)
_JOB_POLL_INTERVAL_SEC = 2.0
_JOB_POLL_MAX_SEC = 600.0
_JOB_RESULT_MAX_CHARS = 12000
_JOB_OVERLAY_LINE_CHARS = 88


def _hand_env_bool(name: str, default: bool = False) -> bool:
    """True if env var is 1/true/yes/on (case-insensitive); else *default*."""
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _safe_overlay_text(s: str) -> str:
    """OpenCV's default font cannot draw most Unicode; avoid mojibake (e.g. ???)."""
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
    s = s.replace("\u2192", "->").replace("\u2794", "->")
    return "".join(c if ord(c) < 128 else " " for c in s)

# Informal single-hand gestures (angles in degrees at PIP: MCP–PIP–TIP chain)
_EXTEND_MIN_DEG = 142.0
_CURLED_MAX_DEG = 135.0
_PINCH_SPAN_FRAC = 0.07  # thumb–index tip distance / span for pinch/OK
_OK_OTHER_EXTEND_MIN = 138.0  # middle/ring/pinky extended for OK vs pinch


def _ensure_asset(path: pathlib.Path, url: str, label: str) -> pathlib.Path:
    if path.is_file():
        return path
    print(f"Downloading {label} (one-time)...", file=sys.stderr)
    urllib.request.urlretrieve(url, path)
    return path


def _expression_from_blendshapes(
    blendshapes: list[category_lib.Category] | None,
) -> str:
    """Smile / sad / neutral from ARKit-style blendshape scores."""
    if not blendshapes:
        return "Neutral"
    bs = blendshapes

    def score(idx: face_landmarker.Blendshapes) -> float:
        c = bs[int(idx)]
        return float(c.score) if c.score is not None else 0.0

    smile = (
        score(face_landmarker.Blendshapes.MOUTH_SMILE_LEFT)
        + score(face_landmarker.Blendshapes.MOUTH_SMILE_RIGHT)
    ) / 2.0
    frown = (
        score(face_landmarker.Blendshapes.MOUTH_FROWN_LEFT)
        + score(face_landmarker.Blendshapes.MOUTH_FROWN_RIGHT)
    ) / 2.0

    if smile >= _SMILE_MIN and smile > frown:
        return "Smile"
    if frown >= _FROWN_MIN and frown > smile:
        return "Sad"
    return "Neutral"


def _dist2_xy(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _angle_at_b(a, b, c) -> float:
    """Angle ABC at vertex b in degrees (0–180)."""
    v1 = (a.x - b.x, a.y - b.y)
    v2 = (c.x - b.x, c.y - b.y)
    n1 = math.hypot(v1[0], v1[1])
    n2 = math.hypot(v2[0], v2[1])
    if n1 < 1e-9 or n2 < 1e-9:
        return 180.0
    cos_t = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos_t))


def _hand_span(lm: list) -> float:
    """Rough scale: wrist to middle MCP."""
    HL = hand_landmarker.HandLandmark
    return _dist2_xy(lm[HL.WRIST], lm[HL.MIDDLE_FINGER_MCP])


def _finger_mean_tip_wrist_dist(lm: list) -> float:
    """Mean 2D distance (normalized image coords) wrist → four fingertips; rises as fingers open."""
    HL = hand_landmarker.HandLandmark
    w = lm[HL.WRIST]
    s = 0.0
    for t in (
        HL.INDEX_FINGER_TIP,
        HL.MIDDLE_FINGER_TIP,
        HL.RING_FINGER_TIP,
        HL.PINKY_TIP,
    ):
        s += _dist2_xy(w, lm[t])
    return s / 4.0


def _palm_hand_rotation_deg(lm: list) -> float:
    """
    Image-plane angle (deg): wrist toward middle finger, blending MCP and tip for a stabler axis
    than MCP-only (less micro-jitter when rotating).
    """
    HL = hand_landmarker.HandLandmark
    w = lm[HL.WRIST]
    m = lm[HL.MIDDLE_FINGER_MCP]
    t = lm[HL.MIDDLE_FINGER_TIP]
    vx = 0.5 * ((m.x - w.x) + (t.x - w.x))
    vy = 0.5 * ((m.y - w.y) + (t.y - w.y))
    if vx * vx + vy * vy < 1e-10:
        m = lm[HL.MIDDLE_FINGER_MCP]
        return math.degrees(math.atan2(m.y - w.y, m.x - w.x))
    return math.degrees(math.atan2(vy, vx))


def _palm_pitch_deg(lm: list) -> float:
    """
    Forward / back tilt (degrees) for pseudo-3D: mean fingertip z vs wrist when depth is usable,
    else image-plane lean of the finger group (toward / away from wrist in y vs x).
    """
    HL = hand_landmarker.HandLandmark
    w = lm[HL.WRIST]
    tips = (
        HL.INDEX_FINGER_TIP,
        HL.MIDDLE_FINGER_TIP,
        HL.RING_FINGER_TIP,
        HL.PINKY_TIP,
    )
    zw = getattr(w, "z", None)
    dz_list: list[float] = []
    if zw is not None:
        for tid in tips:
            zt = getattr(lm[tid], "z", None)
            if zt is not None:
                dz_list.append(float(zt) - float(zw))
    if dz_list and max(abs(x) for x in dz_list) > 1e-5:
        dz = sum(dz_list) / len(dz_list)
        return float(max(-80.0, min(80.0, _CUBE_PITCH_Z_GAIN * dz)))
    my = sum(lm[tid].y for tid in tips) / len(tips)
    mx = sum(lm[tid].x for tid in tips) / len(tips)
    vy = my - w.y
    vx = abs(mx - w.x) + 1e-6
    return float(max(-80.0, min(80.0, math.degrees(math.atan2(vy, vx)))))


def _smooth_angle(prev: float | None, new: float, alpha: float) -> float:
    """Low-pass to reduce jitter (degrees; no wrap — fine for slow palm rotation)."""
    if prev is None:
        return new
    return (1.0 - alpha) * prev + alpha * new


def _smooth_angle_deg_shortest(prev: float, target: float, alpha: float) -> float:
    """EMA toward *target* using shortest turn on the circle (handles ±180° wrap)."""
    delta = ((target - prev + 180.0) % 360.0) - 180.0
    return prev + alpha * delta


def _smooth_center_xy(
    prev: tuple[int, int] | None,
    raw: tuple[int, int],
    *,
    alpha: float,
) -> tuple[int, int]:
    """EMA on cube screen position for stable placement while hand moves slightly."""
    if prev is None:
        return raw
    rx, ry = float(raw[0]), float(raw[1])
    px, py = float(prev[0]), float(prev[1])
    x = int(round((1.0 - alpha) * px + alpha * rx))
    y = int(round((1.0 - alpha) * py + alpha * ry))
    return (x, y)


def _hand_bbox_center_screen_px(lm: list, w: int, h: int) -> tuple[int, int]:
    """Bounding-box center of all landmarks (used for fist / generic)."""
    xs = [p.x for p in lm]
    ys = [p.y for p in lm]
    cx_n = (min(xs) + max(xs)) / 2.0
    cy_n = (min(ys) + max(ys)) / 2.0
    return int(cx_n * w), int(cy_n * h)


def _norm_axis_bounds_from_lm(lm_seq: list) -> tuple[float, float, float, float]:
    """Axis-aligned bounds (minx, maxx, miny, maxy) in normalized image coordinates."""
    xs = [p.x for p in lm_seq]
    ys = [p.y for p in lm_seq]
    return (min(xs), max(xs), min(ys), max(ys))


def _expand_norm_bounds(
    b: tuple[float, float, float, float], pad: float
) -> tuple[float, float, float, float]:
    minx, maxx, miny, maxy = b
    return (minx - pad, maxx + pad, miny - pad, maxy + pad)


def _point_in_norm_bounds(px: float, py: float, b: tuple[float, float, float, float]) -> bool:
    minx, maxx, miny, maxy = b
    return minx <= px <= maxx and miny <= py <= maxy


def _norm_rect_area(b: tuple[float, float, float, float]) -> float:
    minx, maxx, miny, maxy = b
    return max(0.0, maxx - minx) * max(0.0, maxy - miny)


def _norm_rect_intersection_area(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    minx = max(a[0], b[0])
    maxx = min(a[1], b[1])
    miny = max(a[2], b[2])
    maxy = min(a[3], b[3])
    if minx >= maxx or miny >= maxy:
        return 0.0
    return (maxx - minx) * (maxy - miny)


def _hand_is_face_false_positive(
    lm: list,
    face_bounds_norm: tuple[float, float, float, float] | None,
) -> bool:
    """
    MediaPipe often returns a \"hand\" that is actually the face. Those should not drive
    cube, CLAW, or overlays. With face bounds (from Face Landmarker), use overlap and
    landmark-in-ROI counts — centroid/wrist-only checks miss many false positives.
    Without bounds, reject compact detections high in the frame (typical false positive).
    """
    HL = hand_landmarker.HandLandmark
    wrist = lm[HL.WRIST]
    n = len(lm)
    cx = sum(p.x for p in lm) / n
    cy = sum(p.y for p in lm) / n
    if face_bounds_norm is not None:
        hb = _norm_axis_bounds_from_lm(lm)
        fb_loose = _expand_norm_bounds(face_bounds_norm, 0.11)
        inside = sum(1 for p in lm if _point_in_norm_bounds(p.x, p.y, fb_loose))
        if inside >= 7:
            return True
        if n > 0 and (inside / n) >= 0.33:
            return True
        ha = _norm_rect_area(hb)
        inter = _norm_rect_intersection_area(hb, face_bounds_norm)
        if ha > 1e-8 and (inter / ha) >= 0.26:
            return True
        if _point_in_norm_bounds(cx, cy, _expand_norm_bounds(face_bounds_norm, 0.06)):
            return True
        if _point_in_norm_bounds(wrist.x, wrist.y, _expand_norm_bounds(face_bounds_norm, 0.11)):
            return True
        return False
    xs = [p.x for p in lm]
    ys = [p.y for p in lm]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    bw, bh = maxx - minx, maxy - miny
    cyy = 0.5 * (miny + maxy)
    span = _hand_span(lm)
    if cyy < 0.52 and bh < 0.20 and bw < 0.30 and span < 0.09:
        return True
    if miny < 0.38 and cyy < 0.54 and span < 0.095:
        return True
    return False


def _filter_hands_not_face_proxy(
    hand_landmarks: list,
    handedness: list[list[category_lib.Category]],
    face_bounds_norm: tuple[float, float, float, float] | None,
) -> tuple[list, list[list[category_lib.Category]]]:
    """Drop hand detections that overlap the face (see :func:`_hand_is_face_false_positive`)."""
    if not hand_landmarks:
        return [], []
    out_lm: list = []
    out_h: list[list[category_lib.Category]] = []
    for i, lm in enumerate(hand_landmarks):
        if _hand_is_face_false_positive(lm, face_bounds_norm):
            continue
        out_lm.append(lm)
        out_h.append(handedness[i] if i < len(handedness) else [])
    return out_lm, out_h


def _palm_center_screen_px(lm: list, w: int, h: int) -> tuple[int, int]:
    """Approximate palm-pad center (wrist + MCP ring) so the cube sits in the open palm."""
    HL = hand_landmarker.HandLandmark
    idx = (
        HL.WRIST,
        HL.INDEX_FINGER_MCP,
        HL.MIDDLE_FINGER_MCP,
        HL.RING_FINGER_MCP,
        HL.PINKY_MCP,
    )
    sx = sum(lm[i].x for i in idx) / len(idx)
    sy = sum(lm[i].y for i in idx) / len(idx)
    return int(sx * w), int(sy * h)


def _is_labeled_right_hand(categories: list[category_lib.Category] | None) -> bool:
    if not categories:
        return False
    name = (categories[0].category_name or "").lower()
    return "right" in name


def _is_labeled_left_hand(categories: list[category_lib.Category] | None) -> bool:
    if not categories:
        return False
    name = (categories[0].category_name or "").lower()
    return "left" in name


def _claw_u_signature(lm: list) -> bool:
    """
    Thumb + index form a rounded U (gap, not pinched); index slightly curved;
    thumb toward index; middle/ring/pinky loosely curled. Heuristic only.
    """
    HL = hand_landmarker.HandLandmark
    span = _hand_span(lm)
    if span < 0.02:
        return False

    d_ti = _dist2_xy(lm[HL.THUMB_TIP], lm[HL.INDEX_FINGER_TIP])
    r_ti = d_ti / span
    if not (_CLAW_TI_MIN < r_ti < _CLAW_TI_MAX):
        return False

    # Thumb closer to index tip than to pinky tip (inward C toward index)
    if _dist2_xy(lm[HL.THUMB_TIP], lm[HL.INDEX_FINGER_TIP]) >= _dist2_xy(
        lm[HL.THUMB_TIP], lm[HL.PINKY_TIP]
    ):
        return False

    # Not a vertical "thumb up" (thumb far above index row)
    if lm[HL.THUMB_TIP].y < lm[HL.INDEX_FINGER_MCP].y - 0.06:
        return False

    idx_ang = _angle_at_b(
        lm[HL.INDEX_FINGER_MCP],
        lm[HL.INDEX_FINGER_PIP],
        lm[HL.INDEX_FINGER_TIP],
    )
    if not (_CLAW_INDEX_ANGLE_MIN < idx_ang < _CLAW_INDEX_ANGLE_MAX):
        return False

    curled = 0
    for mcp, pip, tip in (
        (HL.MIDDLE_FINGER_MCP, HL.MIDDLE_FINGER_PIP, HL.MIDDLE_FINGER_TIP),
        (HL.RING_FINGER_MCP, HL.RING_FINGER_PIP, HL.RING_FINGER_TIP),
        (HL.PINKY_MCP, HL.PINKY_PIP, HL.PINKY_TIP),
    ):
        ang = _angle_at_b(lm[mcp], lm[pip], lm[tip])
        if _CLAW_FINGER_CURLED_MIN < ang < _CLAW_FINGER_CURLED_MAX:
            curled += 1
    if curled < _CLAW_MIN_CURLED_COUNT:
        return False

    return True


def _claw_thumb_index_inward(lm: list) -> bool:
    """
    Tight claw / pinch: thumb and index tips close, distal segments aim toward each other
    (Shaka-like curl on other fingers, but thumb+index pinch inward — counts as CLAW).
    """
    HL = hand_landmarker.HandLandmark
    span = _hand_span(lm)
    if span < 0.02:
        return False

    d_ti = _dist2_xy(lm[HL.THUMB_TIP], lm[HL.INDEX_FINGER_TIP])
    r = d_ti / span
    if not (_CLAW_INWARD_MIN < r < _CLAW_INWARD_MAX):
        return False

    tip_ip = lm[HL.THUMB_IP]
    tu = lm[HL.THUMB_TIP]
    pip_idx = lm[HL.INDEX_FINGER_PIP]
    iu = lm[HL.INDEX_FINGER_TIP]
    vt = (tu.x - tip_ip.x, tu.y - tip_ip.y)
    vi = (iu.x - pip_idx.x, iu.y - pip_idx.y)
    nt = math.hypot(vt[0], vt[1])
    ni = math.hypot(vi[0], vi[1])
    if nt < 1e-6 or ni < 1e-6:
        return False
    cos_t = (vt[0] * vi[0] + vt[1] * vi[1]) / (nt * ni)
    if cos_t > _CLAW_INWARD_DOT_MAX:
        return False

    # Not vertical thumb-up
    if lm[HL.THUMB_TIP].y < lm[HL.INDEX_FINGER_MCP].y - 0.06:
        return False

    curled = 0
    for mcp, pip, tip in (
        (HL.MIDDLE_FINGER_MCP, HL.MIDDLE_FINGER_PIP, HL.MIDDLE_FINGER_TIP),
        (HL.RING_FINGER_MCP, HL.RING_FINGER_PIP, HL.RING_FINGER_TIP),
        (HL.PINKY_MCP, HL.PINKY_PIP, HL.PINKY_TIP),
    ):
        ang = _angle_at_b(lm[mcp], lm[pip], lm[tip])
        if _CLAW_FINGER_CURLED_MIN < ang < _CLAW_FINGER_CURLED_MAX:
            curled += 1
    if curled < _CLAW_MIN_CURLED_COUNT:
        return False

    return True


def _claw_thumb_pinky_inward(lm: list) -> bool:
    """
    Thumb and pinky tips toward each other (inward); index, middle, and ring
    all down (curled). Distinct from Shaka (wider span, no inward convergence).
    """
    HL = hand_landmarker.HandLandmark
    span = _hand_span(lm)
    if span < 0.02:
        return False

    d_tp = _dist2_xy(lm[HL.THUMB_TIP], lm[HL.PINKY_TIP])
    d_ti = _dist2_xy(lm[HL.THUMB_TIP], lm[HL.INDEX_FINGER_TIP])
    r = d_tp / span
    if not (_CLAW_TP_INWARD_MIN < r < _CLAW_TP_INWARD_MAX):
        return False
    if d_tp >= d_ti * 0.98:
        return False

    idx_ang = _pip_angle(
        lm,
        HL.INDEX_FINGER_MCP,
        HL.INDEX_FINGER_PIP,
        HL.INDEX_FINGER_TIP,
    )
    mid_ang = _pip_angle(
        lm,
        HL.MIDDLE_FINGER_MCP,
        HL.MIDDLE_FINGER_PIP,
        HL.MIDDLE_FINGER_TIP,
    )
    rng_ang = _pip_angle(
        lm,
        HL.RING_FINGER_MCP,
        HL.RING_FINGER_PIP,
        HL.RING_FINGER_TIP,
    )
    if idx_ang > _CURLED_MAX_DEG or mid_ang > _CURLED_MAX_DEG or rng_ang > _CURLED_MAX_DEG:
        return False

    tip_ip = lm[HL.THUMB_IP]
    tu = lm[HL.THUMB_TIP]
    pip_p = lm[HL.PINKY_PIP]
    pu = lm[HL.PINKY_TIP]
    vt = (tu.x - tip_ip.x, tu.y - tip_ip.y)
    vp = (pu.x - pip_p.x, pu.y - pip_p.y)
    nt = math.hypot(vt[0], vt[1])
    ni = math.hypot(vp[0], vp[1])
    if nt < 1e-6 or ni < 1e-6:
        return False
    cos_t = (vt[0] * vp[0] + vt[1] * vp[1]) / (nt * ni)
    if cos_t > _CLAW_TP_INWARD_DOT_MAX:
        return False

    if lm[HL.THUMB_TIP].y < lm[HL.INDEX_FINGER_MCP].y - 0.06:
        return False

    if _is_shaka(lm):
        return False

    return True


def _claw_pose_any(lm: list) -> bool:
    """U-gap claw, thumb–index inward pinch, or thumb–pinky inward."""
    return _claw_u_signature(lm) or _claw_thumb_index_inward(lm) or _claw_thumb_pinky_inward(lm)


def _thumb_gesture(lm: list) -> str | None:
    """Thumb up / down from one hand's normalized landmarks, or None."""
    HL = hand_landmarker.HandLandmark
    tip_y = lm[HL.THUMB_TIP].y
    ip_y = lm[HL.THUMB_IP].y
    idx_mcp_y = lm[HL.INDEX_FINGER_MCP].y

    if tip_y < ip_y - _THUMB_BEND_MARGIN and tip_y < idx_mcp_y - _THUMB_VS_INDEX_MARGIN:
        return "Thumb up"
    if tip_y > ip_y + _THUMB_BEND_MARGIN and tip_y > idx_mcp_y + _THUMB_VS_INDEX_MARGIN:
        return "Thumb down"
    return None


def _pip_angle(lm: list, mcp: int, pip: int, tip: int) -> float:
    return _angle_at_b(lm[mcp], lm[pip], lm[tip])


def _is_fist(lm: list) -> bool:
    HL = hand_landmarker.HandLandmark
    for mcp, pip, tip in (
        (HL.INDEX_FINGER_MCP, HL.INDEX_FINGER_PIP, HL.INDEX_FINGER_TIP),
        (HL.MIDDLE_FINGER_MCP, HL.MIDDLE_FINGER_PIP, HL.MIDDLE_FINGER_TIP),
        (HL.RING_FINGER_MCP, HL.RING_FINGER_PIP, HL.RING_FINGER_TIP),
        (HL.PINKY_MCP, HL.PINKY_PIP, HL.PINKY_TIP),
    ):
        if _pip_angle(lm, mcp, pip, tip) > _CURLED_MAX_DEG:
            return False
    return True


def _is_open_palm(lm: list) -> bool:
    HL = hand_landmarker.HandLandmark
    for mcp, pip, tip in (
        (HL.INDEX_FINGER_MCP, HL.INDEX_FINGER_PIP, HL.INDEX_FINGER_TIP),
        (HL.MIDDLE_FINGER_MCP, HL.MIDDLE_FINGER_PIP, HL.MIDDLE_FINGER_TIP),
        (HL.RING_FINGER_MCP, HL.RING_FINGER_PIP, HL.RING_FINGER_TIP),
        (HL.PINKY_MCP, HL.PINKY_PIP, HL.PINKY_TIP),
    ):
        if _pip_angle(lm, mcp, pip, tip) < _EXTEND_MIN_DEG:
            return False
    return True


def _two_hand_triangle_formation(left_lm: list, right_lm: list) -> bool:
    """
    Both hands combine into one triangle in the image: left/right index tips and the midpoint
    of the two thumb tips (typical \"frame\" / pyramid pose). Hands-only; never driven by face.
    """
    HL = hand_landmarker.HandLandmark
    idx_min = _EXTEND_MIN_DEG - 12.0
    for lm in (left_lm, right_lm):
        if _pip_angle(lm, HL.INDEX_FINGER_MCP, HL.INDEX_FINGER_PIP, HL.INDEX_FINGER_TIP) < idx_min:
            return False
        for mcp, pip, tip in (
            (HL.MIDDLE_FINGER_MCP, HL.MIDDLE_FINGER_PIP, HL.MIDDLE_FINGER_TIP),
            (HL.RING_FINGER_MCP, HL.RING_FINGER_PIP, HL.RING_FINGER_TIP),
            (HL.PINKY_MCP, HL.PINKY_PIP, HL.PINKY_TIP),
        ):
            if _pip_angle(lm, mcp, pip, tip) > _CURLED_MAX_DEG + 12.0:
                return False

    lw = left_lm[HL.WRIST]
    rw = right_lm[HL.WRIST]
    d_wrist = _dist2_xy(lw, rw)
    if not (0.10 <= d_wrist <= 0.62):
        return False

    li = left_lm[HL.INDEX_FINGER_TIP]
    ri = right_lm[HL.INDEX_FINGER_TIP]
    lt = left_lm[HL.THUMB_TIP]
    rt = right_lm[HL.THUMB_TIP]
    cx = 0.5 * (lt.x + rt.x)
    cy = 0.5 * (lt.y + rt.y)

    def _d(ax: float, ay: float, bx: float, by: float) -> float:
        return math.hypot(ax - bx, ay - by)

    e_ab = _d(li.x, li.y, ri.x, ri.y)
    e_ac = _d(li.x, li.y, cx, cy)
    e_bc = _d(ri.x, ri.y, cx, cy)
    m = min(e_ab, e_ac, e_bc)
    if m < 1e-5:
        return False
    if e_ab + e_ac <= e_bc or e_ab + e_bc <= e_ac or e_ac + e_bc <= e_ab:
        return False

    s = (e_ab + e_ac + e_bc) / 2.0
    area_sq = s * (s - e_ab) * (s - e_ac) * (s - e_bc)
    if area_sq <= 0:
        return False
    area = math.sqrt(area_sq)
    span = 0.5 * (_hand_span(left_lm) + _hand_span(right_lm))
    if span < 0.018:
        return False
    ar = area / (span * span)
    if not (0.022 <= ar <= 0.52):
        return False

    c = SimpleNamespace(x=cx, y=cy)
    a_li = _angle_at_b(ri, li, c)
    a_ri = _angle_at_b(li, ri, c)
    a_c = _angle_at_b(li, c, ri)
    for ang in (a_li, a_ri, a_c):
        if ang < 14.0 or ang > 158.0:
            return False
    return True


def _two_hand_triangle_pinch_pose(left_lm: list, right_lm: list) -> bool:
    """
    Diamond / prayer triangle: index fingertips together, thumb tips together, hands separated.
    Matches the common two-hand pose used to arm sphere + interactive OpenClaw.
    """
    HL = hand_landmarker.HandLandmark
    li = left_lm[HL.INDEX_FINGER_TIP]
    ri = right_lm[HL.INDEX_FINGER_TIP]
    lt = left_lm[HL.THUMB_TIP]
    rt = right_lm[HL.THUMB_TIP]
    d_idx = math.hypot(li.x - ri.x, li.y - ri.y)
    d_th = math.hypot(lt.x - rt.x, lt.y - rt.y)
    span = 0.5 * (_hand_span(left_lm) + _hand_span(right_lm))
    if span < 0.018:
        return False
    if d_idx > 0.19 * span or d_th > 0.22 * span:
        return False
    d_wrist = _dist2_xy(left_lm[HL.WRIST], right_lm[HL.WRIST])
    if not (0.11 <= d_wrist <= 0.58):
        return False
    # Middle finger roughly extended (fingers pointing up in the pose).
    for lm in (left_lm, right_lm):
        if _pip_angle(lm, HL.MIDDLE_FINGER_MCP, HL.MIDDLE_FINGER_PIP, HL.MIDDLE_FINGER_TIP) < 95.0:
            return False
    return True


def _thumb_index_close(lm: list) -> bool:
    HL = hand_landmarker.HandLandmark
    span = _hand_span(lm)
    if span < 0.02:
        return False
    d = _dist2_xy(lm[HL.THUMB_TIP], lm[HL.INDEX_FINGER_TIP])
    return d <= _PINCH_SPAN_FRAC * span


def _is_ok_sign(lm: list) -> bool:
    """OK: thumb–index circle; index bent; other three fingers extended."""
    HL = hand_landmarker.HandLandmark
    if not _thumb_index_close(lm):
        return False
    idx_ang = _pip_angle(lm, HL.INDEX_FINGER_MCP, HL.INDEX_FINGER_PIP, HL.INDEX_FINGER_TIP)
    if idx_ang >= _EXTEND_MIN_DEG - 8:
        return False
    for mcp, pip, tip in (
        (HL.MIDDLE_FINGER_MCP, HL.MIDDLE_FINGER_PIP, HL.MIDDLE_FINGER_TIP),
        (HL.RING_FINGER_MCP, HL.RING_FINGER_PIP, HL.RING_FINGER_TIP),
        (HL.PINKY_MCP, HL.PINKY_PIP, HL.PINKY_TIP),
    ):
        if _pip_angle(lm, mcp, pip, tip) < _OK_OTHER_EXTEND_MIN:
            return False
    return True


def _is_peace(lm: list) -> bool:
    HL = hand_landmarker.HandLandmark
    i_ext = _pip_angle(lm, HL.INDEX_FINGER_MCP, HL.INDEX_FINGER_PIP, HL.INDEX_FINGER_TIP) >= _EXTEND_MIN_DEG
    m_ext = _pip_angle(lm, HL.MIDDLE_FINGER_MCP, HL.MIDDLE_FINGER_PIP, HL.MIDDLE_FINGER_TIP) >= _EXTEND_MIN_DEG
    r_cur = _pip_angle(lm, HL.RING_FINGER_MCP, HL.RING_FINGER_PIP, HL.RING_FINGER_TIP) <= _CURLED_MAX_DEG
    p_cur = _pip_angle(lm, HL.PINKY_MCP, HL.PINKY_PIP, HL.PINKY_TIP) <= _CURLED_MAX_DEG
    return i_ext and m_ext and r_cur and p_cur


def _is_point(lm: list) -> bool:
    HL = hand_landmarker.HandLandmark
    i_ext = _pip_angle(lm, HL.INDEX_FINGER_MCP, HL.INDEX_FINGER_PIP, HL.INDEX_FINGER_TIP) >= _EXTEND_MIN_DEG
    m_cur = _pip_angle(lm, HL.MIDDLE_FINGER_MCP, HL.MIDDLE_FINGER_PIP, HL.MIDDLE_FINGER_TIP) <= _CURLED_MAX_DEG
    r_cur = _pip_angle(lm, HL.RING_FINGER_MCP, HL.RING_FINGER_PIP, HL.RING_FINGER_TIP) <= _CURLED_MAX_DEG
    p_cur = _pip_angle(lm, HL.PINKY_MCP, HL.PINKY_PIP, HL.PINKY_TIP) <= _CURLED_MAX_DEG
    return i_ext and m_cur and r_cur and p_cur


def _is_shaka(lm: list) -> bool:
    HL = hand_landmarker.HandLandmark
    p_ext = _pip_angle(lm, HL.PINKY_MCP, HL.PINKY_PIP, HL.PINKY_TIP) >= _EXTEND_MIN_DEG
    t_open = _pip_angle(lm, HL.THUMB_CMC, HL.THUMB_MCP, HL.THUMB_IP) >= 105.0
    i_cur = _pip_angle(lm, HL.INDEX_FINGER_MCP, HL.INDEX_FINGER_PIP, HL.INDEX_FINGER_TIP) <= _CURLED_MAX_DEG
    m_cur = _pip_angle(lm, HL.MIDDLE_FINGER_MCP, HL.MIDDLE_FINGER_PIP, HL.MIDDLE_FINGER_TIP) <= _CURLED_MAX_DEG
    r_cur = _pip_angle(lm, HL.RING_FINGER_MCP, HL.RING_FINGER_PIP, HL.RING_FINGER_TIP) <= _CURLED_MAX_DEG
    return p_ext and t_open and i_cur and m_cur and r_cur


def _is_pinch_only(lm: list) -> bool:
    """Thumb–index pinch, but not a full OK (other fingers not all extended)."""
    if not _thumb_index_close(lm):
        return False
    if _is_ok_sign(lm):
        return False
    if _claw_thumb_index_inward(lm):
        return False
    return True


_GESTURE_PRIORITY: dict[str, int] = {
    "Thumb up": 0,
    "Thumb down": 1,
    "OK": 2,
    "Peace": 3,
    "Point": 4,
    "Shaka": 5,
    "Pinch": 6,
    "Fist": 7,
    "Open palm": 8,
}

# BGR for overlay
_HAND_LABEL_BGR: dict[str, tuple[int, int, int]] = {
    "Claw U": (255, 200, 0),
    "Claw…": (0, 200, 255),
    "Thumb up": (0, 255, 128),
    "Thumb down": (0, 128, 255),
    "OK": (0, 255, 255),
    "Peace": (255, 0, 255),
    "Point": (255, 140, 0),
    "Shaka": (0, 180, 255),
    "Pinch": (200, 200, 255),
    "Fist": (110, 110, 110),
    "Open palm": (140, 255, 140),
}


def _single_hand_everyday_gesture(lm: list) -> str | None:
    """Everyday / informal label for one hand, or None."""
    t = _thumb_gesture(lm)
    if t:
        return t
    if _is_ok_sign(lm):
        return "OK"
    if _is_peace(lm):
        return "Peace"
    if _is_point(lm):
        return "Point"
    if _is_shaka(lm):
        return "Shaka"
    if _is_pinch_only(lm):
        return "Pinch"
    if _is_fist(lm):
        return "Fist"
    if _is_open_palm(lm):
        return "Open palm"
    return None


def _hand_gesture_everyday(hand_landmarks: list) -> str:
    """Best gesture across hands (lower _GESTURE_PRIORITY wins)."""
    if not hand_landmarks:
        return "No hand"
    found: list[str] = []
    for lm in hand_landmarks:
        g = _single_hand_everyday_gesture(lm)
        if g:
            found.append(g)
    if not found:
        return "—"
    return min(found, key=lambda x: _GESTURE_PRIORITY.get(x, 99))


def _right_hand_index(
    hand_landmarks: list,
    handedness: list[list[category_lib.Category]],
) -> int | None:
    for i, _ in enumerate(hand_landmarks):
        cats = handedness[i] if i < len(handedness) else None
        if _is_labeled_right_hand(cats):
            return i
    return None


def _left_hand_index(
    hand_landmarks: list,
    handedness: list[list[category_lib.Category]],
) -> int | None:
    for i, _ in enumerate(hand_landmarks):
        cats = handedness[i] if i < len(handedness) else None
        if _is_labeled_left_hand(cats):
            return i
    return None


def _claw_ml_hand_index(
    hand_landmarks: list,
    handedness: list[list[category_lib.Category]],
) -> int | None:
    """Prefer right hand for ML (typical training setup); use left if right not in frame."""
    ri = _right_hand_index(hand_landmarks, handedness)
    if ri is not None:
        return ri
    return _left_hand_index(hand_landmarks, handedness)


def _raw_claw_heuristic(
    hand_landmarks: list,
    handedness: list[list[category_lib.Category]],
) -> bool:
    """True if left or right hand matches claw pose (U-gap / thumb–index inward / thumb–pinky)."""
    if not hand_landmarks:
        return False
    for i, lm in enumerate(hand_landmarks):
        cats = handedness[i] if i < len(handedness) else None
        if (_is_labeled_right_hand(cats) or _is_labeled_left_hand(cats)) and _claw_pose_any(lm):
            return True
    return False


def _compute_raw_claw(
    hand_landmarks: list,
    handedness: list[list[category_lib.Category]],
    ml_bundle,
    seq_buf: collections.deque,
) -> bool:
    """
    If models/ exist: MLP+LSTM on preferred hand (right if present, else left), OR geometry.
    Geometry OR keeps inward-claw working if ML was trained only on one pose or one side.
    """
    if not hand_landmarks:
        seq_buf.clear()
        return False
    if ml_bundle is not None and claw_features is not None:
        hi = _claw_ml_hand_index(hand_landmarks, handedness)
        if hi is None:
            seq_buf.clear()
            return _raw_claw_heuristic(hand_landmarks, handedness)
        vec = claw_features.landmarks_to_feature_vector(hand_landmarks[hi])
        seq_buf.append(vec)
        _, _, p = ml_bundle.predict_proba_claw(vec, seq_buf)
        ml_hit = p >= ml_bundle.threshold
        heur_hit = _raw_claw_heuristic(hand_landmarks, handedness)
        return ml_hit or heur_hit
    return _raw_claw_heuristic(hand_landmarks, handedness)


def _log_finger_debug(
    hand_landmarks: list,
    handedness: list[list[category_lib.Category]],
    *,
    raw_claw: bool,
    claw_hold_sec: float,
) -> None:
    """Print normalized landmark positions and claw-related metrics (stderr)."""
    HL = hand_landmarker.HandLandmark
    print("--- finger debug ---", file=sys.stderr)
    print(
        f"  claw: raw={raw_claw}  hold_accum={claw_hold_sec:.2f}s / {_CLAW_HOLD_SECONDS:.0f}s",
        file=sys.stderr,
    )
    for i, lm in enumerate(hand_landmarks):
        cats = handedness[i] if i < len(handedness) else None
        label = cats[0].category_name if cats and cats[0].category_name else "?"
        right = _is_labeled_right_hand(cats)
        left = _is_labeled_left_hand(cats)
        claw_u = _claw_u_signature(lm)
        claw_in = _claw_thumb_index_inward(lm)
        claw_tp = _claw_thumb_pinky_inward(lm)
        claw_pose = _claw_pose_any(lm)
        thumb = _thumb_gesture(lm)
        everyday = _single_hand_everyday_gesture(lm)

        def n(idx: int) -> str:
            p = lm[idx]
            z = getattr(p, "z", None)
            if z is not None:
                return f"({p.x:.3f},{p.y:.3f},{z:.3f})"
            return f"({p.x:.3f},{p.y:.3f})"

        span = _hand_span(lm)
        d_ti = _dist2_xy(lm[HL.THUMB_TIP], lm[HL.INDEX_FINGER_TIP])
        r_ti = d_ti / span if span > 1e-6 else 0.0
        d_tp = _dist2_xy(lm[HL.THUMB_TIP], lm[HL.PINKY_TIP])
        idx_ang = _angle_at_b(lm[HL.INDEX_FINGER_MCP], lm[HL.INDEX_FINGER_PIP], lm[HL.INDEX_FINGER_TIP])
        mid_ang = _angle_at_b(lm[HL.MIDDLE_FINGER_MCP], lm[HL.MIDDLE_FINGER_PIP], lm[HL.MIDDLE_FINGER_TIP])
        ring_ang = _angle_at_b(lm[HL.RING_FINGER_MCP], lm[HL.RING_FINGER_PIP], lm[HL.RING_FINGER_TIP])
        pnk_ang = _angle_at_b(lm[HL.PINKY_MCP], lm[HL.PINKY_PIP], lm[HL.PINKY_TIP])

        print(
            f"  hand[{i}] {label}  right={right}  left={left}  "
            f"claw_U={claw_u}  claw_ti={claw_in}  claw_tp={claw_tp}  claw_any={claw_pose}  "
            f"thumb={thumb}  everyday={everyday}",
            file=sys.stderr,
        )
        print(
            f"    wrist{n(HL.WRIST)}  thumb_cmc{n(HL.THUMB_CMC)}  "
            f"thumb_mcp{n(HL.THUMB_MCP)}  thumb_ip{n(HL.THUMB_IP)}  thumb_tip{n(HL.THUMB_TIP)}",
            file=sys.stderr,
        )
        print(
            f"    index  mcp{n(HL.INDEX_FINGER_MCP)}  pip{n(HL.INDEX_FINGER_PIP)}  "
            f"dip{n(HL.INDEX_FINGER_DIP)}  tip{n(HL.INDEX_FINGER_TIP)}",
            file=sys.stderr,
        )
        print(
            f"    mid/rng/pnk tips  {n(HL.MIDDLE_FINGER_TIP)}  "
            f"{n(HL.RING_FINGER_TIP)}  {n(HL.PINKY_TIP)}",
            file=sys.stderr,
        )
        print(
            f"    metrics: span={span:.3f}  d(thumb_tip,index_tip)={d_ti:.3f}  "
            f"ratio={r_ti:.3f}  d(thumb_tip,pinky_tip)={d_tp:.3f}  "
            f"thumb_up_line_y_check tip_y-mcp_y={lm[HL.THUMB_TIP].y - lm[HL.INDEX_FINGER_MCP].y:+.3f}",
            file=sys.stderr,
        )
        print(
            f"    angles_deg: index_pip={idx_ang:.1f}  mid_pip={mid_ang:.1f}  "
            f"ring_pip={ring_ang:.1f}  pinky_pip={pnk_ang:.1f}",
            file=sys.stderr,
        )
    print(file=sys.stderr)


def _draw_labeled_panel(
    frame,
    lines: list[str],
    *,
    x1: int,
    y1: int,
    box_w: int,
    title: str,
    max_lines: int,
    accent_bgr: tuple[int, int, int],
) -> int:
    """
    Draw a titled semi-transparent panel; return bottom y (exclusive).
    """
    if not lines and not title:
        return y1
    pad = 8
    line_h = 17
    title_h = 22
    n = min(len(lines), max_lines) if lines else 0
    box_h = title_h + pad + (n * line_h if n else 0) + pad
    x2, y2 = x1 + box_w, y1 + box_h

    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w - 1, x2), min(h - 1, y2)

    overlay = frame.copy()
    cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 0, 0), thickness=-1)
    cv2.addWeighted(overlay, 0.52, frame, 0.48, 0.0, frame)
    cv2.rectangle(frame, (x1, y1), (x2, y1 + title_h), accent_bgr, thickness=-1)
    cv2.putText(
        frame,
        title[:80],
        (x1 + pad, y1 + title_h - 6),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    y = y1 + title_h + pad + line_h - 3
    for i in range(n):
        s = lines[-n + i]
        cv2.putText(
            frame,
            s[:110],
            (x1 + pad, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (240, 240, 240),
            1,
            cv2.LINE_AA,
        )
        y += line_h
    return y2


def _draw_hand_and_interaction_panels(
    frame,
    hand_lines: list[str],
    interaction_lines: list[str],
) -> int:
    """Left: hands/CLAW. Right: speech + API/TUI. Returns y below both panels."""
    h, w = frame.shape[:2]
    gap = 12
    side_w = max(200, (w - gap - 24) // 2)
    y1 = 10
    x_left = 10
    x_right = x_left + side_w + gap

    bottom_left = _draw_labeled_panel(
        frame,
        hand_lines,
        x1=x_left,
        y1=y1,
        box_w=side_w,
        title="Hands / CLAW",
        max_lines=_HAND_OVERLAY_LINES,
        accent_bgr=(60, 90, 140),
    )
    bottom_right = _draw_labeled_panel(
        frame,
        interaction_lines,
        x1=x_right,
        y1=y1,
        box_w=side_w,
        title="Speech & MasterClaw",
        max_lines=_SPEECH_OVERLAY_LINES,
        accent_bgr=(50, 120, 70),
    )
    return max(bottom_left, bottom_right) + 8


def _append_job_result_to_overlay(
    data: dict,
    log: Callable[[str], None],
    *,
    body_override: str | None = None,
) -> None:
    """Append completed/failed job fields to the interaction panel (chunked).

    If *body_override* is set (e.g. LLM summary for long results), show that instead of raw output.
    """
    status = (data.get("status") or "").lower()
    log(f"Final status: {data.get('status', '?')}")
    if body_override is not None:
        text = body_override[:_JOB_RESULT_MAX_CHARS]
        log(_safe_overlay_text("--- OpenClaw summary ---"))
        for i in range(0, len(text), _JOB_OVERLAY_LINE_CHARS):
            log(_safe_overlay_text(text[i : i + _JOB_OVERLAY_LINE_CHARS]))
        if len(body_override) > _JOB_RESULT_MAX_CHARS:
            log(_safe_overlay_text("… [truncated]"))
        return
    if status == "failed":
        err = data.get("error")
        msg = f"Error: {err!s}"
        log(_safe_overlay_text(msg))
        return

    res = data.get("result") or {}
    if not isinstance(res, dict):
        raw_full = str(res).strip()
        if not raw_full:
            log(_safe_overlay_text("(empty result)"))
            return
        raw = raw_full[:_JOB_RESULT_MAX_CHARS]
        truncated = len(raw_full) > _JOB_RESULT_MAX_CHARS
        for i in range(0, len(raw), _JOB_OVERLAY_LINE_CHARS):
            log(_safe_overlay_text(raw[i : i + _JOB_OVERLAY_LINE_CHARS]))
        if truncated:
            log(_safe_overlay_text("… [truncated]"))
        return

    out = str(res.get("output") or "").strip()
    if not out:
        log(_safe_overlay_text("(empty task output)"))
        return
    out = out[:_JOB_RESULT_MAX_CHARS]
    truncated = len(str(res.get("output") or "").strip()) >= _JOB_RESULT_MAX_CHARS
    for i in range(0, len(out), _JOB_OVERLAY_LINE_CHARS):
        log(_safe_overlay_text(out[i : i + _JOB_OVERLAY_LINE_CHARS]))
    if truncated:
        log(_safe_overlay_text("… [truncated]"))


def _hand_terminate_session_phrase(text: str) -> bool:
    """User wants to end the interactive OpenClaw session (voice)."""
    t = text.lower().strip()
    if not t:
        return False
    if "terminate" in t and "container" in t:
        return True
    if "stop" in t and "container" in t:
        return True
    if "end" in t and "session" in t:
        return True
    return False


def _poll_job_until_terminal(
    api: str,
    job_id: str,
    log: Callable[[str], None],
    *,
    summarize_speech: bool = False,
    speak_results: bool = True,
) -> tuple[str, threading.Thread | None]:
    """GET /subagent/{id} until completed/failed or timeout.

    Returns ``(status, result_tts_thread)``. *result_tts_thread* is the thread that
    speaks the job result (if any); join it before opening the mic for follow-up listen.
    """
    import httpx  # type: ignore

    deadline = time.monotonic() + _JOB_POLL_MAX_SEC
    last_status: str | None = None
    spoken_running_prompt = False
    log("Waiting for job result…")
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(f"{api}/subagent/{job_id}")
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            log(f"Status poll error: {e}")
            time.sleep(_JOB_POLL_INTERVAL_SEC)
            continue
        st = (data.get("status") or "").lower()
        if st != last_status:
            log(f"Job status: {data.get('status', '?')}")
            if (
                st == "running"
                and speak_results
                and not spoken_running_prompt
            ):
                spoken_running_prompt = True

                def _tts_running() -> None:
                    try:
                        from dave_it_guy.voice_tts import speak_hand_demo_output

                        speak_hand_demo_output("Job is running.")
                    except Exception:
                        pass

                threading.Thread(target=_tts_running, daemon=True).start()
            last_status = st
        if st in ("completed", "failed"):
            log("--- output ---")
            tts_text = ""
            result_tts_thread: threading.Thread | None = None
            try:
                from dave_it_guy.voice_summarize import MIN_CHARS_TO_SUMMARIZE
                from dave_it_guy.voice_tts import prepare_spoken_job_result

                tts_text = prepare_spoken_job_result(data, summarize=summarize_speech)
                use_summary_overlay = (
                    summarize_speech
                    and st == "completed"
                    and isinstance(data.get("result"), dict)
                    and len(str((data.get("result") or {}).get("output") or "").strip())
                    >= MIN_CHARS_TO_SUMMARIZE
                )
                if use_summary_overlay:
                    _append_job_result_to_overlay(data, log, body_override=tts_text)
                else:
                    _append_job_result_to_overlay(data, log)
            except Exception:
                try:
                    _append_job_result_to_overlay(data, log)
                except Exception:
                    pass
                try:
                    from dave_it_guy.voice_tts import prepare_spoken_job_result as _prep

                    tts_text = _prep(data, summarize=summarize_speech)
                except Exception:
                    tts_text = ""

            if speak_results and tts_text.strip():

                def _tts(utterance: str) -> None:
                    try:
                        from dave_it_guy.voice_tts import speak_hand_demo_output

                        ok = speak_hand_demo_output(utterance)
                        if not ok and utterance.strip():
                            log(
                                "TTS unavailable: install macOS `say`, or Linux "
                                "`spd-say` / `espeak` (readout was skipped)."
                            )
                    except Exception as e:
                        log(f"TTS error: {e}")

                result_tts_thread = threading.Thread(
                    target=_tts, args=(tts_text,), daemon=True
                )
                result_tts_thread.start()
            elif speak_results and not tts_text.strip() and st == "completed":
                log(
                    "TTS skipped: no speakable text from API result "
                    "(check result shape; overlay may still show output)."
                )
            return st, result_tts_thread
        time.sleep(_JOB_POLL_INTERVAL_SEC)
    log(f"Timed out after {_JOB_POLL_MAX_SEC:.0f}s (check MasterClaw / option 3).")
    return "timeout", None


def main() -> None:
    root = pathlib.Path(__file__).resolve().parent
    hand_path = _ensure_asset(root / _HAND_MODEL_NAME, _HAND_MODEL_URL, _HAND_MODEL_NAME)

    face_roi_filter = _hand_env_bool("DAVE_HAND_FACE_ROI_FILTER", True)
    face_lm = None
    if _ENABLE_FACE_DETECTION or face_roi_filter:
        face_path = _ensure_asset(root / _FACE_MODEL_NAME, _FACE_MODEL_URL, _FACE_MODEL_NAME)
        face_options = face_landmarker.FaceLandmarkerOptions(
            base_options=base_options_lib.BaseOptions(model_asset_path=str(face_path)),
            running_mode=running_mode_lib.VisionTaskRunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=_ENABLE_FACE_DETECTION,
        )
        face_lm = face_landmarker.FaceLandmarker.create_from_options(face_options)

    hand_options = hand_landmarker.HandLandmarkerOptions(
        base_options=base_options_lib.BaseOptions(model_asset_path=str(hand_path)),
        running_mode=running_mode_lib.VisionTaskRunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.62,
        min_hand_presence_confidence=0.55,
        min_tracking_confidence=0.55,
    )
    hand_lm = hand_landmarker.HandLandmarker.create_from_options(hand_options)

    ml_bundle = None
    try:
        import claw_ml

        if claw_features is not None:
            ml_bundle = claw_ml.try_load(root)
        else:
            ml_bundle = None
    except Exception:
        ml_bundle = None
    seq_buf: collections.deque = collections.deque(maxlen=(ml_bundle.seq_len if ml_bundle else 16))

    hconn = hand_landmarker.HandLandmarksConnections
    # Match Sample-Lucia-The-Master / MediaPipe defaults: per-joint dots + colored connections.
    hand_point_style = drawing_styles.get_default_hand_landmarks_style()
    hand_line_style = _hand_connection_styles_for_tasks(
        drawing_styles.get_default_hand_connections_style()
    )

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise SystemExit("Could not open camera index 0. Check permissions and device.")

    label_history: collections.deque[str] = collections.deque(maxlen=10)
    hand_history: collections.deque[str] = collections.deque(maxlen=10)
    hand_overlay_log: collections.deque[str] = collections.deque(maxlen=60)
    interaction_overlay_log: collections.deque[str] = collections.deque(maxlen=80)
    try:
        import os

        _api_hint = os.environ.get("MASTERCLAW_URL", "http://localhost:8090")
    except Exception:
        _api_hint = "http://localhost:8090"
    cube_require_triangle = _hand_env_bool("DAVE_HAND_CUBE_REQUIRE_TRIANGLE", False)
    cube_finger_resize = _hand_env_bool("DAVE_HAND_CUBE_FINGER_RESIZE", True)
    triangle_feature = _hand_env_bool("DAVE_HAND_TRIANGLE_FEATURE", False)
    if face_roi_filter:
        interaction_overlay_log.append(
            "Hand vs face: ROI filter on (DAVE_HAND_FACE_ROI_FILTER=0 disables extra face pass)"
        )
    interaction_overlay_log.append(f"MasterClaw: {_api_hint}")
    interaction_overlay_log.append("Cube hold -> voice -> full OpenClaw job")
    if cube_require_triangle:
        interaction_overlay_log.append(
            "Cube (DAVE_HAND_CUBE_REQUIRE_TRIANGLE=1): triangle arm → fist on; open palm/fist hold; palm rotates"
        )
    else:
        interaction_overlay_log.append(
            "Cube: fist on; then open palm or fist = hold; open palm = move + rotate (Lucia-style)"
        )
    if cube_finger_resize:
        interaction_overlay_log.append("Cube size: finger spread (DAVE_HAND_CUBE_FINGER_RESIZE=0 off)")
    if triangle_feature:
        interaction_overlay_log.append(
            "Sphere/triangle: ON (DAVE_HAND_TRIANGLE_INTERACTIVE / DAVE_HAND_TRIANGLE_TASK)"
        )
    else:
        interaction_overlay_log.append(
            "Sphere/triangle: OFF (set DAVE_HAND_TRIANGLE_FEATURE=1 to enable)"
        )
    try:
        import speech_recognition  # noqa: F401
        speech_ok = True
    except Exception as e:
        speech_ok = False
        interaction_overlay_log.append("Voice: pip install SpeechRecognition")
        interaction_overlay_log.append(f"(missing: {e})")
        interaction_overlay_log.append("Mac mic: brew install portaudio && pip install pyaudio")
    if speech_ok:
        interaction_overlay_log.append(
            "Point (index finger): hold to stop speech / cancel listen "
            "(DAVE_HAND_POINT_INTERRUPTS_VOICE=0 disables)"
        )
    ts_ms = 0
    claw_hold_sec = 0.0
    prev_mono = time.monotonic()
    last_finger_debug_mono = 0.0

    # Cube interaction state per hand side (debounced)
    cube_state = {
        "left": {
            "on": False,
            "on_since": None,
            "f_on": 0,
            "f_off": 0,
            "center": None,
            "last_ms": 0,
            "cube_angle": 0.0,
            "cube_angle_prev_raw": None,
            "cube_pitch_deg": 0.0,
            "cube_size_scale": 1.0,
            "_cube_reset_smooth": False,
            "cube_unseen_streak": 0,
        },
        "right": {
            "on": False,
            "on_since": None,
            "f_on": 0,
            "f_off": 0,
            "center": None,
            "last_ms": 0,
            "cube_angle": 0.0,
            "cube_angle_prev_raw": None,
            "cube_pitch_deg": 0.0,
            "cube_size_scale": 1.0,
            "_cube_reset_smooth": False,
            "cube_unseen_streak": 0,
        },
    }
    sphere_state = {
        "left": {
            "on": False,
            "p_on": 0,
            "s_off": 0,
            "center": None,
            "last_ms": 0,
            "sphere_angle": 0.0,
            "sphere_angle_prev_raw": None,
            "sphere_pitch_deg": 0.0,
            "_sphere_reset_smooth": False,
            "sphere_unseen_streak": 0,
        },
        "right": {
            "on": False,
            "p_on": 0,
            "s_off": 0,
            "center": None,
            "last_ms": 0,
            "sphere_angle": 0.0,
            "sphere_angle_prev_raw": None,
            "sphere_pitch_deg": 0.0,
            "_sphere_reset_smooth": False,
            "sphere_unseen_streak": 0,
        },
    }
    tri_streak = 0
    triangle_arm_until_mono = 0.0
    tri_sphere_streak = 0
    tri_oc_fired_this_hold = False
    sphere_arm_until_mono = 0.0
    last_interactive_oc_fire = 0.0
    hand_interactive_oc_session: dict[str, str | None] = {"job_id": None}
    hand_interactive_oc_lock = threading.Lock()
    voice_point_cancel = threading.Event()
    point_interrupt_streak = 0

    # Background trigger state: cube-hold → listen → create full OpenClaw task
    trigger_lock = threading.Lock()
    trigger_status = {"state": "idle", "detail": "", "last_fire_mono": 0.0}

    hand_cal = HandCalibration(enabled=calibration_enabled_from_env())
    if calibration_enabled_from_env():
        interaction_overlay_log.append("ML calibration: ON (hold threshold learns unsupervised)")

    def _interaction_log(msg: str) -> None:
        with trigger_lock:
            interaction_overlay_log.append(_safe_overlay_text(msg))

    _logged_once_keys: set[str] = set()

    def _interaction_log_once(key: str, msg: str) -> None:
        with trigger_lock:
            if key in _logged_once_keys:
                return
            _logged_once_keys.add(key)
            interaction_overlay_log.append(_safe_overlay_text(msg))

    def _trigger_interactive_openclaw_worker() -> None:
        """POST interactive full OpenClaw; poll each turn like cube flow (Lucia stack pattern)."""
        speak_enable = _hand_env_bool("DAVE_HAND_SPEAK", True)
        summarize_speech = _hand_env_bool("DAVE_HAND_SUMMARIZE_SPEECH", False)

        def _speak_triangle(msg: str) -> None:
            if not speak_enable:
                return

            def _run() -> None:
                try:
                    from dave_it_guy.voice_tts import speak_hand_demo_output

                    speak_hand_demo_output(msg)
                except Exception:
                    pass

            threading.Thread(target=_run, daemon=True).start()

        try:
            import httpx  # type: ignore
        except Exception as e:
            _interaction_log(f"httpx missing: {e}")
            return
        api = os.environ.get("MASTERCLAW_URL", "http://localhost:8090").rstrip("/")
        task = os.environ.get(
            "DAVE_HAND_TRIANGLE_TASK",
            "Interactive hand gesture session; follow user sphere control.",
        )
        followup_task = os.environ.get(
            "DAVE_HAND_TRIANGLE_FOLLOWUP_TASK",
            "Continue the interactive session in the same workspace; build on the prior turn.",
        )
        reuse = _hand_env_bool("DAVE_HAND_TRIANGLE_REUSE_SESSION", True)
        force_new = _hand_env_bool("DAVE_HAND_TRIANGLE_NEW_SESSION", False)
        with hand_interactive_oc_lock:
            existing_jid = None if force_new else hand_interactive_oc_session["job_id"]

        if reuse and existing_jid:
            _interaction_log(
                f"POST {api}/subagent/{existing_jid}/followup (same interactive container)"
            )
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(
                        f"{api}/subagent/{existing_jid}/followup",
                        json={
                            "task": followup_task,
                            "context": None,
                            "timeout_seconds": 300,
                        },
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    jid = data.get("job_id", existing_jid)
                    _interaction_log(f"Interactive follow-up ok job_id={jid}")
                    _speak_triangle("Continuing interactive OpenClaw in the same session.")
                    _poll_job_until_terminal(
                        api,
                        str(jid),
                        _interaction_log,
                        summarize_speech=summarize_speech,
                        speak_results=speak_enable,
                    )
                    return
                if resp.status_code == 409:
                    _interaction_log(
                        "Interactive follow-up skipped: prior turn still running (try again)."
                    )
                    return
                if resp.status_code in (404, 410):
                    with hand_interactive_oc_lock:
                        hand_interactive_oc_session["job_id"] = None
                    _interaction_log(
                        "Prior interactive session ended; starting a new full OpenClaw job."
                    )
                else:
                    resp.raise_for_status()
            except Exception as e:
                _interaction_log(f"Interactive follow-up failed: {e}")
                return

        _interaction_log(
            f"POST {api}/subagent interactive=true use_full_openclaw=true (is MasterClaw up?)"
        )
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{api}/subagent",
                    json={
                        "task": task,
                        "context": None,
                        "model": "llama3.2",
                        "timeout_seconds": 300,
                        "use_full_openclaw": True,
                        "interactive": True,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            jid = data.get("job_id", "?")
            _interaction_log(f"Interactive OpenClaw ok job_id={jid}")
            if jid != "?":
                with hand_interactive_oc_lock:
                    hand_interactive_oc_session["job_id"] = str(jid)
                _interaction_log_once(
                    f"tri_attach_{jid}",
                    f"Attach (same running container): docker exec -it openclaw-subagent-{jid} openclaw tui",
                )
            _speak_triangle("Interactive mode is on. Full OpenClaw job started.")
            if jid != "?":
                _poll_job_until_terminal(
                    api,
                    str(jid),
                    _interaction_log,
                    summarize_speech=summarize_speech,
                    speak_results=speak_enable,
                )
        except Exception as e:
            _interaction_log(f"Interactive OpenClaw failed: {e}")
            _speak_triangle("Interactive OpenClaw could not start. Check the on-screen log.")

    def _trigger_worker() -> None:
        voice_point_cancel.clear()
        speak_enable = _hand_env_bool("DAVE_HAND_SPEAK", True)
        summarize_speech = _hand_env_bool("DAVE_HAND_SUMMARIZE_SPEECH", False)
        allow_cleanup = _hand_env_bool("DAVE_HAND_ALLOW_CLEANUP", False)
        simple_listen = _hand_env_bool("DAVE_HAND_SIMPLE_LISTEN", False)

        def _speak_prompt(msg: str) -> None:
            if not speak_enable:
                return

            def _run() -> None:
                try:
                    from dave_it_guy.voice_tts import speak_hand_demo_output

                    speak_hand_demo_output(msg)
                except Exception:
                    pass

            threading.Thread(target=_run, daemon=True).start()

        with trigger_lock:
            trigger_status["state"] = "listening"
            trigger_status["detail"] = "Listening…"

        try:
            import speech_recognition as sr  # type: ignore
        except Exception as e:
            with trigger_lock:
                trigger_status["state"] = "error"
                trigger_status["detail"] = f"Missing SpeechRecognition: {e}"
            _interaction_log_once("install_sr", "Install: pip install SpeechRecognition")
            _interaction_log_once("install_mac", "(macOS mic: brew install portaudio && pip install pyaudio)")
            _interaction_log_once("import_err", f"Import error: {e}")
            return

        task: str | None = None
        if simple_listen:
            _interaction_log("Listening… (speak task for full OpenClaw)")
            _speak_prompt("Listening. Speak your OpenClaw task.")
            r = sr.Recognizer()
            try:
                from dave_it_guy.voice_assistant import listen_one_phrase_with_cancel

                with sr.Microphone() as source:
                    r.pause_threshold = 1.1
                    r.energy_threshold = 300
                    audio = listen_one_phrase_with_cancel(
                        r,
                        source,
                        phrase_time_limit=40.0,
                        cancel_event=voice_point_cancel,
                        max_wait=8.0,
                    )
                if audio is None:
                    with trigger_lock:
                        trigger_status["state"] = "idle"
                        trigger_status["detail"] = ""
                    _interaction_log("Listen cancelled (point gesture).")
                    return
                task = (r.recognize_google(audio) or "").strip()
            except Exception as e:
                with trigger_lock:
                    trigger_status["state"] = "error"
                    trigger_status["detail"] = f"Listen failed: {e}"
                _interaction_log(f"Listen / STT failed: {e}")
                _speak_prompt("Could not understand audio. Try again.")
                return
        else:
            _interaction_log("--- Task mode (full OpenClaw) ---")
            _interaction_log(
                "You do not need to say Dave again. You can speak in several parts; "
                "after each part, say more or say done when finished."
            )
            _interaction_log("Listening for task… (multipart — say done to finish)")
            _speak_prompt(
                "Describe what the full OpenClaw worker should do. Say done when finished."
            )
            try:
                from dave_it_guy.voice_assistant import (
                    TaskListenCancelledError,
                    listen_task_instruction_multipart,
                )

                task = listen_task_instruction_multipart(
                    emit=_interaction_log,
                    print_heard=True,
                    cancel_event=voice_point_cancel,
                )
            except TaskListenCancelledError as e:
                task = e.partial_text
                if not (task and str(task).strip()):
                    with trigger_lock:
                        trigger_status["state"] = "idle"
                        trigger_status["detail"] = ""
                    _interaction_log("Listen cancelled (point gesture).")
                    return
                _interaction_log(
                    f"Partial task after point interrupt: "
                    f"{str(task)[:160]}{'…' if len(str(task)) > 160 else ''}"
                )
            except RuntimeError as e:
                with trigger_lock:
                    trigger_status["state"] = "error"
                    trigger_status["detail"] = f"Speech service error: {e}"
                _interaction_log(f"Speech recognition service error: {e}")
                return

        if not task:
            with trigger_lock:
                trigger_status["state"] = "error"
                trigger_status["detail"] = "No task heard."
            _interaction_log("No task heard. Try again.")
            _speak_prompt("No task heard. Try again.")
            return

        from dave_it_guy.voice_assistant import (
            api_cleanup,
            api_list_jobs,
            normalize_task_instruction,
            parse_voice_command,
        )

        task_norm = normalize_task_instruction(task)
        _interaction_log(
            f"Task text (combined): {task_norm[:200]}{'…' if len(task_norm) > 200 else ''}"
        )

        cmd = parse_voice_command(task_norm)

        try:
            import httpx  # type: ignore
        except Exception as e:
            with trigger_lock:
                trigger_status["state"] = "error"
                trigger_status["detail"] = f"Missing httpx: {e}"
            _interaction_log(f"httpx missing: {e}")
            return

        base_url = os.environ.get("MASTERCLAW_URL", "http://localhost:8090")
        api = base_url.rstrip("/")
        _interaction_log(f"API: {api}")

        if cmd.kind == "cleanup":
            if not allow_cleanup:
                with trigger_lock:
                    trigger_status["state"] = "error"
                    trigger_status["detail"] = "Cleanup disabled"
                _interaction_log("Cleanup skipped (set DAVE_HAND_ALLOW_CLEANUP=1).")
                _speak_prompt("Cleanup skipped. Set DAVE_HAND_ALLOW_CLEANUP to enable.")
                return
            with trigger_lock:
                trigger_status["state"] = "creating"
                trigger_status["detail"] = "Cleanup…"
            try:
                result = api_cleanup(api)
                with hand_interactive_oc_lock:
                    hand_interactive_oc_session["job_id"] = None
                _interaction_log(f"Cleanup: {result}")
                if speak_enable:

                    def _sp_cleanup() -> None:
                        try:
                            from dave_it_guy.voice_tts import (
                                format_cleanup_for_tts,
                                speak_hand_demo_output,
                            )

                            speak_hand_demo_output(format_cleanup_for_tts(result))
                        except Exception:
                            pass

                    threading.Thread(target=_sp_cleanup, daemon=True).start()
            except Exception as e:
                with trigger_lock:
                    trigger_status["state"] = "error"
                    trigger_status["detail"] = f"Cleanup failed: {e}"
                _interaction_log(f"Cleanup failed: {e}")
                _speak_prompt(f"Cleanup failed: {e}")
                return
            with trigger_lock:
                trigger_status["state"] = "done"
                trigger_status["detail"] = "Cleanup finished"
            return

        if cmd.kind == "list":
            with trigger_lock:
                trigger_status["state"] = "creating"
                trigger_status["detail"] = "Listing jobs…"
            try:
                data = api_list_jobs(api)
                ids = data.get("job_ids", [])
                body = "\n".join(ids) if ids else "(no jobs)"
                for i in range(0, len(body), _JOB_OVERLAY_LINE_CHARS):
                    _interaction_log(_safe_overlay_text(body[i : i + _JOB_OVERLAY_LINE_CHARS]))
                if speak_enable:

                    def _sp_list() -> None:
                        try:
                            from dave_it_guy.voice_tts import (
                                format_jobs_for_tts,
                                speak_hand_demo_output,
                            )

                            speak_hand_demo_output(format_jobs_for_tts(ids))
                        except Exception:
                            pass

                    threading.Thread(target=_sp_list, daemon=True).start()
            except Exception as e:
                with trigger_lock:
                    trigger_status["state"] = "error"
                    trigger_status["detail"] = f"List failed: {e}"
                _interaction_log(f"List jobs failed: {e}")
                _speak_prompt(f"List jobs failed: {e}")
                return
            with trigger_lock:
                trigger_status["state"] = "done"
                trigger_status["detail"] = "Listed jobs"
            return

        post_job_listen = _hand_env_bool("DAVE_HAND_POST_JOB_LISTEN", True)

        while True:
            use_full = True
            task_submit = task_norm
            if cmd.kind == "lightweight":
                use_full = False
                if cmd.task:
                    task_submit = cmd.task
            elif cmd.kind == "full" and cmd.task:
                task_submit = cmd.task
            elif cmd.kind == "need_task_light":
                use_full = False
            elif cmd.kind == "need_task_full":
                use_full = True

            voice_oc_interactive = use_full and _hand_env_bool(
                "DAVE_HAND_VOICE_OPENCLAW_INTERACTIVE", True
            )
            voice_oc_reuse = _hand_env_bool("DAVE_HAND_VOICE_OPENCLAW_REUSE", True)
            voice_oc_force_new = _hand_env_bool("DAVE_HAND_VOICE_OPENCLAW_NEW_SESSION", False)

            with hand_interactive_oc_lock:
                if voice_oc_force_new:
                    voice_existing = None
                elif voice_oc_reuse:
                    voice_existing = hand_interactive_oc_session["job_id"]
                else:
                    voice_existing = None

            used_followup = False
            jid: str | None = None

            if voice_oc_interactive and voice_oc_reuse and voice_existing:
                _interaction_log(
                    f"POST {api}/subagent/{voice_existing}/followup "
                    f"(voice, same interactive OpenClaw container)"
                )
                try:
                    with httpx.Client(timeout=60.0) as client:
                        resp = client.post(
                            f"{api}/subagent/{voice_existing}/followup",
                            json={
                                "task": task_submit,
                                "context": None,
                                "timeout_seconds": 300,
                            },
                        )
                    if resp.status_code == 200:
                        body = resp.json()
                        jid = str(body.get("job_id", voice_existing))
                        used_followup = True
                    elif resp.status_code == 409:
                        with trigger_lock:
                            trigger_status["state"] = "error"
                            trigger_status["detail"] = "Prior turn still running"
                        _interaction_log(
                            "Voice follow-up skipped: prior turn still running. Try again shortly."
                        )
                        return
                    elif resp.status_code in (404, 410):
                        with hand_interactive_oc_lock:
                            hand_interactive_oc_session["job_id"] = None
                        _interaction_log(
                            "Prior interactive session gone; creating a new full OpenClaw job."
                        )
                    else:
                        resp.raise_for_status()
                except Exception as e:
                    with trigger_lock:
                        trigger_status["state"] = "error"
                        trigger_status["detail"] = f"Follow-up failed: {e}"
                    _interaction_log(f"Voice follow-up failed: {e}")
                    return

            if not used_followup:
                with trigger_lock:
                    trigger_status["state"] = "creating"
                    trigger_status["detail"] = (
                        f"Creating {'full OpenClaw' if use_full else 'lightweight'}…"
                    )
                _interaction_log(
                    f"POST /subagent (use_full_openclaw={str(use_full).lower()} "
                    f"interactive={str(voice_oc_interactive).lower()})…"
                )

                payload = {
                    "task": task_submit,
                    "context": None,
                    "model": "llama3.2",
                    "timeout_seconds": 300,
                    "use_full_openclaw": use_full,
                    "interactive": voice_oc_interactive,
                }
                try:
                    with httpx.Client(timeout=60.0) as client:
                        resp = client.post(f"{api}/subagent", json=payload)
                        resp.raise_for_status()
                        create_body = resp.json()
                except Exception as e:
                    with trigger_lock:
                        trigger_status["state"] = "error"
                        trigger_status["detail"] = f"Create failed: {e}"
                    _interaction_log(f"Create job failed: {e}")
                    return

                jid = create_body.get("job_id")
                if not jid:
                    with trigger_lock:
                        trigger_status["state"] = "error"
                        trigger_status["detail"] = "No job_id in response"
                    _interaction_log("No job_id in create response.")
                    return

                if voice_oc_interactive and use_full:
                    with hand_interactive_oc_lock:
                        hand_interactive_oc_session["job_id"] = str(jid)
                    _interaction_log_once(
                        f"voice_attach_{jid}",
                        f"Attach (same running container): docker exec -it "
                        f"openclaw-subagent-{jid} openclaw tui",
                    )

            if jid is None:
                with trigger_lock:
                    trigger_status["state"] = "error"
                    trigger_status["detail"] = "No job id after submit"
                _interaction_log("Internal error: missing job id after submit.")
                return

            with trigger_lock:
                trigger_status["state"] = "running"
                trigger_status["detail"] = f"Job {str(jid)[:12]}…"
            if not used_followup:
                _interaction_log(f"Job created: {jid}")
            else:
                _interaction_log(f"Follow-up accepted for job: {jid}")

            poll_st, job_result_tts_thread = _poll_job_until_terminal(
                api,
                str(jid),
                _interaction_log,
                summarize_speech=summarize_speech,
                speak_results=speak_enable,
            )

            if poll_st == "timeout":
                with trigger_lock:
                    trigger_status["state"] = "error"
                    trigger_status["detail"] = "Job timed out"
                return
            if poll_st == "failed":
                with trigger_lock:
                    trigger_status["state"] = "error"
                    trigger_status["detail"] = "Job failed"
                return

            if not (
                post_job_listen
                and voice_oc_interactive
                and use_full
                and not simple_listen
            ):
                break

            if job_result_tts_thread is not None:
                job_result_tts_thread.join(timeout=600.0)

            _interaction_log(
                "Listening for next instruction... "
                "(say done to finish, or say terminate container)"
            )
            _speak_prompt(
                "Listening for next instruction. Say done when finished, "
                "or say terminate container to end the session."
            )
            with trigger_lock:
                trigger_status["state"] = "listening"
                trigger_status["detail"] = "Next instruction..."

            voice_point_cancel.clear()

            try:
                from dave_it_guy.voice_assistant import (
                    TaskListenCancelledError,
                    listen_task_instruction_multipart,
                )

                task_next = listen_task_instruction_multipart(
                    emit=_interaction_log,
                    print_heard=True,
                    cancel_event=voice_point_cancel,
                )
            except TaskListenCancelledError as e:
                task_next = e.partial_text
                if not (task_next and str(task_next).strip()):
                    with trigger_lock:
                        trigger_status["state"] = "idle"
                        trigger_status["detail"] = ""
                    _interaction_log("Next instruction cancelled (point gesture).")
                    break
                _interaction_log(
                    f"Partial next instruction after point: "
                    f"{str(task_next)[:160]}{'...' if len(str(task_next)) > 160 else ''}"
                )

            if not task_next or not str(task_next).strip():
                _interaction_log("No next instruction — ending follow-up loop.")
                break

            task_norm = normalize_task_instruction(task_next)
            _interaction_log(
                f"Next task text (combined): {task_norm[:200]}"
                f"{'...' if len(task_norm) > 200 else ''}"
            )

            if _hand_terminate_session_phrase(task_norm):
                with hand_interactive_oc_lock:
                    hand_interactive_oc_session["job_id"] = None
                _interaction_log("Interactive session ended (terminate container).")
                _speak_prompt("Session ended.")
                break

            cmd = parse_voice_command(task_norm)

            if cmd.kind == "cleanup":
                if not allow_cleanup:
                    with trigger_lock:
                        trigger_status["state"] = "error"
                        trigger_status["detail"] = "Cleanup disabled"
                    _interaction_log("Cleanup skipped (set DAVE_HAND_ALLOW_CLEANUP=1).")
                    _speak_prompt("Cleanup skipped. Set DAVE_HAND_ALLOW_CLEANUP to enable.")
                    return
                with trigger_lock:
                    trigger_status["state"] = "creating"
                    trigger_status["detail"] = "Cleanup…"
                try:
                    result = api_cleanup(api)
                    with hand_interactive_oc_lock:
                        hand_interactive_oc_session["job_id"] = None
                    _interaction_log(f"Cleanup: {result}")
                    if speak_enable:

                        def _sp_cleanup2() -> None:
                            try:
                                from dave_it_guy.voice_tts import (
                                    format_cleanup_for_tts,
                                    speak_hand_demo_output,
                                )

                                speak_hand_demo_output(format_cleanup_for_tts(result))
                            except Exception:
                                pass

                        threading.Thread(target=_sp_cleanup2, daemon=True).start()
                except Exception as e:
                    with trigger_lock:
                        trigger_status["state"] = "error"
                        trigger_status["detail"] = f"Cleanup failed: {e}"
                    _interaction_log(f"Cleanup failed: {e}")
                    _speak_prompt(f"Cleanup failed: {e}")
                    return
                with trigger_lock:
                    trigger_status["state"] = "done"
                    trigger_status["detail"] = "Cleanup finished"
                return

            if cmd.kind == "list":
                with trigger_lock:
                    trigger_status["state"] = "creating"
                    trigger_status["detail"] = "Listing jobs…"
                try:
                    data = api_list_jobs(api)
                    ids = data.get("job_ids", [])
                    body = "\n".join(ids) if ids else "(no jobs)"
                    for i in range(0, len(body), _JOB_OVERLAY_LINE_CHARS):
                        _interaction_log(_safe_overlay_text(body[i : i + _JOB_OVERLAY_LINE_CHARS]))
                    if speak_enable:

                        def _sp_list2() -> None:
                            try:
                                from dave_it_guy.voice_tts import (
                                    format_jobs_for_tts,
                                    speak_hand_demo_output,
                                )

                                speak_hand_demo_output(format_jobs_for_tts(ids))
                            except Exception:
                                pass

                        threading.Thread(target=_sp_list2, daemon=True).start()
                except Exception as e:
                    with trigger_lock:
                        trigger_status["state"] = "error"
                        trigger_status["detail"] = f"List failed: {e}"
                    _interaction_log(f"List jobs failed: {e}")
                    _speak_prompt(f"List jobs failed: {e}")
                    return
                with trigger_lock:
                    trigger_status["state"] = "done"
                    trigger_status["detail"] = "Listed jobs"
                return

        with trigger_lock:
            if voice_oc_interactive and use_full:
                trigger_status["state"] = "idle"
                trigger_status["detail"] = ""
                trigger_status["last_fire_mono"] = (
                    time.monotonic() - _CUBE_TRIGGER_COOLDOWN_SECONDS
                )
            else:
                trigger_status["state"] = "done"
                trigger_status["detail"] = "Job finished"

    try:
        while True:
            try:
                ok, frame = cap.read()
                if not ok:
                    break
            except KeyboardInterrupt:
                break

            h, w = frame.shape[:2]
            now_mono = time.monotonic()
            dt = min(now_mono - prev_mono, _DT_CAP)
            prev_mono = now_mono

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp_image_lib.Image(image_format=mp_image_lib.ImageFormat.SRGB, data=rgb)

            face_bounds_norm: tuple[float, float, float, float] | None = None
            label = "Face disabled" if not _ENABLE_FACE_DETECTION else "No face"
            hand_label = "No hand"

            if face_lm is not None:
                face_result = face_lm.detect_for_video(mp_image, ts_ms)
                if face_result.face_landmarks:
                    fl0 = face_result.face_landmarks[0]
                    face_bounds_norm = _norm_axis_bounds_from_lm(fl0)
                if _ENABLE_FACE_DETECTION and face_result.face_landmarks:
                    blend = face_result.face_blendshapes[0] if face_result.face_blendshapes else None
                    raw = _expression_from_blendshapes(blend)
                    label_history.append(raw)
                    label = collections.Counter(label_history).most_common(1)[0][0]

            hand_result = hand_lm.detect_for_video(mp_image, ts_ms)
            ts_ms += 33

            hands_lm, hands_h = _filter_hands_not_face_proxy(
                list(hand_result.hand_landmarks or []),
                list(hand_result.handedness or []),
                face_bounds_norm,
            )

            raw_claw = False
            if hands_lm:
                raw_claw = _compute_raw_claw(
                    hands_lm,
                    hands_h,
                    ml_bundle,
                    seq_buf,
                )
                if raw_claw:
                    claw_hold_sec += dt
                else:
                    claw_hold_sec = 0.0

                if claw_hold_sec >= _CLAW_HOLD_SECONDS:
                    hand_history.append("Claw U")
                elif raw_claw:
                    hand_history.append("Claw…")
                else:
                    hand_history.append(_hand_gesture_everyday(hands_lm))
                hand_label = collections.Counter(hand_history).most_common(1)[0][0]
            else:
                claw_hold_sec = 0.0
                seq_buf.clear()
                hand_history.append("No hand")
                hand_label = collections.Counter(hand_history).most_common(1)[0][0]

            if hands_lm and _hand_env_bool("DAVE_HAND_POINT_INTERRUPTS_VOICE", True):
                any_point = any(_is_point(lm) for lm in hands_lm)
                if any_point:
                    point_interrupt_streak += 1
                else:
                    point_interrupt_streak = 0
                if point_interrupt_streak >= _POINT_INTERRUPT_STREAK_FRAMES:
                    try:
                        from dave_it_guy.voice_tts import stop_hand_demo_speech

                        stop_hand_demo_speech()
                    except Exception:
                        pass
                    voice_point_cancel.set()
            else:
                point_interrupt_streak = 0

            claw_progress_text = ""
            if hands_lm and raw_claw and claw_hold_sec < _CLAW_HOLD_SECONDS:
                claw_progress_text = f"Claw hold: {claw_hold_sec:.1f} / {_CLAW_HOLD_SECONDS:.1f}s"

            # Build on-screen "terminal output" (keep it compact).
            if now_mono - last_finger_debug_mono >= _DEBUG_FINGER_INTERVAL_SEC:
                last_finger_debug_mono = now_mono
                if hands_lm:
                    hands_seen = []
                    for i, lm in enumerate(hands_lm):
                        cats = hands_h[i] if i < len(hands_h) else None
                        side = (cats[0].category_name if cats and cats[0].category_name else "?")
                        g = _single_hand_everyday_gesture(lm) or "—"
                        hands_seen.append(f"{side}:{g}")
                    hands_s = "  ".join(hands_seen)
                else:
                    hands_s = "No hands"
                claw_line = (
                    f"CLAW: raw={raw_claw} hold={claw_hold_sec:.1f}/{_CLAW_HOLD_SECONDS:.0f}s"
                )
                hand_overlay_log.append(claw_line)
                hand_overlay_log.append(f"Hands: {hands_s}")
                if claw_progress_text:
                    hand_overlay_log.append(claw_progress_text)

            color = (0, 255, 0) if label == "Smile" else (0, 165, 255) if label == "Sad" else (200, 200, 200)
            hand_color = _HAND_LABEL_BGR.get(hand_label, (180, 180, 180))

            def _draw_sphere_at(
                cx: int,
                cy: int,
                *,
                color_s: tuple[int, int, int],
                angle_deg: float = 0.0,
                pitch_deg: float = 0.0,
            ) -> None:
                pr = math.radians(float(pitch_deg))
                depth = 1.0 + 0.2 * math.sin(pr)
                r = max(14, int(min(w, h) * 0.11 * max(0.88, min(1.12, depth))))
                cv2.circle(frame, (cx, cy), r, color_s, 2, cv2.LINE_AA)
                ax_maj = max(5, int(r * 0.92))
                ax_min = max(4, int(r * 0.48))
                cv2.ellipse(
                    frame,
                    (cx, cy),
                    (ax_maj, ax_min),
                    angle_deg,
                    0,
                    360,
                    color_s,
                    2,
                    cv2.LINE_AA,
                )
                cv2.ellipse(
                    frame,
                    (cx, cy),
                    (ax_min, ax_maj),
                    angle_deg + 90.0,
                    0,
                    360,
                    color_s,
                    2,
                    cv2.LINE_AA,
                )

            def _draw_cube_at(
                cx: int,
                cy: int,
                *,
                color_cube: tuple[int, int, int],
                angle_deg: float = 0.0,
                size_scale: float = 1.0,
                pitch_deg: float = 0.0,
            ) -> None:
                # Cube is half the previous size (was 0.5 of min dimension); size_scale from finger spread.
                ss = max(0.4, min(1.75, float(size_scale)))
                side = int(min(w, h) * 0.25 * ss)
                side = max(14, min(side, int(min(w, h) * 0.48)))
                half = side // 2
                fx1, fy1 = cx - half, cy - half
                fx2, fy2 = cx + half, cy + half
                # Back-face offset + vertical skew: pitch reads as forward/back roll, not only depth.
                pr = math.radians(float(pitch_deg))
                off = max(10, int(side * 0.18 * (1.0 + 0.88 * math.sin(pr))))
                bx1, by1 = fx1 - off, fy1 - off
                bx2, by2 = fx2 - off, fy2 - off

                rad = math.radians(angle_deg)
                cr, sr = math.cos(rad), math.sin(rad)

                def _rot(px: float, py: float) -> tuple[int, int]:
                    dx, dy = px - cx, py - cy
                    rx = cx + dx * cr - dy * sr
                    ry = cy + dx * sr + dy * cr
                    return int(round(rx)), int(round(ry))

                skew = int(round(side * 0.24 * math.sin(pr)))

                def _skew_front_top(pt: tuple[int, int]) -> tuple[int, int]:
                    return pt[0], pt[1] - skew

                def _skew_front_bot(pt: tuple[int, int]) -> tuple[int, int]:
                    return pt[0], pt[1] + skew

                # Front face (near)
                p0 = _skew_front_top(_rot(fx1, fy1))
                p1 = _skew_front_top(_rot(fx2, fy1))
                p2 = _skew_front_bot(_rot(fx2, fy2))
                p3 = _skew_front_bot(_rot(fx1, fy2))
                # Back face (offset)
                q0 = _skew_front_top(_rot(bx1, by1))
                q1 = _skew_front_top(_rot(bx2, by1))
                q2 = _skew_front_bot(_rot(bx2, by2))
                q3 = _skew_front_bot(_rot(bx1, by2))

                thickness = 3
                for a, b in ((p0, p1), (p1, p2), (p2, p3), (p3, p0)):
                    cv2.line(frame, a, b, color_cube, thickness, cv2.LINE_AA)
                for a, b in ((q0, q1), (q1, q2), (q2, q3), (q3, q0)):
                    cv2.line(frame, a, b, color_cube, thickness, cv2.LINE_AA)
                for a, b in ((p0, q0), (p1, q1), (p2, q2), (p3, q3)):
                    cv2.line(frame, a, b, color_cube, thickness, cv2.LINE_AA)

            # Debounced per-hand cube interaction (left and right independently).
            now_ms = int(time.time() * 1000)
            seen_side = {"left": False, "right": False}
            left_h: list | None = None
            right_h: list | None = None
            if hands_lm:
                for i, lm in enumerate(hands_lm):
                    cats = hands_h[i] if i < len(hands_h) else None
                    hand_name = ((cats[0].category_name if cats and cats[0].category_name else "").lower())
                    if "left" in hand_name and left_h is None:
                        left_h = lm
                    elif "right" in hand_name and right_h is None:
                        right_h = lm
            if cube_require_triangle:
                if left_h is not None and right_h is not None and _two_hand_triangle_formation(
                    left_h, right_h
                ):
                    tri_streak += 1
                else:
                    tri_streak = 0
                if tri_streak >= _CUBE_TRIANGLE_ON_FRAMES:
                    triangle_arm_until_mono = now_mono + _CUBE_TRIANGLE_ARM_SECONDS
                fist_may_start_cube = now_mono < triangle_arm_until_mono
            else:
                fist_may_start_cube = False

            if triangle_feature:
                tri_ok_sphere = False
                if left_h is not None and right_h is not None:
                    tri_ok_sphere = _two_hand_triangle_pinch_pose(left_h, right_h) or _two_hand_triangle_formation(
                        left_h, right_h
                    )
                if tri_ok_sphere:
                    tri_sphere_streak += 1
                else:
                    tri_sphere_streak = 0
                    tri_oc_fired_this_hold = False
                if tri_ok_sphere and tri_sphere_streak >= _TRIANGLE_SPHERE_STREAK_FRAMES:
                    sphere_arm_until_mono = now_mono + _SPHERE_ARM_SECONDS
                    if (
                        not tri_oc_fired_this_hold
                        and _hand_env_bool("DAVE_HAND_TRIANGLE_INTERACTIVE", True)
                        and (now_mono - last_interactive_oc_fire >= _INTERACTIVE_OC_COOLDOWN_SEC)
                    ):
                        tri_oc_fired_this_hold = True
                        last_interactive_oc_fire = now_mono
                        threading.Thread(
                            target=_trigger_interactive_openclaw_worker, daemon=True
                        ).start()
                sphere_armed = now_mono < sphere_arm_until_mono
            else:
                sphere_armed = False

            if hands_lm:
                for i, lm in enumerate(hands_lm):
                    cats = hands_h[i] if i < len(hands_h) else None
                    hand_name = ((cats[0].category_name if cats and cats[0].category_name else "").lower())
                    side = "left" if "left" in hand_name else "right" if "right" in hand_name else None
                    if side is None:
                        continue
                    seen_side[side] = True
                    st = cube_state[side]
                    st["cube_unseen_streak"] = 0

                    # Cube logic uses raw finger geometry — not the overlay label (thumb/OK/etc.
                    # can win priority over "Open palm" while the hand is still an open palm).
                    is_fist_lm = _is_fist(lm)
                    is_palm_lm = _is_open_palm(lm)

                    # Turning ON: consecutive fist frames. If ``DAVE_HAND_CUBE_REQUIRE_TRIANGLE=1``,
                    # fist only counts after the two-hand triangle has armed the window.
                    can_count_fist_on = st["on"] or (not cube_require_triangle) or fist_may_start_cube
                    if is_fist_lm and can_count_fist_on:
                        st["f_on"] += 1
                        st["f_off"] = 0
                    else:
                        st["f_on"] = 0

                    # While cube is ON, fist or open palm (landmarks) keeps the cube alive.
                    if st["on"]:
                        if is_fist_lm or is_palm_lm:
                            st["f_off"] = 0
                        else:
                            st["f_off"] += 1
                    else:
                        # Cube is OFF: non-fist frames count toward staying off.
                        if not is_fist_lm:
                            st["f_off"] += 1

                    if (not st["on"]) and st["f_on"] >= _CUBE_FIST_ON_FRAMES:
                        st["on"] = True
                        st["on_since"] = now_mono
                        st["_cube_reset_smooth"] = True
                    if st["on"] and st["f_off"] >= _CUBE_FIST_OFF_FRAMES:
                        st["on"] = False
                        st["on_since"] = None
                        st["cube_angle"] = 0.0
                        st["cube_angle_prev_raw"] = None
                        st["cube_pitch_deg"] = 0.0
                        st.pop("_cube_angle_delta_ema", None)
                        st["cube_size_scale"] = 1.0

                    # Update center while the hand is visible. Open palm: anchor to palm pad;
                    # fist: bbox center. While cube is ON, always refresh last_ms so brief
                    # gesture flicker does not hide the cube (still need fist/palm for f_off).
                    if st["on"]:
                        st["last_ms"] = now_ms
                        if is_palm_lm:
                            raw_c = _palm_center_screen_px(lm, w, h)
                            if st.get("_cube_reset_smooth"):
                                st["center"] = raw_c
                                st["_cube_reset_smooth"] = False
                            else:
                                st["center"] = _smooth_center_xy(
                                    st.get("center"),
                                    raw_c,
                                    alpha=_CUBE_CENTER_EMA_PALM,
                                )
                        elif is_fist_lm:
                            raw_c = _hand_bbox_center_screen_px(lm, w, h)
                            if st.get("_cube_reset_smooth"):
                                st["center"] = raw_c
                                st["_cube_reset_smooth"] = False
                            else:
                                st["center"] = _smooth_center_xy(
                                    st.get("center"),
                                    raw_c,
                                    alpha=_CUBE_CENTER_EMA_FIST,
                                )
                        # else: ambiguous gesture — keep previous center; last_ms already set
                    else:
                        raw_c = _hand_bbox_center_screen_px(lm, w, h)
                        st["center"] = raw_c
                        st["last_ms"] = now_ms

                    # Palm: continuous in-plane rotation (integrate deltas → full 360°+) + pitch depth.
                    if st["on"] and is_palm_lm:
                        raw_ang = _palm_hand_rotation_deg(lm)
                        prev_raw = st.get("cube_angle_prev_raw")
                        if prev_raw is None:
                            st["cube_angle"] = float(raw_ang)
                            st["cube_angle_prev_raw"] = float(raw_ang)
                        else:
                            d = float(raw_ang) - float(prev_raw)
                            if d > 180.0:
                                d -= 360.0
                            elif d < -180.0:
                                d += 360.0
                            prev_d = float(st.get("_cube_angle_delta_ema") or d)
                            d_sm = (1.0 - _CUBE_ANGLE_UNWRAP_DELTA_EMA) * prev_d + _CUBE_ANGLE_UNWRAP_DELTA_EMA * d
                            st["_cube_angle_delta_ema"] = d_sm
                            st["cube_angle"] = float(st["cube_angle"]) + d_sm
                            st["cube_angle_prev_raw"] = float(raw_ang)
                        pitch_raw = _palm_pitch_deg(lm)
                        st["cube_pitch_deg"] = _smooth_angle(
                            st.get("cube_pitch_deg"),
                            pitch_raw,
                            _CUBE_ANGLE_EMA_PALM,
                        )
                    elif st["on"] and is_fist_lm:
                        # Next open palm re-syncs in-plane angle (avoids huge delta after moving in fist).
                        st["cube_angle_prev_raw"] = None

                    if st["on"] and cube_finger_resize:
                        spread = _finger_mean_tip_wrist_dist(lm)
                        denom = _CUBE_SPREAD_HI - _CUBE_SPREAD_LO
                        t = (spread - _CUBE_SPREAD_LO) / denom if denom > 1e-9 else 0.5
                        t = max(0.0, min(1.0, t))
                        target = _CUBE_SCALE_MIN + t * (_CUBE_SCALE_MAX - _CUBE_SCALE_MIN)
                        prev = float(st.get("cube_size_scale") or 1.0)
                        st["cube_size_scale"] = (1.0 - _CUBE_SIZE_EMA) * prev + _CUBE_SIZE_EMA * target

                    ss = sphere_state[side]
                    if not sphere_armed:
                        ss["on"] = False
                        ss["p_on"] = 0
                        ss["s_off"] = 0
                        ss["sphere_angle"] = 0.0
                        ss["sphere_angle_prev_raw"] = None
                        ss["sphere_pitch_deg"] = 0.0
                        ss.pop("_sphere_angle_delta_ema", None)
                    else:
                        ss["sphere_unseen_streak"] = 0
                        if not ss["on"]:
                            if is_palm_lm:
                                ss["p_on"] = int(ss.get("p_on") or 0) + 1
                                ss["s_off"] = 0
                            else:
                                ss["p_on"] = 0
                                ss["s_off"] = int(ss.get("s_off") or 0) + 1
                        else:
                            if is_palm_lm:
                                ss["s_off"] = 0
                            else:
                                ss["s_off"] = int(ss.get("s_off") or 0) + 1
                        if (not ss["on"]) and ss["p_on"] >= _SPHERE_PALM_ON_FRAMES:
                            ss["on"] = True
                            ss["_sphere_reset_smooth"] = True
                        if ss["on"] and ss["s_off"] >= _SPHERE_OFF_FRAMES:
                            ss["on"] = False
                            ss["sphere_angle"] = 0.0
                            ss["sphere_angle_prev_raw"] = None
                            ss["sphere_pitch_deg"] = 0.0
                            ss.pop("_sphere_angle_delta_ema", None)
                        if ss["on"]:
                            ss["last_ms"] = now_ms
                            if is_palm_lm:
                                raw_s = _palm_center_screen_px(lm, w, h)
                                if ss.get("_sphere_reset_smooth"):
                                    ss["center"] = raw_s
                                    ss["_sphere_reset_smooth"] = False
                                else:
                                    ss["center"] = _smooth_center_xy(
                                        ss.get("center"),
                                        raw_s,
                                        alpha=_CUBE_CENTER_EMA_PALM,
                                    )
                                raw_sa = _palm_hand_rotation_deg(lm)
                                prev_s = ss.get("sphere_angle_prev_raw")
                                if prev_s is None:
                                    ss["sphere_angle"] = float(raw_sa)
                                    ss["sphere_angle_prev_raw"] = float(raw_sa)
                                else:
                                    d = float(raw_sa) - float(prev_s)
                                    if d > 180.0:
                                        d -= 360.0
                                    elif d < -180.0:
                                        d += 360.0
                                    prev_d = float(ss.get("_sphere_angle_delta_ema") or d)
                                    d_sm = (
                                        (1.0 - _CUBE_ANGLE_UNWRAP_DELTA_EMA) * prev_d
                                        + _CUBE_ANGLE_UNWRAP_DELTA_EMA * d
                                    )
                                    ss["_sphere_angle_delta_ema"] = d_sm
                                    ss["sphere_angle"] = float(ss["sphere_angle"]) + d_sm
                                    ss["sphere_angle_prev_raw"] = float(raw_sa)
                                pitch_s = _palm_pitch_deg(lm)
                                ss["sphere_pitch_deg"] = _smooth_angle(
                                    ss.get("sphere_pitch_deg"),
                                    pitch_s,
                                    _CUBE_ANGLE_EMA_PALM,
                                )
                            elif is_fist_lm:
                                ss["sphere_angle_prev_raw"] = None

            # If a side wasn't seen this frame, count it as non-fist (for turn-off),
            # but allow short dropouts while keeping the cube at last center.
            for side in ("left", "right"):
                st = cube_state[side]
                if not seen_side[side]:
                    st["cube_unseen_streak"] = int(st.get("cube_unseen_streak") or 0) + 1
                    st["f_on"] = 0
                    # Short tracking gaps while rotating: grace before counting as "off" frames.
                    if (
                        st["on"]
                        and st["cube_unseen_streak"] <= _CUBE_UNSEEN_GRACE_FRAMES
                    ):
                        # Keep last_ms fresh so the draw path does not drop the cube at 350ms
                        # while MediaPipe briefly loses the hand during rotation.
                        st["last_ms"] = now_ms
                    else:
                        st["f_off"] += 1
                    if st["on"] and st["f_off"] >= _CUBE_FIST_OFF_FRAMES:
                        st["on"] = False
                        st["on_since"] = None
                        st["cube_angle"] = 0.0
                        st["cube_angle_prev_raw"] = None
                        st["cube_pitch_deg"] = 0.0
                        st.pop("_cube_angle_delta_ema", None)
                        st["cube_size_scale"] = 1.0

            if sphere_armed:
                for side in ("left", "right"):
                    ss = sphere_state[side]
                    if not seen_side[side]:
                        ss["sphere_unseen_streak"] = int(ss.get("sphere_unseen_streak") or 0) + 1
                        ss["p_on"] = 0
                        if (
                            ss["on"]
                            and ss["sphere_unseen_streak"] <= _SPHERE_UNSEEN_GRACE_FRAMES
                        ):
                            ss["last_ms"] = now_ms
                        else:
                            ss["s_off"] = int(ss.get("s_off") or 0) + 1
                        if ss["on"] and ss["s_off"] >= _SPHERE_OFF_FRAMES:
                            ss["on"] = False
                            ss["sphere_angle"] = 0.0
                            ss["sphere_angle_prev_raw"] = None
                            ss["sphere_pitch_deg"] = 0.0
                            ss.pop("_sphere_angle_delta_ema", None)

            # When no cube is visible/on, clear terminal states so the user can retry after releasing.
            any_cube_on = any(cube_state[s]["on"] for s in ("left", "right"))
            with trigger_lock:
                if not any_cube_on and trigger_status["state"] in ("error", "done"):
                    trigger_status["state"] = "idle"
                    trigger_status["detail"] = ""

            # Trigger: after cube has been ON for a short hold, start voice→full OpenClaw.
            # Only fire when idle (not after error/done until cube was released — reset above).
            # Do not re-fire while state is error with cube still held (prevents log spam).
            with trigger_lock:
                state = trigger_status["state"]
                last_fire = float(trigger_status["last_fire_mono"] or 0.0)
            trigger_hold_sec = hand_cal.get_trigger_hold_seconds(_CUBE_TRIGGER_HOLD_SECONDS)
            held = False
            for side in ("left", "right"):
                st = cube_state[side]
                if st["on"] and st.get("on_since") is not None:
                    if (now_mono - float(st["on_since"])) >= trigger_hold_sec:
                        held = True
                        break
            if (
                speech_ok
                and state == "idle"
                and (now_mono - last_fire) >= _CUBE_TRIGGER_COOLDOWN_SECONDS
                and held
            ):
                hold_sec = 0.0
                for side in ("left", "right"):
                    st = cube_state[side]
                    if st["on"] and st.get("on_since") is not None:
                        hold_sec = max(hold_sec, now_mono - float(st["on_since"]))
                hand_cal.record_trigger_hold_seconds(hold_sec)
                with trigger_lock:
                    trigger_status["last_fire_mono"] = now_mono
                    trigger_status["state"] = "starting"
                    trigger_status["detail"] = "Triggering voice…"
                _interaction_log_once("cube_start", "Cube held: starting voice → full OpenClaw")
                threading.Thread(target=_trigger_worker, daemon=True).start()
            elif (
                not speech_ok
                and held
                and (now_mono - last_fire) >= _CUBE_TRIGGER_COOLDOWN_SECONDS
            ):
                # One-time hint if user holds cube but SpeechRecognition is not installed
                _interaction_log_once(
                    "need_speech_pkg",
                    "Voice unavailable: pip install SpeechRecognition (then restart this demo)",
                )

            # Draw cubes for sides that are ON (optionally held during short dropouts).
            for side in ("left", "right"):
                st = cube_state[side]
                if not st["on"] or st["center"] is None:
                    continue
                if now_ms - int(st["last_ms"] or 0) > _CUBE_HOLD_MS_IF_LOST and not seen_side[side]:
                    continue
                cube_color = (255, 0, 255) if side == "left" else (255, 255, 0)
                cx, cy = st["center"]
                _draw_cube_at(
                    cx,
                    cy,
                    color_cube=cube_color,
                    angle_deg=float(st.get("cube_angle") or 0.0),
                    size_scale=float(st.get("cube_size_scale") or 1.0)
                    if cube_finger_resize
                    else 1.0,
                    pitch_deg=float(st.get("cube_pitch_deg") or 0.0),
                )
            for side in ("left", "right"):
                ss = sphere_state[side]
                if not ss["on"] or ss.get("center") is None:
                    continue
                if now_ms - int(ss["last_ms"] or 0) > _CUBE_HOLD_MS_IF_LOST and not seen_side[side]:
                    continue
                sc = (0, 255, 140) if side == "left" else (0, 200, 255)
                sx, sy = ss["center"]
                _draw_sphere_at(
                    sx,
                    sy,
                    color_s=sc,
                    angle_deg=float(ss.get("sphere_angle") or 0.0),
                    pitch_deg=float(ss.get("sphere_pitch_deg") or 0.0),
                )
            # Draw hand skeletons last so wireframes stay visible on top of cube/sphere.
            if hands_lm:
                for hlm in hands_lm:
                    drawing_utils.draw_landmarks(
                        frame,
                        _tasks_landmarks_to_proto_for_draw(hlm),
                        _task_connections_to_tuples(hconn.HAND_CONNECTIONS),
                        landmark_drawing_spec=hand_point_style,
                        connection_drawing_spec=hand_line_style,
                    )
            panel_bottom = _draw_hand_and_interaction_panels(
                frame,
                list(hand_overlay_log),
                list(interaction_overlay_log),
            )
            ty = max(panel_bottom, 36)
            cv2.putText(
                frame,
                f"Expression: {label}",
                (16, ty),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                color,
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                f"Hand: {hand_label}",
                (16, ty + 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                hand_color,
                2,
                cv2.LINE_AA,
            )
            if claw_progress_text:
                cv2.putText(
                    frame,
                    claw_progress_text,
                    (16, ty + 80),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.75,
                    (0, 220, 255),
                    2,
                    cv2.LINE_AA,
                )
            claw_mode = "Claw detect: MLP + LSTM (ensemble)" if ml_bundle is not None else "Claw detect: geometry (no models/)"
            cv2.putText(
                frame,
                claw_mode,
                (16, ty + 112),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (160, 160, 160),
                1,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                "q / ESC: quit",
                (16, h - 16),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

            window_name = "Face + hands (MediaPipe)"
            cv2.imshow(window_name, frame)

            # Exit conditions:
            # - ESC / q / Q
            # - user closes the window
            # - Ctrl+C in terminal
            try:
                key = cv2.waitKey(1) & 0xFF
            except KeyboardInterrupt:
                break
            if key in (27, ord("q"), ord("Q")):
                break
            try:
                if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except Exception:
                # Some OpenCV builds can throw if the window disappears mid-frame.
                pass
    finally:
        try:
            from dave_it_guy.voice_tts import stop_hand_demo_speech

            stop_hand_demo_speech()
        except Exception:
            pass
        cap.release()
        cv2.destroyAllWindows()
        if face_lm is not None:
            face_lm.close()
        hand_lm.close()
        try:
            hand_cal.flush()
        except Exception:
            pass


if __name__ == "__main__":
    main()

