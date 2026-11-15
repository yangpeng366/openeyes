"""Read a window as a text scene.

A scene is what an agent that cannot look at a picture needs: what is on
screen, what it is called, and where it sits.  Three tiers feed it, in the
order the repository already prefers:

    uia    - the accessibility tree, when the app exposes one
    ocr    - the text of a canvas app, read off the pixels
    opencv - contours for the controls that carry no text at all

Every threshold lives in this module; what a thing *means* stays the
model's call.  The output is evidence with coordinates, never an action.
"""
from __future__ import annotations

import math
import time
from typing import Any

# --- thresholds -------------------------------------------------------------

#: Two OCR fragments join into one line when they overlap this much
#: vertically (0..1).  Half a line height is the usual break point.
LINE_OVERLAP = 0.6

#: ... and when the horizontal gap between them stays below this multiple of
#: the line height.  CJK text has no word gaps, Latin text does.
LINE_GAP_FACTOR = 1.5

#: Text this short inside a rounded rectangle reads as a button.
BUTTON_TEXT_MAX = 6

#: A box this wide (fraction of the window) is a control, not a panel.
BUTTON_MAX_WIDTH_RATIO = 0.4

#: Shape area limits in pixels: below the floor it is noise, above the
#: ceiling it is a page rather than a control.
SHAPE_MIN_AREA = 64
SHAPE_MAX_AREA = 16000

#: A square-shaped box of this area or less reads as a check box.
CHECKBOX_MAX_AREA = 400

#: A contour must fill its bounding box this well to look like a control;
#: glyph strokes and text blobs do not.
SHAPE_MIN_SOLIDITY = 0.72

#: A caret (dropdown triangle) fills about half its bounding box.
#: Solidity ~0.54 rejects it at the 0.72 gate, so it is detected before
#: that filter by fill ratio and compact proportions.
CARET_FILL_RANGE = (0.40, 0.65)
CARET_MIN_SIDE = 8
CARET_MAX_ASPECT = 2.4

#: Fill ratio alone is not enough: a glyph stroke (``v``, ``x``, ``4``, ``w``)
#: after the 5x5 closing also fills 0.40..0.65 of its box, and its outline can
#: have three polygon corners.  What a filled caret has and a stroke never has
#: is a *solid* interior: contour area == hull area (convexity ~1.0) and a hull
#: that simplifies to three sides.  Measured on 91 synthetic triangles and 325
#: non-carets (diamond/square/circle/hexagon + 539-glyph sweep over 4 fonts,
#: 9 sizes, 15 strings): this gate keeps 84 triangles and accepts nothing else.
CARET_MIN_CONVEXITY = 0.85
CARET_HULL_EPSILON = 0.05

#: A caret-shaped contour this far inside one OCR line is that line's ink
#: measured twice (a glyph the OCR already read), not a dropdown arrow.
CARET_TEXT_COVERAGE = 0.5

#: Smallest edge a contour needs, in design pixels, to be a control at all.
SHAPE_MIN_SIDE = 6

#: Bounds on the measured display scale. A DPI read of 0 or a nonsensical
#: value must not erase every shape, and no real monitor exceeds 4x.
SHAPE_SCALE_MIN = 0.25
SHAPE_SCALE_MAX = 4.0

#: OCR text overlapping a UIA element is the same thing reported twice.
DEDUP_IOU = 0.3

#: An OCR line mostly covered by accessibility elements is a duplicate of
#: them, even when no single element contains the whole line (a merged menu
#: bar is covered by many small elements).
DEDUP_COVERAGE = 0.5

#: Canny thresholds for the shape pass.  Deliberately higher than the
#: 1-pixel-border values some other scripts use: those vanish on light
#: themes and at 150% scaling.
CANNY_LOW = 40
CANNY_HIGH = 120


def _is_cjk(char: str) -> bool:
    """True for characters that do not want a space between neighbours."""
    code = ord(char)
    return (
        0x3000 <= code <= 0x303F          # CJK punctuation
        or 0x3400 <= code <= 0x4DBF       # extension A
        or 0x4E00 <= code <= 0x9FFF       # unified ideographs
        or 0xF900 <= code <= 0xFAFF       # compatibility ideographs
        or 0xFF00 <= code <= 0xFFEF       # fullwidth forms
    )


def _union(first: list[int], second: list[int]) -> list[int]:
    """Union of two (x, y, w, h) boxes."""
    x = min(first[0], second[0])
    y = min(first[1], second[1])
    right = max(first[0] + first[2], second[0] + second[2])
    bottom = max(first[1] + first[3], second[1] + second[3])
    return [int(x), int(y), int(right - x), int(bottom - y)]


