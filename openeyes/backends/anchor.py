"""Persistent OpenCV anchors for live Windows windows.

The store is a plain directory tree:

    <store>/<app>/<name>.png
    <store>/<app>/manifest.json

Nothing here imports pywin32, Pillow, or OpenCV at module import time. The
manifest helpers can therefore be used from tests and tooling on any platform;
the live find / capture / click path is Windows-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ERROR_ANCHOR_NOT_FOUND = "ANCHOR_NOT_FOUND"
ERROR_ANCHOR_AMBIGUOUS = "ANCHOR_AMBIGUOUS"
ERROR_ANCHOR_WEAK = "ANCHOR_WEAK"
ERROR_ANCHOR_TEMPLATE_MISSING = "ANCHOR_TEMPLATE_MISSING"
ERROR_STORE_NOT_FOUND = "ANCHOR_STORE_NOT_FOUND"
ERROR_INVALID_HWND = "INVALID_HWND"
ERROR_FOREGROUND_MISMATCH = "FOREGROUND_MISMATCH"
ERROR_WINDOW_MINIMIZED = "WINDOW_MINIMIZED"
ERROR_TARGET_OCCLUDED = "TARGET_OCCLUDED"
ERROR_CAPTURE_FAILED = "CAPTURE_FAILED"
ERROR_ANCHOR_VERIFY_FAILED = "ANCHOR_VERIFY_FAILED"
ERROR_FRAME_UNSTABLE = "FRAME_UNSTABLE"
ERROR_CLICK_FAILED = "CLICK_FAILED"

VERIFY_SCORE = 0.92
PROBE_HISTORY_WINDOW = 20


class AnchorStoreError(RuntimeError):
    """Base error for a malformed or missing anchor store."""


class AnchorVerificationError(AnchorStoreError):
    """A freshly captured crop did not match a second capture well enough."""


class FrameUnstableError(AnchorVerificationError):
    """Consecutive captures drifted too far to persist an anchor."""

    def __init__(self, message: str, stability: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.stability = dict(stability)


def default_store() -> Path:
    """Default store path, matching the Codex-home layout used by desktop agents."""
    override = os.environ.get("OPENEYES_ANCHOR_STORE")
    if override:
        return Path(override).expanduser()
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "anchors"
    return Path.home() / ".codex-home" / "anchors"


def _token(value: str, field: str) -> str:
    text = str(value).strip()
    if not text or text in {".", ".."} or any(sep in text for sep in ("/", "\\")):
        raise ValueError(f"{field} must be a single path component, got {value!r}")
    return text


def _anchor_name(name: str) -> str:
    text = _token(name, "anchor name")
    if text.lower().endswith(".png"):
        text = text[:-4]
    if not text:
        raise ValueError("anchor name cannot be empty")
    return text


def _app_dir(store: str | os.PathLike[str], app: str) -> Path:
    return Path(store).expanduser() / _token(app, "app")


def _manifest_path(store: str | os.PathLike[str], app: str) -> Path:
    return _app_dir(store, app) / "manifest.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AnchorStoreError(f"anchor manifest does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AnchorStoreError(f"anchor manifest is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AnchorStoreError(f"anchor manifest must be an object: {path}")
    anchors = data.get("anchors")
    if not isinstance(anchors, list):
        raise AnchorStoreError(f"anchor manifest has no anchors list: {path}")
    for index, entry in enumerate(anchors):
        if not isinstance(entry, dict):
            raise AnchorStoreError(f"anchor {index} is not an object in {path}")
        if not entry.get("name") or not entry.get("file"):
            raise AnchorStoreError(f"anchor {index} needs name and file in {path}")
    return data


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    text = json.dumps(dict(data), ensure_ascii=False, indent=2) + "\n"
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def load_store(store: str | os.PathLike[str], app: str) -> dict[str, Any]:
    """Load one app manifest, raising a clear error if it is absent/invalid."""
    path = _manifest_path(store, app)
    if not path.is_file():
        raise AnchorStoreError(f"anchor manifest does not exist: {path}")
    return _read_json(path)


def list_anchors(
    store: str | os.PathLike[str],
    app: str | None = None,
) -> list[dict[str, Any]]:
    """List anchors, optionally scoped to one app.

    Each returned entry keeps all manifest fields and gains an ``app`` key so
    a caller does not need a second lookup to know where it came from.
    """
    root = Path(store).expanduser()
    if app is not None:
        apps = [app]
    elif not root.is_dir():
        return []
    else:
        apps = sorted(path.name for path in root.iterdir()
                      if path.is_dir() and (path / "manifest.json").is_file())

    out: list[dict[str, Any]] = []
    for app_name in apps:
        manifest = load_store(root, app_name)
        for entry in manifest["anchors"]:
            item = dict(entry)
            item["app"] = app_name
            out.append(item)
    return out


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_dpi_awareness() -> str:
    try:
        from openeyes.core.dpi import current_dpi_awareness

        return current_dpi_awareness()
    except Exception:
        return "unknown"


def _pil_image(image: Any):
    from PIL import Image

    if isinstance(image, Image.Image):
        return image.convert("RGB")
    if isinstance(image, (str, os.PathLike)):
        with Image.open(image) as opened:
            return opened.convert("RGB")
    import numpy as np

    array = np.asarray(image)
    if array.size == 0:
        raise ValueError("anchor image is empty")
    if array.ndim == 2:
        return Image.fromarray(array.astype("uint8"), mode="L").convert("RGB")
    if array.ndim == 3 and array.shape[2] == 3:
        return Image.fromarray(array.astype("uint8"), mode="RGB")
    raise ValueError(f"unsupported anchor image shape: {array.shape}")


def _normalize_rect(rect: Any) -> list[int]:
    if isinstance(rect, Mapping):
        values = [rect.get("x"), rect.get("y"), rect.get("w"), rect.get("h")]
    elif isinstance(rect, str):
        values = [part.strip() for part in rect.split(",")]
    elif isinstance(rect, Sequence):
        values = list(rect)
    else:
        raise ValueError("rect must be [x, y, w, h] or an object with x/y/w/h")
    if len(values) != 4 or any(value is None for value in values):
        raise ValueError("rect must contain exactly x, y, w, h")
    x, y, width, height = (int(value) for value in values)
    if width <= 0 or height <= 0:
        raise ValueError("rect width and height must be positive")
    return [x, y, width, height]


def _same_scale_product(first: Any, second: Any) -> bool:
    if first is None or second is None:
        return first is None and second is None
    try:
        return round(float(first), 4) == round(float(second), 4)
    except (TypeError, ValueError):
        return False


def _scale_ratio(current: Any, stored: Any) -> float | None:
    """Current render metric divided by the manifest's capture metric."""
    try:
        current_value = float(current)
        stored_value = float(stored)
    except (TypeError, ValueError):
        return None
    if current_value <= 0 or stored_value <= 0:
        return None
    return current_value / stored_value