def _iou(first: list[int], second: list[int]) -> float:
    """Intersection over union of two (x, y, w, h) boxes."""
    x1 = max(first[0], second[0])
    y1 = max(first[1], second[1])
    x2 = min(first[0] + first[2], second[0] + second[2])
    y2 = min(first[1] + first[3], second[1] + second[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    overlap = float((x2 - x1) * (y2 - y1))
    area_a = float(first[2] * first[3])
    area_b = float(second[2] * second[3])
    union = area_a + area_b - overlap
    return overlap / union if union > 0 else 0.0


def _contains(box: list[int], x: float, y: float) -> bool:
    """True when the point sits inside the box."""
    return (box[0] <= x <= box[0] + box[2]
            and box[1] <= y <= box[1] + box[3])


def _bbox_dict(box: list[int]) -> dict[str, int]:
    """(x, y, w, h) -> the dict shape the scene contract uses."""
    return {"x": int(box[0]), "y": int(box[1]),
            "w": int(box[2]), "h": int(box[3])}


def _center_dict(box: list[int]) -> dict[str, int]:
    """The centre of a box, in the same dict shape."""
    return {"x": int(box[0] + box[2] / 2), "y": int(box[1] + box[3] / 2)}


def _aware_dpi() -> None:
    """Put the process into per-monitor DPI aware mode before measuring.

    UI Automation reports *physical* screen pixels, while a DPI-unaware
    process gets virtualised ones from ``GetWindowRect``.  Mixing the two
    spaces is how an element ends up 100 px away from where it is drawn, so
    the rect is always read in the same space the accessibility tree uses.
    """
    try:
        from openeyes.core.dpi import ensure_dpi_awareness

        ensure_dpi_awareness()
    except Exception:
        pass


def _window_info(hwnd: int) -> dict[str, Any]:
    """Title, class, rect, size and DPI of one window; nulls when unknown."""
    info: dict[str, Any] = {"title": None, "class_name": None, "rect": None,
                            "size": None, "dpi_scale": None}
    _aware_dpi()
    try:
        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        info["rect"] = [int(left), int(top), int(right), int(bottom)]
        info["size"] = [int(right - left), int(bottom - top)]
        info["title"] = win32gui.GetWindowText(hwnd) or None
        info["class_name"] = win32gui.GetClassName(hwnd) or None
    except Exception:
        pass
    try:
        import ctypes

        scale = float(ctypes.windll.user32.GetDpiForWindow(int(hwnd))) / 96.0
        info["dpi_scale"] = round(scale, 4) if scale > 0 else None
    except Exception:
        pass
    return info


def _is_minimized(hwnd: int) -> bool:
    """True when the window is minimised (its frame is a placeholder strip)."""
    try:
        import win32gui

        return bool(win32gui.IsIconic(int(hwnd)))
    except Exception:
        return False


def _capture(hwnd: int, capture: Any = None) -> tuple[Any, str]:
    """Grab one frame and name the method that produced it.

    ``PrintWindow`` reads the window itself, so it survives another window
    covering it; a screen grab copies whatever is on top.
    """
    if capture is not None:
        return capture(hwnd), "injected"
    from openeyes.core.capture import capture_window_best

    return capture_window_best(hwnd)


def _capture_screen(hwnd: int) -> tuple[Any, str]:
    """Grab the screen rectangle of a window, whatever is on top of it."""
    from openeyes.core.capture import capture_window

    return capture_window(hwnd), "screen"


def _detect_uia(hwnd: int, detect: Any = None) -> list[Any]:
    """The accessibility elements of one window (empty when it has none)."""
    if detect is not None:
        return list(detect(hwnd) or [])
    from openeyes.core.selector import detect_elements

    return list(detect_elements(hwnd) or [])


def _join_text(items: list[dict[str, Any]]) -> str:
    """Join the fragments of one line, with spaces only where they belong."""
    text = ""
    for item in items:
        fragment = str(item.get("text", "")).strip()
        if not fragment:
            continue
        if text and not (_is_cjk(text[-1]) and _is_cjk(fragment[0])):
            text += " "
        text += fragment
    return text


def _group_lines(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold OCR fragments into reading-order lines.

    Fragments join when they overlap vertically and sit close enough
    horizontally; the result keeps the union box and the best score.
    """
    lines: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda entry: (entry["bbox"][1], entry["bbox"][0])):
        x, y, w, h = item["bbox"]
        placed = False
        for line in lines:
            lx, ly, lw, lh = line["bbox"]
            overlap = min(y + h, ly + lh) - max(y, ly)
            if overlap <= 0:
                continue
            ratio = overlap / float(min(h, lh))
            gap = max(0, max(lx, x) - min(lx + lw, x + w))
            if ratio >= LINE_OVERLAP and gap <= LINE_GAP_FACTOR * max(h, lh):
                line["items"].append(item)
                line["bbox"] = _union(line["bbox"], item["bbox"])
                placed = True
                break
        if not placed:
            lines.append({"items": [item], "bbox": list(item["bbox"])})
    for line in lines:
        line["items"].sort(key=lambda entry: entry["bbox"][0])
        line["text"] = _join_text(line["items"])
        line["score"] = max(float(entry.get("score", 0.0)) for entry in line["items"])
    lines.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
    return lines


def shape_scale(window: dict[str, Any],
                frame_scale: tuple[float, float] | None = None) -> float:
    """Return frame pixels per 96-DPI design pixel.

    Two independent measurements feed it: ``dpi_scale`` (window pixels per
    design pixel, read off the window) and ``frame_scale`` (window pixels
    per *frame* pixel, i.e. ``capture.size != window.size``).  The frame
    correction stands on its own: an unreadable DPI is "assume 96 DPI", not
    "the frame is the window grid".  Dropping it maps a 1.5x frame onto
    design-pixel thresholds, so every threshold shrinks against pixels that
    are 1.5x larger and a real check box reads as a `panel`.
    """
    try:
        dpi_scale = float(window.get("dpi_scale"))
    except (TypeError, ValueError):
        dpi_scale = 1.0
    if not (dpi_scale > 0 and math.isfinite(dpi_scale)):
        dpi_scale = 1.0
    scale = dpi_scale
    if frame_scale:
        try:
            horizontal = float(frame_scale[0])
            vertical = float(frame_scale[1])
        except (IndexError, TypeError, ValueError):
            horizontal = vertical = 0.0
        if (horizontal > 0 and vertical > 0
                and math.isfinite(horizontal) and math.isfinite(vertical)):
            scale = dpi_scale / math.sqrt(horizontal * vertical)
    return round(min(SHAPE_SCALE_MAX, max(SHAPE_SCALE_MIN, scale)), 4)


def _shape_thresholds(scale: float = 1.0) -> dict[str, float]:
    """Convert design-pixel shape thresholds to frame pixels."""
    factor = float(scale) if isinstance(scale, (int, float)) else 1.0
    if not (factor > 0 and math.isfinite(factor)):
        factor = 1.0
    factor = min(SHAPE_SCALE_MAX, max(SHAPE_SCALE_MIN, factor))
    area = factor * factor
    return {
        "min_area": SHAPE_MIN_AREA * area,
        "max_area": SHAPE_MAX_AREA * area,
        "checkbox_max_area": CHECKBOX_MAX_AREA * area,
        "min_side": SHAPE_MIN_SIDE * factor,
        "caret_min_side": CARET_MIN_SIDE * factor,
        "bar_max_height": 16 * factor,
    }


def _shape_kind(width: int, height: int, area: int,
                vertices: int = 0, circularity: float = 0.0,
                checkbox_max_area: float = CHECKBOX_MAX_AREA,
                bar_max_height: float = 16) -> str:
    """Name a contour by its proportions and polygon geometry."""
    if width > 2.2 * height:
        return "bar" if height <= bar_max_height else "panel"
    if abs(width - height) <= max(3, 0.25 * max(width, height)):
        if area <= checkbox_max_area:
            return "checkbox"
        return "panel" if circularity < 0.85 else "icon"
    return "icon"


def _detect_shapes(image: Any, scale: float = 1.0) -> list[dict[str, Any]]:
    """Contours of the frame, with thresholds scaled to frame pixels."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    thresholds = _shape_thresholds(scale)
    try:
        array = np.array(image.convert("RGB"))
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, CANNY_LOW, CANNY_HIGH)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=1)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
    except Exception:
        return []
    shapes: list[dict[str, Any]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = int(width * height)
        if area < thresholds["min_area"] or area > thresholds["max_area"]:
            continue
        if (width < thresholds["min_side"]
                or height < thresholds["min_side"]):
            continue
        solidity = float(cv2.contourArea(contour)) / float(area)
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        vertices = len(approx)
        # Carets (dropdown triangles) have low solidity (~0.5) but are
        # compact and small; detect before the solidity gate drops them.
        # The interior fill is what tells a solid wedge from a glyph stroke.
        hull = cv2.convexHull(contour)
        hull_area = float(cv2.contourArea(hull))
        hull_perimeter = cv2.arcLength(hull, True)
        convexity = (float(cv2.contourArea(contour)) / hull_area
                     if hull_area > 0 else 0.0)
        hull_sides = (len(cv2.approxPolyDP(
            hull, CARET_HULL_EPSILON * hull_perimeter, True))
            if hull_perimeter > 0 else 0)
        if (CARET_FILL_RANGE[0] <= solidity <= CARET_FILL_RANGE[1]
                and convexity >= CARET_MIN_CONVEXITY
                and hull_sides == 3
                and width >= thresholds["caret_min_side"]
                and height >= thresholds["caret_min_side"]
                and max(width, height)
                <= CARET_MAX_ASPECT * min(width, height)):
            shapes.append({
                "kind": "caret",
                "bbox": {"x": int(x), "y": int(y), "w": int(width), "h": int(height)},
                "area": area,
            })
            continue
        if solidity < SHAPE_MIN_SOLIDITY:
            # Text strokes and ragged blobs: not controls.
            continue
        circularity = (4 * np.pi * cv2.contourArea(contour)
                       / (peri * peri) if peri > 0 else 0.0)
        shapes.append({
            "kind": _shape_kind(width, height, area, vertices,
                               circularity, thresholds["checkbox_max_area"],
                               thresholds["bar_max_height"]),
            "bbox": {"x": int(x), "y": int(y), "w": int(width), "h": int(height)},
            "area": area,
        })
    shapes.sort(key=lambda shape: (shape["bbox"]["y"], shape["bbox"]["x"]))
    return shapes


def _uia_kind(control_type: str) -> str:
    """Map an accessibility control type onto a scene kind."""
    mapping = {
        "button": "button",
        "edit": "input",
        "combobox": "input",
        "checkbox": "checkbox",
        "radiobutton": "checkbox",
        "menu": "menu",
        "menuitem": "menu",
    }
    return mapping.get((control_type or "").lower(), "label")


def _scene_space(window: dict[str, Any], frame_size: tuple[int, int] | None
                 ) -> tuple[list[int], tuple[float, float]]:
    """The one pixel grid every bbox in the scene is reported in.

    Returns ``(size, scale)``: ``size`` is the extent of that grid - always
    the window's own physical pixels, i.e. the space
    ``window.rect[:2] + center`` lands on and ``eyes click --x/--y`` takes -
    and ``scale`` converts *frame* pixels into it.

    The accessibility tree already speaks window pixels
    (``schema.Element.bbox`` is window-relative), so it is not scaled.  OCR
    and OpenCV measure the captured frame, which is a different grid when the
    capture path and ``GetWindowRect`` sit at different pixel densities (DPI
    virtualisation, PrintWindow at another zoom).  Resampling those boxes
    into the window grid is what keeps a click landing on the control even
    when ``capture.size != window.size``.
    """
    rect = list(window.get("rect") or [0, 0, 0, 0])
    size = list(window.get("size") or [0, 0])
    width, height = int(size[0] or 0), int(size[1] or 0)
    if (width <= 0 or height <= 0) and len(rect) == 4:
        width = int(rect[2]) - int(rect[0])
        height = int(rect[3]) - int(rect[1])
    width, height = max(0, width), max(0, height)
    if frame_size and int(frame_size[0]) > 0 and int(frame_size[1]) > 0:
        frame_width, frame_height = int(frame_size[0]), int(frame_size[1])
        if width > 0 and height > 0:
            return [width, height], (width / frame_width, height / frame_height)
    return [width, height], (1.0, 1.0)


def _scale_box(box: list[int], scale: tuple[float, float]) -> list[int]:
    """Frame pixels -> the scene grid's window pixels."""
    return [int(round(box[0] * scale[0])), int(round(box[1] * scale[1])),
            int(round(box[2] * scale[0])), int(round(box[3] * scale[1]))]


def _clamp_box(box: list[int], width: int, height: int) -> list[int] | None:
    """Clip a box to the window, or drop it when its centre is outside.

    A box whose centre falls outside the window is not an executable
    position: an agent that clicks that centre hits some other window.
    Chromium on a virtual desktop reports whole subtrees outside the frame,
    so this is a real case, not a theoretical one.
    """
    if width <= 0 or height <= 0:
        return box if box[2] > 0 and box[3] > 0 else None
    if box[2] <= 0 or box[3] <= 0:
        return None
    center_x = box[0] + box[2] / 2.0
    center_y = box[1] + box[3] / 2.0
    if not (0 <= center_x < width and 0 <= center_y < height):
        return None
    left, top = max(0, box[0]), max(0, box[1])
    right = min(width, box[0] + box[2])
    bottom = min(height, box[1] + box[3])
    if right <= left or bottom <= top:
        return None
    return [left, top, right - left, bottom - top]


def _uia_element(element: Any) -> dict[str, Any] | None:
    """One accessibility element, in the scene shape.

    ``element.bbox`` is window-relative already (the uia backend shifts the
    absolute UIA rectangle by the window origin while it walks) and the
    scene grid *is* the window's pixel grid, so nothing is rescaled here.

    ``None`` when the element has no area: Chromium reports shell views with
    an empty rectangle, and those are noise in a digest.
    """
    bbox = getattr(element, "bbox", None)
    if bbox is None:
        return None
    box = [int(bbox.x), int(bbox.y), int(bbox.w), int(bbox.h)]
    if box[2] <= 0 or box[3] <= 0:
        return None
    state = getattr(element, "state", None)
    control_type = (getattr(element, "control_type", "") or "").strip()
    name = (getattr(element, "name", "") or "").strip()
    # An unnamed element still says what it is; the type is the best name
    # an accessibility tree offers.
    text = name or control_type
    if text.endswith(SHELL_NAME_SUFFIXES):
        return None
    return {
        "kind": _uia_kind(control_type),
        "text": text,
        "bbox": _bbox_dict(box),
        "center": _center_dict(box),
        "score": round(float(getattr(element, "score", 1.0) or 0.0), 4),
        "source": "uia",
        "interactive": bool(getattr(element, "interactive", False)),
        "state": {
            "focused": bool(getattr(state, "focused", False)),
            "enabled": bool(getattr(state, "enabled", True)),
        },
    }


def _classify_text(line: dict[str, Any], shapes: list[dict[str, Any]],
                   window_width: int) -> str:
    """Name a piece of OCR text by the box it sits in."""
    box = line["bbox"]
    center_x = box[0] + box[2] / 2.0
    center_y = box[1] + box[3] / 2.0
    for shape in shapes:
        shape_box = [shape["bbox"]["x"], shape["bbox"]["y"],
                     shape["bbox"]["w"], shape["bbox"]["h"]]
        if not _contains(shape_box, center_x, center_y):
            continue
        width = shape_box[2]
        height = shape_box[3]
        if width > 2.2 * height:
            continue
        if window_width and width > BUTTON_MAX_WIDTH_RATIO * window_width:
            continue
        if len(line["text"]) <= BUTTON_TEXT_MAX and height <= 80:
            return "button"
    return "label"


def _text_element(line: dict[str, Any], kind: str) -> dict[str, Any]:
    """One OCR line, in the scene shape."""
    box = list(line["bbox"])
    return {
        "kind": kind,
        "text": line["text"],
        "bbox": _bbox_dict(box),
        "center": _center_dict(box),
        "score": round(float(line.get("score", 0.0)), 4),
        "source": "ocr",
        "interactive": kind in {"button", "input", "checkbox", "menu"},
        "state": {"focused": False, "enabled": True},
    }


def _shape_element(shape: dict[str, Any]) -> dict[str, Any]:
    """One contour without text, in the scene shape.

    A contour is geometric evidence, not a control: it is reported with a
    position and a shape kind, and never marked interactive.  Whether the
    thing is clickable is the model's call after reading the name next to
    it, not a conclusion a Canny edge can support.
    """
    box = [shape["bbox"]["x"], shape["bbox"]["y"],
           shape["bbox"]["w"], shape["bbox"]["h"]]
    kind = shape["kind"]
    if kind == "bar":
        kind = "panel"
    return {
        "kind": kind,
        "text": "",
        "bbox": _bbox_dict(box),
        "center": _center_dict(box),
        "score": 0.5,
        "source": "opencv",
        "interactive": False,
        "state": {"focused": False, "enabled": True},
    }


def _failure(hwnd: int, window: dict[str, Any], error_code: str,
             started: float, notes: list[str],
             requested_source: str = "auto",
             reason: str | None = None) -> dict[str, Any]:
    """An empty scene that says why it is empty, in one code and one line."""
    return {
        "ok": False,
        "source": "",
        "requested_source": requested_source,
        "hwnd": int(hwnd),
        "window": window,
        "capture": {"method": None, "occluded": False, "size": None,
                    "region": None, "verified": None, "suppressed": []},
        "elements": [],
        "text_lines": [],
        "shapes": [],
        "counts": {},
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 2),
        "error_code": error_code,
        "reason": reason,
        "notes": notes,
    }


#: The tiers ``source`` may name.
SOURCES = ("auto", "uia", "ocr")


def _note_with(notes: list[str], *prefixes: str) -> str | None:
    """The first note that starts with one of *prefixes*."""
    for note in notes:
        if note.startswith(prefixes):
            return note
    return None


def _is_foreground(hwnd: int) -> bool:
    """True when this window is the one in front.

    Necessary but not sufficient for a screen grab: a window can be in front
    and still be partly covered.  An unreadable foreground is ``False`` -
    the check fails closed.
    """
    try:
        from openeyes.core.windows import foreground_window

        return int(foreground_window()) == int(hwnd)
    except Exception:
        return False


#: How many points per axis the occlusion probe samples across a window.
OCCLUSION_SAMPLES = 6


def _screen_view_clear(hwnd: int) -> tuple[bool, str | None]:
    """Is every sampled point of the window rectangle showing this window?

    ``WindowFromPoint`` names the window that owns a screen pixel, so a
    sample whose root window is not *hwnd* proves the rectangle is at least
    partly covered: a screen grab of it would mix two windows' pixels.  Being
    foreground is necessary but not sufficient - an always-on-top window, a
    notification toast or a docked panel covers part of a foreground window
    while the foreground check still passes.

    Returns ``(clear, reason)``.  Anything the check cannot establish -
    win32 missing, an unreadable rectangle, a sampling call that raises -
    is ``(False, ...)``: an unproven frame is never treated as trustworthy.
    """
    try:
        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(int(hwnd))
    except Exception as exc:
        return False, f"the window rectangle cannot be read ({type(exc).__name__})"
    width, height = int(right) - int(left), int(bottom) - int(top)
    if width <= 0 or height <= 0:
        return False, "the window rectangle has no area"
    # Stay away from the rounded frame corners: a point there may belong to
    # the desktop even though the window is fully visible.
    margin_x = max(1, width // 32)
    margin_y = max(1, height // 32)
    for column in range(OCCLUSION_SAMPLES):
        x = int(left) + margin_x + int(
            column * (width - 2 * margin_x) / max(1, OCCLUSION_SAMPLES - 1))
        for row in range(OCCLUSION_SAMPLES):
            y = int(top) + margin_y + int(
                row * (height - 2 * margin_y) / max(1, OCCLUSION_SAMPLES - 1))
            try:
                hit = win32gui.WindowFromPoint((x, y))
                root = int(win32gui.GetAncestor(hit, 2)) if hit else 0
            except Exception:
                return False, f"occlusion sampling failed at ({x},{y})"
            if root != int(hwnd):
                return False, (f"screen point ({x},{y}) belongs to "
                               f"hwnd={root}, not hwnd={int(hwnd)}")
    return True, None


def _screen_is_readable(hwnd: int, on_screen: bool, foreground: bool
                        ) -> tuple[bool, str | None]:
    """Can a screen grab of *hwnd* be shown to hold only its own pixels?"""
    if not on_screen:
        return False, ("the window is off-screen, so a screen grab cannot "
                       "show its pixels")
    if not foreground:
        return False, ("the window is not the foreground window, so a screen "
                       "grab may hold another window's pixels")
    clear, reason = _screen_view_clear(hwnd)
    if not clear:
        return False, ("the window is in front but its rectangle is not "
                       "fully visible: " + (reason or "another window overlaps it"))
    return True, None


def _frame_provenance(method: str | None, screen: tuple[bool, str | None]
                      ) -> tuple[bool, str | None]:
    """Can this frame be shown to be *hwnd*'s own pixels?

    ``printwindow`` makes the window render itself, so the answer is yes by
    construction; ``injected`` is a frame the caller handed in.  A ``screen``
    grab is only its picture when ``screen`` (the foreground / on-screen /
    unobstructed check) says so.
    """
    if method is None:
        return False, None
    if method in {"printwindow", "injected"}:
        return True, None
    if method != "screen":
        return False, f"unknown capture method {method!r}"
    return screen


def _validate_region(region: Any) -> tuple[int, int, int, int] | None:
    """A (left, top, right, bottom) crop, or None when it is not one."""
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        return None
    try:
        left, top, right, bottom = (int(value) for value in region)
    except (TypeError, ValueError):
        return None
    if left < 0 or top < 0 or right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _rect_size(rect: Any) -> tuple[int, int] | None:
    """The window rectangle's size, or None when there is no usable one."""
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        return None
    try:
        left, top, right, bottom = (int(value) for value in rect)
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return right - left, bottom - top


def _crop_frame(image: Any, crop: tuple[int, int, int, int] | None
                ) -> tuple[Any, str | None]:
    """The part of a frame the caller asked about, or a structured problem."""
    if image is None or crop is None:
        return image, None
    try:
        return image.crop(crop), None
    except Exception as exc:
        return None, f"crop-failed: {type(exc).__name__}: {exc}"


def describe_window(hwnd: int, *, source: str = "auto", include_ocr: bool = True,
                    include_shapes: bool = True, max_items: int = 250,
                    region: tuple[int, int, int, int] | None = None,
                    info: Any = None,
                    capture: Any = None, detect: Any = None,
                    engine: Any = None) -> dict[str, Any]:
    """Describe one window as a scene of elements.

    ``source`` picks the tier: ``auto`` prefers the accessibility tree and
    adds OCR on top, ``uia`` reads the accessibility tree only (never OCR),
    ``ocr`` reads pixels only.  ``capture`` and ``detect`` are injection
    points for tests; ``engine`` injects an OCR engine.

    Every ``bbox``/``center`` is window-relative **physical pixels of the
    window itself** - the grid ``window.size`` describes, the one ``eyes
    click --x/--y`` turns into a screen point by adding ``window.rect[:2]``
    once.  A captured frame is a different grid when the capture path and
    ``GetWindowRect`` disagree on pixel density; OCR and OpenCV boxes are
    then resampled into the window grid (``capture.size`` keeps the frame's
    own size), so ``capture.size != window.size`` never shifts a click.

    Pixels are read only when they can be shown to be this window's.  A
    screen grab of a window that is not in front - or that is partly covered
    while in front - may hold another window's pixels, so it is never read:
    its OCR/OpenCV results are suppressed (``capture.verified=False``,
    ``capture.suppressed``) instead of being relabelled as this page.

    ``source="uia"`` reads the accessibility tree only: no frame is grabbed
    and no OpenCV shapes are produced, because both are visual features and
    have to be asked for through ``auto`` / ``ocr``.

    Parameters and the window rectangle are validated rather than trusted: a
    bad ``source`` / ``region`` or an unreadable rectangle comes back as
    ``ok=False`` with a code and a reason, and a failed capture, crop or
    shape pass never raises out of here.
    """
    started = time.perf_counter()
    notes: list[str] = []
    hwnd = int(hwnd)
    window = dict(info(hwnd) if info is not None else _window_info(hwnd))
    requested = source if isinstance(source, str) else ""

    if source not in SOURCES:
        return _failure(hwnd, window, "INVALID_SOURCE", started, notes, requested,
                        reason=f"source must be one of {', '.join(SOURCES)}")
    if source == "ocr" and not include_ocr:
        return _failure(hwnd, window, "INVALID_SOURCE", started, notes, requested,
                        reason="source=ocr with include_ocr=False leaves "
                               "nothing to read")
    if region is not None:
        checked = _validate_region(region)
        if checked is None:
            return _failure(hwnd, window, "INVALID_REGION", started, notes, source,
                            reason="region must be left,top,right,bottom with "
                                   "0 <= left < right and 0 <= top < bottom")
        region = checked
    if not window.get("rect"):
        return _failure(hwnd, window, "INVALID_HWND", started, notes, source,
                        reason="the hwnd has no window rectangle")
    rect_size = _rect_size(window.get("rect"))
    if rect_size is None:
        return _failure(hwnd, window, "INVALID_HWND", started, notes, source,
                        reason="the window rectangle is malformed or has no "
                               "area, so no window-relative coordinate can "
                               "be trusted")
    # The scene grid is the window's own pixel grid; a frame is resampled
    # into it, never the other way round.
    window["size"] = [int(rect_size[0]), int(rect_size[1])]
    if _is_minimized(hwnd):
        return _failure(hwnd, window, "WINDOW_MINIMIZED", started, notes, source,
                        reason="a minimised window has no readable surface")

    uia_attempted = source in {"auto", "uia"}
    uia_elements: list[Any] = []
    uia_error: str | None = None
    if uia_attempted:
        try:
            uia_elements = _detect_uia(hwnd, detect)
        except Exception as exc:
            uia_error = f"uia-unavailable: {type(exc).__name__}: {exc}"
            notes.append(uia_error)

    # A frame is only worth grabbing for something that will read it: the
    # shape pass, or OCR.  ``source="uia"`` is the accessibility tree and
    # nothing else, so it neither grabs a frame nor reports OpenCV contours
    # unless a pixel tier (``auto`` / ``ocr``) was asked for.
    read_ocr = bool(include_ocr) and source in {"auto", "ocr"}
    read_shapes = bool(include_shapes) and source in {"auto", "ocr"}
    if include_shapes and not read_shapes:
        notes.append("shapes-skipped: source=uia reads no pixels")
    want_frame = read_shapes or read_ocr

    image = None
    method: str | None = None
    frame_size: tuple[int, int] | None = None
    capture_error: str | None = None
    if want_frame:
        try:
            image, method = _capture(hwnd, capture)
        except Exception as exc:
            capture_error = f"capture-failed: {type(exc).__name__}: {exc}"
        if capture_error is None and image is None:
            capture_error = "capture-failed: the capture produced no frame"
        if capture_error:
            notes.append(capture_error)
        if image is not None:
            frame_size = (int(image.width), int(image.height))

    offset_x = offset_y = 0
    crop: tuple[int, int, int, int] | None = None
    region_error: str | None = None
    if region is not None and image is not None:
        frame_width, frame_height = frame_size or (0, 0)
        left = min(max(0, region[0]), frame_width)
        top = min(max(0, region[1]), frame_height)
        right = min(max(0, region[2]), frame_width)
        bottom = min(max(0, region[3]), frame_height)
        if right <= left or bottom <= top:
            region_error = (f"region-outside-frame: {list(region)} does not "
                            f"intersect the {frame_width}x{frame_height} frame")
            notes.append(region_error)
            image = None
        else:
            crop = (left, top, right, bottom)
            offset_x, offset_y = left, top
            if crop != tuple(region):
                notes.append(f"region-clamped: {list(region)} -> {list(crop)}")

    on_screen = _on_screen(hwnd)
    foreground = _is_foreground(hwnd)
    screen = ((_screen_is_readable(hwnd, on_screen, foreground))
              if method == "screen" and image is not None else (False, None))
    trustworthy, distrust = _frame_provenance(
        method if image is not None else None, screen)
    suppressed: list[str] = []
    frame, crop_error = _crop_frame(image, crop)
    if crop_error:
        notes.append(crop_error)
        frame = None
    if image is not None and not trustworthy:
        suppressed = ["ocr", "opencv"]
        notes.append(f"unverified-frame: {distrust}; its pixels were suppressed")
        frame = None

    # ``uia`` means the accessibility tree and nothing else: OCR is not
    # initialised, and an unreadable PrintWindow frame does not authorise a
    # screen grab either.  ``include_ocr=False`` is the explicit off switch.
    ocr_items: list[dict[str, Any]] = []
    ocr_failed = False
    if read_ocr and frame is not None:
        try:
            from openeyes.backends.ocr import read_text

            ocr_items = read_text(frame, engine=engine)
        except Exception as exc:
            # ``source="ocr"`` is pixels only: the tree is not enumerated,
            # so there is nothing to fall back to and the receipt says so
            # through ``error_code``.  Never claim a fallback that did not
            # happen, and do not spend a second grab on an engine that is
            # already known to be unusable.
            ocr_failed = True
            notes.append(f"ocr-unavailable: {type(exc).__name__}: {exc}")

    if (read_ocr and frame is not None and not ocr_failed and not ocr_items
            and method == "printwindow" and capture is None):
        # The window painted itself but no text came out: some toolkits
        # answer PrintWindow with an empty surface.  A screen grab is only
        # allowed while it can be shown to read *this* window - in front,
        # on screen, and not partly covered.
        screen_ok, screen_reason = _screen_is_readable(
            hwnd, _on_screen(hwnd), _is_foreground(hwnd))
        if not screen_ok:
            notes.append("screen fallback skipped: "
                         + (screen_reason or "the frame is unproven"))
        else:
            try:
                from openeyes.backends.ocr import read_text

                alt_image, alt_method = _capture_screen(hwnd)
                alt_frame, alt_crop_error = _crop_frame(alt_image, crop)
                if alt_crop_error:
                    notes.append(alt_crop_error)
                # Recheck the captured frame before OCR so a newly covered
                # window's pixels are never passed to the OCR engine.
                grab_ok, grab_reason = _screen_is_readable(
                    hwnd, _on_screen(hwnd), _is_foreground(hwnd))
                if not grab_ok:
                    notes.append("screen fallback discarded before OCR: "
                                 + (grab_reason or "unproven"))
                else:
                    alt_items = (read_text(alt_frame, engine=engine)
                                 if alt_frame is not None else [])
                    if alt_items:
                        # The desktop can change while OCR runs; only expose
                        # results if the window is still fully readable.
                        recheck, recheck_reason = _screen_is_readable(
                            hwnd, _on_screen(hwnd), _is_foreground(hwnd))
                        if recheck:
                            image, method, ocr_items = alt_image, alt_method, alt_items
                            frame = alt_frame
                            frame_size = (int(alt_image.width), int(alt_image.height))
                            screen = (True, None)
                            notes.append("printwindow frame carried no text; "
                                         "fell back to a screen grab "
                                         "(window in front and unobstructed)")
                        else:
                            notes.append("screen fallback discarded: "
                                         + (recheck_reason or "unproven"))
            except Exception as exc:
                notes.append(f"screen-fallback-failed: {type(exc).__name__}: {exc}")

    space, frame_scale = _scene_space(window, frame_size)
    window["size"] = list(space)
    space_width, space_height = int(space[0]), int(space[1])

    shapes: list[dict[str, Any]] = []
    if read_shapes and frame is not None:
        pixel_scale = shape_scale(window, frame_scale)
        if abs(pixel_scale - 1.0) > 1e-6:
            notes.append(f"shape-scale: {pixel_scale:g} device pixels per "
                         "96-DPI design pixel")
        try:
            shapes = _detect_shapes(frame, scale=pixel_scale)
        except Exception as exc:
            notes.append(f"shapes-failed: {type(exc).__name__}: {exc}")
        for shape in shapes:
            box = [shape["bbox"]["x"] + offset_x,
                   shape["bbox"]["y"] + offset_y,
                   shape["bbox"]["w"], shape["bbox"]["h"]]
            shape["bbox"] = _bbox_dict(_scale_box(box, frame_scale))

    lines = _group_lines(ocr_items)
    for line in lines:
        box = [line["bbox"][0] + offset_x, line["bbox"][1] + offset_y,
               line["bbox"][2], line["bbox"][3]]
        line["bbox"] = _scale_box(box, frame_scale)

    elements: list[dict[str, Any]] = []
    uia_boxes: list[list[int]] = []
    off_window = 0
    for element in uia_elements:
        scene_element = _uia_element(element)
        if scene_element is None:
            continue
        if ocr_items and not scene_element["interactive"]:
            # Chromium names its view shells "Pane", "ContentsView", ...
            # - structure, not content.  Once OCR has the words, only the
            # actionable controls from the tree are worth keeping.
            continue
        box = _clamp_box([scene_element["bbox"]["x"], scene_element["bbox"]["y"],
                          scene_element["bbox"]["w"], scene_element["bbox"]["h"]],
                         space_width, space_height)
        if box is None:
            off_window += 1
            continue
        if box != [scene_element["bbox"]["x"], scene_element["bbox"]["y"],
                   scene_element["bbox"]["w"], scene_element["bbox"]["h"]]:
            scene_element["bbox"] = _bbox_dict(box)
            scene_element["center"] = _center_dict(box)
        elements.append(scene_element)
        uia_boxes.append(list(box))

    if off_window:
        notes.append(f"uia-outside-window: {off_window} element(s) dropped")

    window_width = int((window.get("size") or [0, 0])[0])
    text_lines: list[str] = []
    for line in lines:
        box = line["bbox"]
        comparable = [other for other in uia_boxes if _comparable(box, other)]
        duplicate = any(
            _iou(box, other) > DEDUP_IOU
            or _contains(other, box[0] + box[2] / 2.0, box[1] + box[3] / 2.0)
            for other in comparable
        ) or _coverage(box, comparable) >= DEDUP_COVERAGE
        if duplicate:
            continue
        text_lines.append(line["text"])
        elements.append(_text_element(line, _classify_text(line, shapes, window_width)))

    shape_boxes = [[shape["bbox"]["x"], shape["bbox"]["y"],
                    shape["bbox"]["w"], shape["bbox"]["h"]] for shape in shapes]
    for idx, shape in enumerate(shapes):
        if shape["kind"] not in {"checkbox", "icon", "caret"}:
            continue
        box = shape_boxes[idx]
        if any(_iou(box, element_box) > DEDUP_IOU for element_box in uia_boxes):
            continue
        if any(_contains(line["bbox"],
                         box[0] + box[2] / 2.0,
                         box[1] + box[3] / 2.0)
               for line in lines):
            continue
        if shape["kind"] == "caret" and any(
            _coverage(box, [line["bbox"]]) >= CARET_TEXT_COVERAGE
            for line in lines
        ):
            # The dedup runs shape -> text: a caret contour that lies mostly
            # inside one line of text is that line's ink read twice (OCR has
            # the glyph, Canny has its outline), not a dropdown arrow.  A
            # real arrow sits beside the text, or in an empty combo box, so
            # a text line never covers half of it.
            continue
        elements.append(_shape_element(shape))

    elements.sort(key=lambda element: (element["bbox"]["y"], element["bbox"]["x"]))
    if max_items and len(elements) > max_items:
        notes.append(f"truncated: {len(elements) - max_items} elements dropped")
        elements = elements[:max_items]

    counts: dict[str, int] = {}
    for element in elements:
        counts[element["kind"]] = counts.get(element["kind"], 0) + 1

    if uia_elements and (ocr_items or shapes):
        tier = "mixed"
    elif uia_elements:
        tier = "uia"
    elif ocr_items or shapes:
        tier = "ocr"
    else:
        tier = ""

    occluded = bool(method == "screen" and not screen[0])
    if not on_screen:
        notes.append("window is off-screen; a screen grab would read other pixels")

    ok = bool(elements or text_lines)
    error_code = None
    reason = None
    if not ok:
        if region_error:
            error_code, reason = "INVALID_REGION", region_error
        elif crop_error:
            error_code, reason = "CAPTURE_FAILED", crop_error
        elif image is None and want_frame:
            error_code = "CAPTURE_FAILED"
            reason = (capture_error
                      or _note_with(notes, "capture-failed", "crop-failed")
                      or "no frame could be read from this window")
        elif ocr_failed:
            error_code = "OCR_UNAVAILABLE"
            reason = (_note_with(notes, "ocr-unavailable")
                      or "the OCR engine is unavailable")
        elif image is not None and not trustworthy:
            error_code = "UNVERIFIED_FRAME"
            reason = distrust or "the frame cannot be proven to be this window"
        elif uia_error and not uia_elements:
            error_code = "UIA_UNAVAILABLE"
            reason = uia_error
        elif uia_attempted and not uia_elements:
            error_code = "UIA_EMPTY"
            reason = "the accessibility tree of this window reports no elements"
        elif uia_elements:
            error_code = "SCENE_FILTERED_EMPTY"
            reason = ("the tree returned elements but none survived the "
                      "window/shell/interactive filters")
        else:
            error_code = "CAPTURE_FAILED"
            reason = "nothing readable was produced for this window"

    effective_region = (list(crop) if crop is not None
                        else list(region) if region is not None else None)
    return {
        "ok": ok,
        "source": tier,
        "requested_source": source,
        "hwnd": hwnd,
        "window": window,
        "capture": {"method": method, "occluded": occluded,
                    "size": list(frame_size) if frame_size else None,
                    "region": effective_region,
                    "verified": trustworthy if image is not None else None,
                    "suppressed": suppressed},
        "elements": elements,
        "text_lines": text_lines,
        "shapes": shapes,
        "counts": counts,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 2),
        "error_code": error_code,
        "reason": reason,
        "notes": notes,
    }


def _coverage(box: list[int], others: list[list[int]]) -> float:
    """Share of a box that is covered by the union of the other boxes."""
    bw, bh = box[2], box[3]
    area = float(bw * bh)
    if bw <= 0 or bh <= 0 or not others:
        return 0.0
    # Paint intersections onto a boolean mask so overlapping UIA
    # elements are not double-counted (two half-covers should sum to
    # 0.5, not 1.0).
    try:
        import numpy as np
        mask = np.zeros((bh, bw), dtype=bool)
        for other in others:
            x1 = max(0, other[0] - box[0])
            y1 = max(0, other[1] - box[1])
            x2 = min(bw, other[0] + other[2] - box[0])
            y2 = min(bh, other[1] + other[3] - box[1])
            if x2 > x1 and y2 > y1:
                mask[y1:y2, x1:x2] = True
        return float(mask.sum()) / area
    except ImportError:
        rectangles: list[tuple[int, int, int, int]] = []
        for other in others:
            x1 = max(0, other[0] - box[0])
            y1 = max(0, other[1] - box[1])
            x2 = min(bw, other[0] + other[2] - box[0])
            y2 = min(bh, other[1] + other[3] - box[1])
            if x2 > x1 and y2 > y1:
                rectangles.append((x1, y1, x2, y2))
        if not rectangles:
            return 0.0
        covered = 0
        x_edges = sorted({edge for rect in rectangles for edge in (rect[0], rect[2])})
        # Shifted pair, so the two iterables differ by one by construction:
        # ``strict=True`` here would raise on every non-empty call.
        for left, right in zip(x_edges, x_edges[1:], strict=False):
            if right <= left:
                continue
            spans = [(rect[1], rect[3]) for rect in rectangles
                     if rect[0] < right and rect[2] > left]
            spans.sort()
            y_covered = 0
            current_start, current_end = spans[0]
            for start, end in spans[1:]:
                if start > current_end:
                    y_covered += current_end - current_start
                    current_start, current_end = start, end
                else:
                    current_end = max(current_end, end)
            y_covered += current_end - current_start
            covered += (right - left) * y_covered
        return min(1.0, covered / area)


def _on_screen(hwnd: int) -> bool:
    """True when the window rect touches the virtual screen.

    A window parked on another virtual desktop has a rect outside every
    screen, and a screen grab of it would read the wrong pixels.  Metrics
    that cannot be read are ``False``: this answer gates a pixel read, so an
    unknown screen must not be treated as a known-good one.
    """
    try:
        import win32api
        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        vx = win32api.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        vy = win32api.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        vw = win32api.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        vh = win32api.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    except Exception:
        return False
    return not (right <= vx or left >= vx + vw or bottom <= vy or top >= vy + vh)


def _comparable(box: list[int], other: list[int]) -> bool:
    """True when a UIA box could be the very same thing as this text.

    Chromium exposes whole-window containers as elements; a container is not
    a duplicate of one line of text, so only comparable sizes may dedup.
    """
    if other[3] > 3 * max(box[3], 8):
        return False
    return other[2] <= 4 * max(box[2], 16)


#: Chromium names its view shells "Pane", "ContentsView", "SideEdgeView" -
#: structure, not content.  Such names carry no information for a reader.
SHELL_NAME_SUFFIXES = ("View", "Pane", "Layer", "Surface", "Container")