def _entry_scale_product(entry: Mapping[str, Any]) -> Any:
    """Scale group value for a manifest entry.

    Modern manifests record ``scale_product``; older ones only carry the
    equivalent ``dpi_scale``. The write path always emits ``scale_product``,
    so an entry whose key is present (even null) keeps the modern meaning.
    """
    if "scale_product" in entry:
        return entry["scale_product"]
    return entry.get("dpi_scale")

def _best_eligible_hit(hits: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Best hit that can compete at the weak floor (hits arrive score-sorted)."""
    from openeyes.core.anchors import classify

    for hit in hits:
        if classify(float(hit.get("score", 0.0))) != "miss":
            return hit
    return None


def _competition_peaks(hits: Sequence[Mapping[str, Any]]) -> int:
    """Count the hits ``decide`` actually ranks, not every sub-floor candidate."""
    from openeyes.core.anchors import classify

    return sum(1 for hit in hits
               if classify(float(hit.get("score", 0.0))) != "miss")


def _system_color_theme() -> str | None:

    """Read the Windows app-theme preference; absence is reported as unknown."""
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if int(value) else "dark"
    except Exception:
        return None


def collect_render_context(
    hwnd: int,
    *,
    scale_product: float | None = None,
    device_scale_factor: float | None = None,
    zoom_level: float | None = None,
    color_theme: str | None = None,
    icon_theme: str | None = None,
    layout_signature: str | None = None,
) -> dict[str, Any]:
    """Collect what the OS can prove, leaving unknown render facts null.

    System DPI and the Windows app theme are observable directly. Chromium's
    ``deviceScaleFactor`` and page zoom are not exposed through Win32, so a
    caller that knows them supplies them explicitly. The product is computed
    only when all three factors are known.
    """
    win32gui, _ = _require_windows()
    system_scale = _dpi_scale(hwnd)
    device = None if device_scale_factor is None else float(device_scale_factor)
    zoom = None if zoom_level is None else float(zoom_level)
    product = None if scale_product is None else round(float(scale_product), 4)
    if product is None and device is not None and zoom is not None:
        product = round(system_scale * device * zoom, 4)
    try:
        state = "maximized" if win32gui.IsZoomed(hwnd) else "normal"
    except Exception:
        state = None
    return {
        "scale_product": product,
        "system_scale": round(system_scale, 4),
        "device_scale_factor": (round(device, 4) if device is not None else None),
        "zoom_level": round(zoom, 4) if zoom is not None else None,
        "color_theme": color_theme or _system_color_theme(),
        "icon_theme": icon_theme,
        "layout_signature": layout_signature,
        "window_state": state,
    }


def save_anchor(
    store: str | os.PathLike[str],
    app: str,
    name: str,
    image: Any,
    rect_window: Any,
    *,
    score: float | None = None,
    strength: str | None = None,
    theme: str | None = None,
    dpi_scale: float = 1.0,
    scale_product: float | None = None,
    system_scale: float | None = None,
    device_scale_factor: float | None = None,
    zoom_level: float | None = None,
    color_theme: str | None = None,
    icon_theme: str | None = None,
    layout_signature: str | None = None,
    window_class: str | None = None,
    exe_name: str | None = None,
    captured_at: str | None = None,
    dpi_awareness: str | None = None,
) -> dict[str, Any]:
    """Save or replace one anchor image and its manifest entry.

    The image is written first and the manifest is replaced atomically. The
    SHA-256 describes the exact PNG bytes on disk.
    """
    from openeyes.core.anchors import classify

    app_name = _token(app, "app")
    anchor_name = _anchor_name(name)
    rect = _normalize_rect(rect_window)
    picture = _pil_image(image)

    app_path = _app_dir(store, app_name)
    manifest_path = app_path / "manifest.json"
    existing: dict[str, Any] | None = None
    if manifest_path.is_file():
        existing = load_store(store, app_name)
    numeric_score = float(score if score is not None else 1.0)
    tier = strength or classify(numeric_score)
    if tier not in {"strong", "weak", "miss"}:
        raise ValueError("strength must be strong, weak or miss")

    app_path.mkdir(parents=True, exist_ok=True)
    file_stem = (anchor_name if scale_product is None
                 else f"{anchor_name}@{round(float(scale_product), 4):.4f}")
    image_path = app_path / f"{file_stem}.png"
    temporary = image_path.with_name(image_path.name + ".tmp")
    picture.save(temporary, format="PNG")
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    os.replace(temporary, image_path)

    entry = {
        "name": anchor_name,
        "file": image_path.name,
        "rect_window": rect,
        "score": numeric_score,
        "strength": tier,
        "theme": theme,
        "dpi_scale": float(dpi_scale),
        "scale_product": (round(float(scale_product), 4)
                          if scale_product is not None else None),
        "system_scale": (round(float(system_scale), 4)
                         if system_scale is not None else None),
        "device_scale_factor": (round(float(device_scale_factor), 4)
                                if device_scale_factor is not None else None),
        "zoom_level": (round(float(zoom_level), 4)
                       if zoom_level is not None else None),
        "color_theme": color_theme if color_theme is not None else theme,
        "icon_theme": icon_theme,
        "layout_signature": layout_signature,
        "sha256": digest,
    }
    if existing is None:
        manifest: dict[str, Any] = {
            "app": app_name,
            "window_class": window_class or "",
            "exe_name": exe_name or "",
            "captured_at": captured_at or _utc_now(),
            "dpi_awareness": dpi_awareness or _default_dpi_awareness(),
            "anchors": [],
        }
    else:
        manifest = existing
        manifest["app"] = app_name
        manifest["captured_at"] = captured_at or _utc_now()
        if window_class is not None:
            manifest["window_class"] = window_class
        if exe_name is not None:
            manifest["exe_name"] = exe_name
        if dpi_awareness is not None:
            manifest["dpi_awareness"] = dpi_awareness

    group_value = entry["scale_product"]
    anchors = [
        item for item in manifest["anchors"]
        if not (item.get("name") == anchor_name
                and _same_scale_product(_entry_scale_product(item), group_value))
    ]
    anchors.append(entry)
    manifest["anchors"] = anchors
    _write_json(manifest_path, manifest)
    return dict(entry, app=app_name, path=str(image_path))


def _require_windows():
    if not sys.platform.startswith("win"):
        from openeyes.core.anchors import AnchorUnavailable

        raise AnchorUnavailable("live anchor matching is only implemented on Windows")
    try:
        import win32con
        import win32gui
    except ImportError as exc:  # pragma: no cover - Windows environment dependent
        from openeyes.core.anchors import AnchorUnavailable

        raise AnchorUnavailable(
            'pywin32 is required for live anchors; install it with pip install "openeyes[windows]"'
        ) from exc
    return win32gui, win32con


def _capture_window(hwnd: int):
    from openeyes.core.capture import capture_window

    return capture_window(hwnd)


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    win32gui, _ = _require_windows()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return int(left), int(top), int(right), int(bottom)


def _process_exe(hwnd: int) -> str:
    """Best-effort executable basename for the window's process."""
    import ctypes
    from ctypes import wintypes

    try:
        import win32process

        _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        process = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not process:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(
                process, 0, buffer, ctypes.byref(size)
            ):
                return Path(buffer.value).name
            return ""
        finally:
            kernel32.CloseHandle(process)
    except Exception:
        return ""


def _dpi_scale(hwnd: int) -> float:
    try:
        import ctypes

        scale = float(ctypes.windll.user32.GetDpiForWindow(hwnd)) / 96.0
        return scale if scale > 0 else 1.0
    except Exception:
        return 1.0


def _resolve_app(
    store: str | os.PathLike[str],
    name: str,
    app: str | None,
    exe_name: str,
) -> str:
    if app:
        return _token(app, "app")
    root = Path(store).expanduser()
    if not root.is_dir():
        raise AnchorStoreError(f"anchor store does not exist: {root}")

    matches: list[str] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not (path / "manifest.json").is_file():
            continue
        manifest = load_store(root, path.name)
        if any(entry.get("name") == name for entry in manifest["anchors"]):
            matches.append(path.name)
    if len(matches) == 1:
        return matches[0]
    if exe_name:
        stem = Path(exe_name).stem.lower()
        exact = [candidate for candidate in matches if candidate.lower() == stem]
        by_manifest = [
            candidate for candidate in matches
            if str(load_store(root, candidate).get("exe_name", "")).lower()
            == exe_name.lower()
        ]
        preferred = exact or by_manifest
        if len(preferred) == 1:
            return preferred[0]
    if not matches:
        return exe_name and Path(exe_name).stem or ""
    raise AnchorStoreError(
        f"anchor {name!r} exists in several apps ({', '.join(matches)}); pass app explicitly"
    )


def frame_stability(
    hwnd: int,
    n: int = 3,
    interval: float = 0.15,
    *,
    template: Any = None,
    rect_window: Any = None,
    scales: Sequence[float] | None = None,
    capture: Any = None,
    initial: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Capture ``n`` frames and prove they agree with one anchor template.

    When ``template`` is omitted, the first frame (or ``rect_window`` inside
    it) becomes the reference. GPU composition can replay an old frame or
    return a black block; this check compares the raw pixels of consecutive
    frames and matches every frame against the same template.
    """
    from openeyes.core.anchors import assess_frames

    count = int(n)
    if count < 2:
        raise ValueError("frame stability needs at least two frames")
    if interval < 0:
        raise ValueError("interval must be non-negative")
    grab = capture or _capture_window
    frames = list(initial or [])[:count]
    reference = template
    if reference is None and frames:
        if rect_window is None:
            reference = frames[0]
        else:
            x, y, width, height = _normalize_rect(rect_window)
            reference = frames[0].crop((x, y, x + width, y + height))
    while len(frames) < count:
        if frames and interval:
            time.sleep(float(interval))
        frame = grab(hwnd)
        if reference is None:
            if rect_window is None:
                reference = frame
            else:
                x, y, width, height = _normalize_rect(rect_window)
                reference = frame.crop((x, y, x + width, y + height))
        frames.append(frame)
    result = assess_frames(frames, reference, scales=scales)
    result["hwnd"] = int(hwnd)
    return result


def anchor_frame_stability(
    hwnd: int,
    bbox: Any,
    n: int = 3,
    interval: float = 0.15,
    *,
    initial: Any = None,
    capture: Any = None,
) -> dict[str, Any]:
    """Check that the matched anchor crop is stable across fresh frames.

    Only the crop is compared. A template from another app is an anchor miss,
    not a sign that the whole window is unstable; the crop-level check also
    avoids treating unrelated animations elsewhere in the window as failure.
    """
    from openeyes.core.anchors import assess_frames

    count = int(n)
    if count < 2:
        raise ValueError("anchor frame stability needs at least two frames")
    if interval < 0:
        raise ValueError("interval must be non-negative")
    x, y, width, height = _normalize_rect(bbox)
    grab = capture or _capture_window
    frames = [] if initial is None else [_pil_image(initial)]
    while len(frames) < count:
        if frames and interval:
            time.sleep(float(interval))
        frames.append(_pil_image(grab(hwnd)))

    crops = []
    for frame in frames:
        if x + width > frame.width or y + height > frame.height:
            return {
                "stable": False,
                "pixel_stable": False,
                "template_stable": True,
                "max_abs_diff": None,
                "min_score": None,
                "frames": len(frames),
                "frame_scores": [],
                "reasons": ["size-changed"],
                "bbox": [x, y, width, height],
                "hwnd": int(hwnd),
            }
        crops.append(frame.crop((x, y, x + width, y + height)))

    result = assess_frames(crops, crops[0])
    result["bbox"] = [x, y, width, height]
    result["hwnd"] = int(hwnd)
    return result


def _probe_key(name: str, scale_product: Any) -> str:
    if scale_product is None:
        return name
    return f"{name}|{round(float(scale_product), 4):.4f}"


def _probe_history_path(store: str | os.PathLike[str], app: str) -> Path:
    return _app_dir(store, app) / "probe_stats.json"


def _record_probe(
    store: str | os.PathLike[str],
    app: str,
    name: str,
    scale_product: Any,
    peaks: int,
) -> dict[str, Any]:
    """Append one peak count to a bounded per-anchor probe window.

    A failed write never turns a successful visual match into an error;
    the receipt then reports this probe as its one-sample history.
    """
    key = _probe_key(name, scale_product)
    path = _probe_history_path(store, app)
    history: dict[str, Any] = {}
    try:
        if path.is_file():
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                history = parsed
        probes = history.setdefault(key, [])
        if not isinstance(probes, list):
            probes = []
            history[key] = probes
        probes.append(int(peaks))
        del probes[:-PROBE_HISTORY_WINDOW]
        _write_json(path, history)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        probes = [int(peaks)]
    ambiguous = sum(1 for value in probes if int(value) > 1)
    return {
        "peaks": int(peaks),
        "ambiguous_rate": (ambiguous / len(probes) if probes else 0.0),
        "probe_count": len(probes),
    }


def _find_entries(manifest: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    return [dict(entry) for entry in manifest.get("anchors", [])
            if entry.get("name") == name]


def _find_entry(manifest: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    for entry in manifest.get("anchors", []):
        if entry.get("name") == name:
            return dict(entry)
    return None

def _candidate_phases(
    entries: Sequence[dict[str, Any]],
    scale_product: float | None,
    color_theme: str | None,
) -> list[tuple[str, list[dict[str, Any]]]]:
    group_indexes = [
        index for index, entry in enumerate(entries)
        if scale_product is not None
        and _same_scale_product(_entry_scale_product(entry), scale_product)
    ]
    if color_theme and group_indexes:
        theme = str(color_theme).lower()
        group_indexes.sort(key=lambda index: str(
            entries[index].get("color_theme") or entries[index].get("theme") or ""
        ).lower() != theme)
    group = [entries[index] for index in group_indexes]
    others = [entry for index, entry in enumerate(entries) if index not in group_indexes]

    if group:
        phases = [("group", group)]
        if others:
            phases.append(("fallback", others))
    elif scale_product is not None:
        phases = [("fallback", list(entries))]
    else:
        phases = [("library", list(entries))]
    return phases


def find_anchor(
    hwnd: int,
    store: str | os.PathLike[str],
    name: str,
    app: str | None = None,
    threshold: float = 0.80,
    min_score: float | None = None,
    *,
    weak_support: bool = False,
    require_unique: bool = True,
    check_frame: bool = False,
    scale_product: float | None = None,
    color_theme: str | None = None,
    record_probe: bool = False,
) -> dict[str, Any]:
    """Find one anchor in a live window and return a structured receipt.

    Same-name entries are grouped by ``scale_product`` when the caller knows
    the current product. A group miss falls back to the full library and is
    recorded in ``used_group`` / ``fell_back``.
    """
    from openeyes.core.anchors import decide as choose
    from openeyes.core.anchors import match, scale_scan

    win32gui, _ = _require_windows()
    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    hwnd = int(hwnd or 0)
    lower_bound = float(threshold)
    if min_score is not None:
        lower_bound = max(lower_bound, float(min_score))
    search_floor = min(lower_bound, 0.80)
    report: dict[str, Any] = {
        "ok": False,
        "found": False,
        "hwnd": hwnd,
        "store": str(Path(store).expanduser()),
        "app": app,
        "anchor": name,
        "threshold": lower_bound,
        "search_floor": search_floor,
        "bbox": None,
        "center": None,
        "screen_point": None,
        "score": None,
        "strength": None,
        "scale": None,
        "reason": None,
        "error_code": None,
        "error": None,
        "hits": [],
        "decision": None,
        "scale_product": None if scale_product is None else round(float(scale_product), 4),
        "used_group": False,
        "fell_back": False,
        "peaks": 0,
        "ambiguous_rate": 0.0,
        "probe_count": 0,
        "scan_strategy": scale_scan(None)[1],
        "frame_stability": None,
        "candidates": [],
    }
    if hwnd <= 0 or not win32gui.IsWindow(hwnd):
        report["error_code"] = ERROR_INVALID_HWND
        report["error"] = f"hwnd={hwnd} is not a valid window"
        report["reason"] = "invalid-window"
        return report

    exe_name = _process_exe(hwnd)
    try:
        app_name = _resolve_app(store, name, app, exe_name)
    except AnchorStoreError as exc:
        report["error_code"] = ERROR_STORE_NOT_FOUND
        report["error"] = str(exc)
        report["reason"] = "store-error"
        return report
    report["app"] = app_name
    if not app_name:
        report["error_code"] = ERROR_STORE_NOT_FOUND
        report["error"] = f"could not resolve an app profile for anchor {name!r}"
        report["reason"] = "app-unresolved"
        return report

    try:
        manifest = load_store(store, app_name)
    except AnchorStoreError as exc:
        report["error_code"] = ERROR_STORE_NOT_FOUND
        report["error"] = str(exc)
        report["reason"] = "store-error"
        return report
    entries = _find_entries(manifest, name)
    if not entries:
        report["error_code"] = ERROR_ANCHOR_NOT_FOUND
        report["error"] = f"anchor {name!r} is not in {app_name}/manifest.json"
        report["reason"] = "anchor-not-in-manifest"
        return report

    phases = _candidate_phases(entries, scale_product, color_theme)
    report["candidates"] = [
        {"file": entry.get("file"), "scale_product": _entry_scale_product(entry),
         "phase": phase}
        for phase, phase_entries in phases for entry in phase_entries
    ]

    app_path = _app_dir(store, app_name).resolve()
    group_had_hits = False
    last_failure: dict[str, Any] | None = None
    preferred_failure: dict[str, Any] | None = None

    def finish_probe(receipt: dict[str, Any], peaks: int) -> dict[str, Any]:
        stats = ({"peaks": int(peaks),
                  "ambiguous_rate": 1.0 if int(peaks) > 1 else 0.0,
                  "probe_count": 1}
                 if not record_probe else
                 _record_probe(store, app_name, name, scale_product, peaks))
        receipt.update(stats)
        return receipt

    for phase, phase_entries in phases:
        if phase == "fallback":
            if group_had_hits:
                break
            report["fell_back"] = True
        for entry in phase_entries:
            template_path = (app_path / str(entry["file"])).resolve()
            if app_path not in template_path.parents:
                failure = dict(report, error_code=ERROR_ANCHOR_TEMPLATE_MISSING,
                               error=f"anchor file escapes its app directory: {entry['file']}",
                               reason="invalid-template-path", hits=[], decision=None)
            elif not template_path.is_file():
                failure = dict(report, error_code=ERROR_ANCHOR_TEMPLATE_MISSING,
                               error=f"anchor image does not exist: {template_path}",
                               reason="template-missing", hits=[], decision=None)
            else:
                expected = _scale_ratio(scale_product, _entry_scale_product(entry))
                scales, strategy = scale_scan(expected)
                report["scan_strategy"] = strategy
                try:
                    image = _capture_window(hwnd)
                    hits = match(image, template_path, threshold=search_floor,
                                 scales=scales)
                except Exception as exc:
                    failure = dict(report, error_code=ERROR_CAPTURE_FAILED,
                                   error=f"could not match anchor {name!r}: {exc}",
                                   reason="capture-failed", hits=[], decision=None)
                else:
                    decision = choose(hits, require_unique=require_unique,
                                      weak_support=weak_support)
                    peaks = _competition_peaks(hits)
                    update: dict[str, Any] = {
                        "hits": hits,
                        "peaks": peaks,
                        "decision": {
                            "strength": decision["strength"],
                            "ambiguous": decision["ambiguous"],
                            "reason": decision["reason"],
                            "chosen": decision["chosen"],
                        },
                        "scale_product": report["scale_product"],
                        "used_group": phase == "group",
                        "fell_back": bool(report["fell_back"]),
                        "scan_strategy": strategy,
                    }
                    if hits:
                        update["score"] = float(hits[0]["score"])
                    chosen = decision["chosen"]
                    winner = _best_eligible_hit(hits)
                    below_acceptance = (winner is not None
                                        and float(winner["score"]) < lower_bound)
                    if chosen is None or below_acceptance:
                        if decision["ambiguous"]:
                            code = ERROR_ANCHOR_AMBIGUOUS
                            reason = decision["reason"]
                        elif chosen is None:
                            code = (ERROR_ANCHOR_WEAK
                                    if decision["strength"] == "weak"
                                    else ERROR_ANCHOR_NOT_FOUND)
                            reason = decision["reason"]
                        else:
                            code = ERROR_ANCHOR_NOT_FOUND
                            reason = (f"best hit {float(winner['score']):.3f} is below "
                                      f"the acceptance threshold {lower_bound:.2f}")
                            update["decision"]["chosen"] = None
                            update["decision"]["candidate"] = winner
                            update["below_acceptance"] = True
                        failure = dict(report, **update, strength=decision["strength"],
                                       reason=reason, error_code=code,
                                       error=reason)
                        if winner is not None:
                            preferred_failure = failure
                            if (phase == "group"
                                    and float(winner["score"]) >= lower_bound):
                                group_had_hits = True
                        else:
                            last_failure = failure
                        continue

                    left, top, right, bottom = _window_rect(hwnd)
                    scale_x = (right - left) / image.width if image.width else 1.0
                    scale_y = (bottom - top) / image.height if image.height else 1.0
                    bbox = {
                        "x": int(chosen["x"]),
                        "y": int(chosen["y"]),
                        "w": int(chosen["w"]),
                        "h": int(chosen["h"]),
                    }
                    center = {
                        "x": bbox["x"] + bbox["w"] // 2,
                        "y": bbox["y"] + bbox["h"] // 2,
                    }
                    if check_frame:
                        try:
                            stability = anchor_frame_stability(
                                hwnd, [bbox["x"], bbox["y"], bbox["w"], bbox["h"]],
                                initial=image, capture=_capture_window)
                        except Exception as exc:
                            return finish_probe(dict(
                                report, **update, reason="capture-failed",
                                error=f"could not recapture anchor {name!r}: {exc}",
                                error_code=ERROR_CAPTURE_FAILED,
                            ), peaks)
                        report["frame_stability"] = stability
                        if not stability["stable"]:
                            return finish_probe(dict(
                                report, **update,
                                strength=decision["strength"],
                                reason="frame-unstable",
                                error="the matched anchor crop changed across frames",
                                error_code=ERROR_FRAME_UNSTABLE,
                                frame_stability=stability,
                            ), peaks)
                    update.update({
                        "ok": True,
                        "found": True,
                        "bbox": bbox,
                        "center": center,
                        "screen_point": {
                            "x": int(round(left + center["x"] * scale_x)),
                            "y": int(round(top + center["y"] * scale_y)),
                        },
                        "score": float(chosen["score"]),
                        "strength": decision["strength"],
                        "scale": float(chosen["scale"]),
                        "reason": decision["reason"],
                    })
                    return finish_probe(dict(report, **update), peaks)
            last_failure = failure

    failure = preferred_failure or last_failure or dict(report)
    peaks = int(failure.get("peaks", 0))
    return finish_probe(failure, peaks)


def find_anchor_in_image(
    image: Any,
    store: str | os.PathLike[str],
    name: str,
    app: str | None = None,
    threshold: float = 0.80,
    min_score: float | None = None,
    *,
    weak_support: bool = False,
    require_unique: bool = True,
    scale_product: float | None = None,
    color_theme: str | None = None,
    record_probe: bool = False,
) -> dict[str, Any]:
    """Match an anchor against a saved screenshot without touching a window.

    This is the offline twin of :func:`find_anchor`. It uses the same group
    fallback, scale prior, NMS and decision rules, but reports image-local
    coordinates and never performs a capture or an input action.
    """
    from openeyes.core.anchors import decide as choose
    from openeyes.core.anchors import match, scale_scan

    lower_bound = float(threshold)
    if min_score is not None:
        lower_bound = max(lower_bound, float(min_score))
    search_floor = min(lower_bound, 0.80)
    report: dict[str, Any] = {
        "ok": False,
        "found": False,
        "source": "image",
        "hwnd": None,
        "store": str(Path(store).expanduser()),
        "app": app,
        "anchor": name,
        "threshold": lower_bound,
        "search_floor": search_floor,
        "bbox": None,
        "center": None,
        "screen_point": None,
        "score": None,
        "strength": None,
        "scale": None,
        "reason": None,
        "error_code": None,
        "error": None,
        "hits": [],
        "decision": None,
        "scale_product": (round(float(scale_product), 4)
                           if scale_product is not None else None),
        "used_group": False,
        "fell_back": False,
        "peaks": 0,
        "ambiguous_rate": 0.0,
        "probe_count": 0,
        "scan_strategy": scale_scan(None)[1],
        "frame_stability": "not-applicable-offline",
        # An offline match can never justify a click on its own.
        "actionable": False,
        "candidates": [],
    }
    try:
        app_name = _resolve_app(store, name, app, "")
    except AnchorStoreError as exc:
        report["error_code"] = ERROR_STORE_NOT_FOUND
        report["error"] = str(exc)
        report["reason"] = "store-error"
        return report
    report["app"] = app_name
    if not app_name:
        report["error_code"] = ERROR_STORE_NOT_FOUND
        report["error"] = f"could not resolve an app profile for anchor {name!r}"
        report["reason"] = "app-unresolved"
        return report

    try:
        manifest = load_store(store, app_name)
    except AnchorStoreError as exc:
        report["error_code"] = ERROR_STORE_NOT_FOUND
        report["error"] = str(exc)
        report["reason"] = "store-error"
        return report
    entries = _find_entries(manifest, name)
    if not entries:
        report["error_code"] = ERROR_ANCHOR_NOT_FOUND
        report["error"] = f"anchor {name!r} is not in {app_name}/manifest.json"
        report["reason"] = "anchor-not-in-manifest"
        return report

    phases = _candidate_phases(entries, scale_product, color_theme)
    report["candidates"] = [
        {"file": entry.get("file"), "scale_product": _entry_scale_product(entry),
         "phase": phase}
        for phase, phase_entries in phases for entry in phase_entries
    ]

    try:
        source = _pil_image(image)
    except Exception as exc:
        report["error_code"] = ERROR_CAPTURE_FAILED
        report["error"] = f"could not read anchor image: {exc}"
        report["reason"] = "image-invalid"
        return report
    report["image_size"] = [source.width, source.height]
    app_path = _app_dir(store, app_name).resolve()
    group_had_hits = False
    last_failure: dict[str, Any] | None = None
    preferred_failure: dict[str, Any] | None = None

    def finish_probe(receipt: dict[str, Any], peaks: int) -> dict[str, Any]:
        if record_probe:
            stats = _record_probe(store, app_name, name, scale_product, peaks)
        else:
            stats = {
                "peaks": int(peaks),
                "ambiguous_rate": 1.0 if int(peaks) > 1 else 0.0,
                "probe_count": 1,
            }
        receipt.update(stats)
        return receipt

    for phase, phase_entries in phases:
        if phase == "fallback":
            if group_had_hits:
                break
            report["fell_back"] = True
        for entry in phase_entries:
            template_path = (app_path / str(entry["file"])).resolve()
            if app_path not in template_path.parents:
                failure = dict(report, error_code=ERROR_ANCHOR_TEMPLATE_MISSING,
                               error=f"anchor file escapes its app directory: {entry['file']}",
                               reason="invalid-template-path", hits=[], decision=None)
            elif not template_path.is_file():
                failure = dict(report, error_code=ERROR_ANCHOR_TEMPLATE_MISSING,
                               error=f"anchor image does not exist: {template_path}",
                               reason="template-missing", hits=[], decision=None)
            else:
                expected = _scale_ratio(scale_product, _entry_scale_product(entry))
                scales, strategy = scale_scan(expected)
                report["scan_strategy"] = strategy
                try:
                    hits = match(source, template_path, threshold=search_floor,
                                 scales=scales)
                except Exception as exc:
                    failure = dict(report, error_code=ERROR_CAPTURE_FAILED,
                                   error=f"could not match anchor {name!r}: {exc}",
                                   reason="capture-failed", hits=[], decision=None)
                else:
                    decision = choose(hits, require_unique=require_unique,
                                      weak_support=weak_support)
                    peaks = _competition_peaks(hits)
                    update: dict[str, Any] = {
                        "hits": hits,
                        "peaks": peaks,
                        "decision": {
                            "strength": decision["strength"],
                            "ambiguous": decision["ambiguous"],
                            "reason": decision["reason"],
                            "chosen": decision["chosen"],
                        },
                        "scale_product": report["scale_product"],
                        "used_group": phase == "group",
                        "fell_back": bool(report["fell_back"]),
                        "scan_strategy": strategy,
                    }
                    if hits:
                        update["score"] = float(hits[0]["score"])
                    chosen = decision["chosen"]
                    winner = _best_eligible_hit(hits)
                    below_acceptance = (winner is not None
                                        and float(winner["score"]) < lower_bound)
                    if chosen is None or below_acceptance:
                        if decision["ambiguous"]:
                            code = ERROR_ANCHOR_AMBIGUOUS
                            reason = decision["reason"]
                        elif chosen is None:
                            code = (ERROR_ANCHOR_WEAK
                                    if decision["strength"] == "weak"
                                    else ERROR_ANCHOR_NOT_FOUND)
                            reason = decision["reason"]
                        else:
                            code = ERROR_ANCHOR_NOT_FOUND
                            reason = (f"best hit {float(winner['score']):.3f} is below "
                                      f"the acceptance threshold {lower_bound:.2f}")
                            update["decision"]["chosen"] = None
                            update["decision"]["candidate"] = winner
                            update["below_acceptance"] = True
                        failure = dict(report, **update, strength=decision["strength"],
                                       reason=reason, error_code=code,
                                       error=reason)
                        if winner is not None:
                            preferred_failure = failure
                            if (phase == "group"
                                    and float(winner["score"]) >= lower_bound):
                                group_had_hits = True
                        else:
                            last_failure = failure
                        continue

                    bbox = {
                        "x": int(chosen["x"]),
                        "y": int(chosen["y"]),
                        "w": int(chosen["w"]),
                        "h": int(chosen["h"]),
                    }
                    center = {
                        "x": bbox["x"] + bbox["w"] // 2,
                        "y": bbox["y"] + bbox["h"] // 2,
                    }
                    update.update({
                        "ok": True,
                        "found": True,
                        "bbox": bbox,
                        "center": center,
                        "score": float(chosen["score"]),
                        "strength": decision["strength"],
                        "scale": float(chosen["scale"]),
                        "reason": decision["reason"],
                    })
                    return finish_probe(dict(report, **update), peaks)
            last_failure = failure

    failure = preferred_failure or last_failure or dict(report)
    peaks = int(failure.get("peaks", 0))
    return finish_probe(failure, peaks)


def _safety_checks(
    hwnd: int,
    point: Mapping[str, int] | None,
    *,
    foreground: int | None = None,
    is_window: bool | None = None,
    is_minimized: bool | None = None,
    window_from_point: Any = None,
) -> dict[str, bool]:
    win32gui, win32con = _require_windows()
    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    valid = bool(win32gui.IsWindow(hwnd)) if is_window is None else bool(is_window)
    fg = int(win32gui.GetForegroundWindow()) if foreground is None else int(foreground)
    minimized = bool(win32gui.IsIconic(hwnd)) if is_minimized is None else bool(is_minimized)
    point_on_target = False
    if point is not None and valid:
        x, y = int(point["x"]), int(point["y"])
        if window_from_point is None:
            point_hwnd = int(win32gui.WindowFromPoint((x, y)) or 0)
        else:
            point_hwnd = int(window_from_point(x, y) or 0)
        if point_hwnd:
            try:
                point_on_target = int(
                    win32gui.GetAncestor(point_hwnd, win32con.GA_ROOT)
                ) == hwnd
            except Exception:
                point_on_target = False
    return {
        "foreground": fg == hwnd,
        "is_window": valid,
        "not_minimized": valid and not minimized,
        "point_on_target": point_on_target,
    }


def _first_gate_failure(checks: Mapping[str, bool]) -> tuple[str, str]:
    if not checks["foreground"]:
        return ERROR_FOREGROUND_MISMATCH, "target window is not the foreground window"
    if not checks["is_window"]:
        return ERROR_INVALID_HWND, "target window handle is no longer valid"
    if not checks["not_minimized"]:
        return ERROR_WINDOW_MINIMIZED, "target window is minimized"
    if not checks["point_on_target"]:
        return ERROR_TARGET_OCCLUDED, "the anchor point belongs to another window"
    return "", ""


def click_anchor(
    hwnd: int,
    store: str | os.PathLike[str],
    name: str,
    app: str | None = None,
    threshold: float = 0.80,
    min_score: float | None = None,
    *,
    dry_run: bool = True,
    weak_support: bool = False,
    check_frame: bool = False,
    scale_product: float | None = None,
    color_theme: str | None = None,
    button: str = "left",
    double: bool = False,
    clicker: Any = None,
    foreground: int | None = None,
    is_window: bool | None = None,
    is_minimized: bool | None = None,
    window_from_point: Any = None,
) -> dict[str, Any]:
    """Click a matched anchor, but only after all four safety gates pass."""
    find = find_anchor(
        hwnd,
        store,
        name,
        app=app,
        threshold=threshold,
        min_score=min_score,
        weak_support=weak_support,
        require_unique=True,
        check_frame=check_frame,
        scale_product=scale_product,
        color_theme=color_theme,
    )
    point = find.get("screen_point")
    checks = _safety_checks(
        int(hwnd or 0),
        point,
        foreground=foreground,
        is_window=is_window,
        is_minimized=is_minimized,
        window_from_point=window_from_point,
    )
    receipt: dict[str, Any] = {
        "ok": False,
        "clicked": False,
        "dry_run": bool(dry_run),
        "would_click": False,
        "hwnd": int(hwnd or 0),
        "app": find.get("app"),
        "anchor": name,
        "checks": checks,
        "target": {
            "bbox": find.get("bbox"),
            "center": find.get("center"),
            "screen_point": point,
        },
        "score": find.get("score"),
        "strength": find.get("strength"),
        "used_group": find.get("used_group"),
        "fell_back": find.get("fell_back"),
        "peaks": find.get("peaks"),
        "ambiguous_rate": find.get("ambiguous_rate"),
        "scan_strategy": find.get("scan_strategy"),
        "frame_stability": find.get("frame_stability"),
        "reason": None,
        "error_code": None,
        "error": None,
        "effect_verified": False,
    }
    if not find.get("found"):
        receipt["reason"] = find.get("reason")
        receipt["error_code"] = find.get("error_code")
        receipt["error"] = find.get("error")
        return receipt

    error_code, message = _first_gate_failure(checks)
    if error_code:
        receipt["reason"] = "safety-gate"
        receipt["error_code"] = error_code
        receipt["error"] = message
        return receipt

    receipt["reason"] = "dry-run" if dry_run else "clicked"
    receipt["would_click"] = True
    if dry_run:
        receipt["ok"] = True
        return receipt

    if clicker is None:
        from openeyes.core.actuator import click_xy

        clicker = click_xy
    try:
        clicker(int(point["x"]), int(point["y"]), button=button, double=double)
    except Exception as exc:
        receipt["reason"] = "click-failed"
        receipt["error_code"] = ERROR_CLICK_FAILED
        receipt["error"] = f"click actuator raised: {exc}"
        return receipt
    receipt.update({
        "ok": True,
        "clicked": True,
        "reason": "clicked",
    })
    return receipt


def capture_anchor(
    hwnd: int,
    name: str,
    rect: Any,
    store: str | os.PathLike[str],
    app: str,
    theme: str | None = None,
    scale_product: float | None = None,
    device_scale_factor: float | None = None,
    zoom_level: float | None = None,
    color_theme: str | None = None,
    icon_theme: str | None = None,
    layout_signature: str | None = None,
) -> dict[str, Any]:
    """Capture a crop, prove it with a second capture, then persist it."""
    from openeyes.core.anchors import classify
    from openeyes.core.dpi import ensure_dpi_awareness

    win32gui, _ = _require_windows()
    hwnd = int(hwnd or 0)
    if hwnd <= 0 or not win32gui.IsWindow(hwnd):
        raise ValueError(f"hwnd={hwnd} is not a valid window")
    ensure_dpi_awareness()
    x, y, width, height = _normalize_rect(rect)
    if win32gui.IsIconic(hwnd):
        raise ValueError("window is minimized; restore it before capturing an anchor")

    first = _capture_window(hwnd)
    if x + width > first.width or y + height > first.height:
        raise ValueError(
            f"rect {[x, y, width, height]} lies outside the {first.width}x{first.height} window"
        )
    template = first.crop((x, y, x + width, y + height))
    stability = anchor_frame_stability(
        hwnd, [x, y, width, height], capture=_capture_window, initial=first)
    if not stability["stable"] or float(stability["min_score"]) < VERIFY_SCORE:
        raise FrameUnstableError(
            f"captured crop did not verify: frame unstable at {VERIFY_SCORE:.2f} "
            f"({', '.join(stability['reasons']) or 'score-too-low'})",
            stability,
        )
    best_score = float(stability["min_score"])
    class_name = win32gui.GetClassName(hwnd) or ""
    exe_name = _process_exe(hwnd)
    scale = _dpi_scale(hwnd)
    render = collect_render_context(
        hwnd, scale_product=scale_product, device_scale_factor=device_scale_factor,
        zoom_level=zoom_level, color_theme=color_theme or theme,
        icon_theme=icon_theme, layout_signature=layout_signature,
    )
    entry = save_anchor(
        store,
        app,
        name,
        template,
        [x, y, width, height],
        score=best_score,
        strength=classify(best_score),
        theme=theme,
        dpi_scale=scale,
        scale_product=render["scale_product"],
        system_scale=render["system_scale"],
        device_scale_factor=render["device_scale_factor"],
        zoom_level=render["zoom_level"],
        color_theme=render["color_theme"],
        icon_theme=render["icon_theme"],
        layout_signature=render["layout_signature"],
        window_class=class_name,
        exe_name=exe_name,
        dpi_awareness=ensure_dpi_awareness(),
    )
    return {
        "ok": True,
        "captured": True,
        "hwnd": hwnd,
        "app": app,
        "anchor": entry["name"],
        "path": entry["path"],
        "rect_window": entry["rect_window"],
        "score": entry["score"],
        "strength": entry["strength"],
        "sha256": entry["sha256"],
        "dpi_scale": entry["dpi_scale"],
        "scale_product": entry["scale_product"],
        "color_theme": entry["color_theme"],
        "icon_theme": entry["icon_theme"],
        "layout_signature": entry["layout_signature"],
        "frame_stability": stability,
        "window_class": class_name,
        "exe_name": exe_name,
    }


def overlay(
    hwnd: int,
    hits: Sequence[Mapping[str, Any]],
    out_png: str | os.PathLike[str],
) -> str:
    """Write a debug image with every candidate rectangle and its score."""
    from PIL import ImageDraw

    from openeyes.core.anchors import classify

    image = _capture_window(int(hwnd)).convert("RGB")
    draw = ImageDraw.Draw(image)
    colors = {"strong": (30, 200, 80), "weak": (255, 170, 0), "miss": (235, 60, 60)}
    for index, hit in enumerate(hits):
        score = float(hit.get("score", 0.0))
        strength = str(hit.get("strength") or classify(score))
        x, y = int(hit["x"]), int(hit["y"])
        width, height = int(hit["w"]), int(hit["h"])
        color = colors.get(strength, colors["miss"])
        draw.rectangle((x, y, x + width, y + height), outline=color, width=2)
        label = f"{index + 1}: {score:.3f} {strength} x{hit.get('scale', 1):.2f}"
        draw.text((x, max(0, y - 13)), label, fill=color)
    target = Path(out_png).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")
    return str(target)
