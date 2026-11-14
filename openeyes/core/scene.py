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


def _window_info(hwnd: int) -> dict[str, Any]:
    """Title, class, rect, size and DPI of one window; nulls when unknown."""
    info: dict[str, Any] = {"title": None, "class_name": None, "rect": None,
                            "size": None, "dpi_scale": None}
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


def _shape_kind(width: int, height: int, area: int) -> str:
    """Name a contour by its proportions."""
    if width > 2.2 * height:
        return "bar" if height <= 16 else "panel"
    if abs(width - height) <= max(3, 0.25 * max(width, height)):
        return "checkbox" if area <= CHECKBOX_MAX_AREA else "panel"
    return "icon"


def _detect_shapes(image: Any) -> list[dict[str, Any]]:
    """Contours of the frame: panels, check boxes, icons and bars."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    try:
        array = np.array(image.convert("RGB"))
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, CANNY_LOW, CANNY_HIGH)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
    except Exception:
        return []
    shapes: list[dict[str, Any]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = int(width * height)
        if area < SHAPE_MIN_AREA or area > SHAPE_MAX_AREA:
            continue
        if width < 6 or height < 6:
            continue
        solidity = float(cv2.contourArea(contour)) / float(area)
        if solidity < SHAPE_MIN_SOLIDITY:
            # Text strokes and ragged blobs: not controls.
            continue
        shapes.append({
            "kind": _shape_kind(width, height, area),
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


def _uia_element(element: Any) -> dict[str, Any] | None:
    """One accessibility element, in the scene shape.

    ``None`` when the element has no area: Chromium reports shell views with
    an empty rectangle, and those are noise in a digest.
    """
    bbox = getattr(element, "bbox", None)
    box = [int(bbox.x), int(bbox.y), int(bbox.w), int(bbox.h)] if bbox else [0, 0, 0, 0]
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
    """One contour without text, in the scene shape."""
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
        "interactive": kind in {"checkbox", "icon"},
        "state": {"focused": False, "enabled": True},
    }


def _failure(hwnd: int, window: dict[str, Any], error_code: str,
             started: float, notes: list[str]) -> dict[str, Any]:
    """An empty scene that says why it is empty."""
    return {
        "ok": False,
        "source": "",
        "hwnd": int(hwnd),
        "window": window,
        "capture": {"method": None, "occluded": False},
        "elements": [],
        "text_lines": [],
        "shapes": [],
        "counts": {},
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 2),
        "error_code": error_code,
        "notes": notes,
    }


def describe_window(hwnd: int, *, source: str = "auto", include_ocr: bool = True,
                    include_shapes: bool = True, max_items: int = 250,
                    region: tuple[int, int, int, int] | None = None,
                    info: Any = None,
                    capture: Any = None, detect: Any = None,
                    engine: Any = None) -> dict[str, Any]:
    """Describe one window as a scene of elements.

    ``source`` picks the tier: ``auto`` prefers the accessibility tree and
    adds OCR on top, ``uia`` and ``ocr`` run one each.  ``capture`` and
    ``detect`` are injection points for tests; ``engine`` injects an OCR
    engine.

    The answer never raises for a missing backend: it degrades, notes why,
    and only reports ``ok=False`` when nothing at all could be read.
    """
    started = time.perf_counter()
    notes: list[str] = []
    hwnd = int(hwnd)
    window = info(hwnd) if info is not None else _window_info(hwnd)
    if not window.get("rect"):
        return _failure(hwnd, window, "INVALID_HWND", started, notes)
    if _is_minimized(hwnd):
        return _failure(hwnd, window, "WINDOW_MINIMIZED", started, notes)

    uia_elements: list[Any] = []
    if source in {"auto", "uia"}:
        try:
            uia_elements = _detect_uia(hwnd, detect)
        except Exception as exc:
            notes.append(f"uia-unavailable: {type(exc).__name__}")

    image = None
    method = None
    if include_ocr or include_shapes:
        try:
            image, method = _capture(hwnd, capture)
        except Exception as exc:
            notes.append(f"capture-failed: {type(exc).__name__}: {exc}")

    offset_x = offset_y = 0
    if image is not None and region is not None:
        left, top, right, bottom = (int(value) for value in region)
        image = image.crop((left, top, right, bottom))
        offset_x, offset_y = left, top

    ocr_items: list[dict[str, Any]] = []
    if include_ocr and image is not None and source in {"auto", "ocr"}:
        try:
            from openeyes.backends.ocr import read_text

            ocr_items = read_text(image, engine=engine)
        except Exception as exc:
            notes.append(f"ocr-unavailable: {type(exc).__name__}: {exc}")
            if source == "ocr":
                notes.append("scene fell back to the accessibility tree")

    on_screen = _on_screen(hwnd)
    if (include_ocr and image is not None and not ocr_items
            and method == "printwindow" and capture is None and on_screen):
        # The window painted itself but no text came out: some toolkits
        # answer PrintWindow with an empty surface.  Try the screen, which
        # is only truthful when the window is in front - say so later.
        try:
            from openeyes.backends.ocr import read_text

            alt_image, alt_method = _capture_screen(hwnd)
            alt_items = read_text(alt_image, engine=engine)
            if alt_items:
                image, method, ocr_items = alt_image, alt_method, alt_items
                notes.append("printwindow frame carried no text; "
                            "fell back to a screen grab")
        except Exception as exc:
            notes.append(f"screen-fallback-failed: {type(exc).__name__}")

    shapes: list[dict[str, Any]] = []
    if include_shapes and image is not None:
        shapes = _detect_shapes(image)

    lines = _group_lines(ocr_items)
    for line in lines:
        line["bbox"] = [line["bbox"][0] + offset_x, line["bbox"][1] + offset_y,
                        line["bbox"][2], line["bbox"][3]]

    elements: list[dict[str, Any]] = []
    uia_boxes: list[list[int]] = []
    for element in uia_elements:
        scene_element = _uia_element(element)
        if scene_element is None:
            continue
        if ocr_items and not scene_element["interactive"]:
            # Chromium names its view shells "Pane", "ContentsView", ...
            # - structure, not content.  Once OCR has the words, only the
            # actionable controls from the tree are worth keeping.
            continue
        box = [scene_element["bbox"]["x"], scene_element["bbox"]["y"],
               scene_element["bbox"]["w"], scene_element["bbox"]["h"]]
        if not _within_window(box, window):
            continue
        elements.append(scene_element)
        uia_boxes.append([scene_element["bbox"]["x"], scene_element["bbox"]["y"],
                          scene_element["bbox"]["w"], scene_element["bbox"]["h"]])

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
    for shape in shapes:
        if shape["kind"] not in {"checkbox", "icon"}:
            continue
        box = shape_boxes[shapes.index(shape)]
        if any(_iou(box, element_box) > DEDUP_IOU for element_box in uia_boxes):
            continue
        if any(_contains(box, line["bbox"][0] + line["bbox"][2] / 2.0,
                         line["bbox"][1] + line["bbox"][3] / 2.0)
               for line in lines):
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

    occluded = False
    if method == "screen":
        try:
            from openeyes.core.windows import foreground_window

            occluded = int(foreground_window()) != hwnd
        except Exception:
            occluded = False
    if not on_screen:
        notes.append("window is off-screen; a screen grab would read other pixels")

    ok = bool(elements or text_lines)
    error_code = None
    if not ok:
        error_code = "OCR_UNAVAILABLE" if any(
            note.startswith("ocr-unavailable") for note in notes
        ) else "CAPTURE_FAILED"

    return {
        "ok": ok,
        "source": tier,
        "hwnd": hwnd,
        "window": window,
        "capture": {"method": method, "occluded": occluded},
        "elements": elements,
        "text_lines": text_lines,
        "shapes": shapes,
        "counts": counts,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 2),
        "error_code": error_code,
        "notes": notes,
    }
def _coverage(box: list[int], others: list[list[int]]) -> float:
    """Share of a box that is covered by any of the other boxes."""
    area = float(box[2] * box[3])
    if area <= 0:
        return 0.0
    covered = 0.0
    for other in others:
        x1 = max(box[0], other[0])
        y1 = max(box[1], other[1])
        x2 = min(box[0] + box[2], other[0] + other[2])
        y2 = min(box[1] + box[3], other[1] + other[3])
        if x2 > x1 and y2 > y1:
            covered += float((x2 - x1) * (y2 - y1))
    return min(1.0, covered / area)


def _on_screen(hwnd: int) -> bool:
    """True when the window rect touches the virtual screen.

    A window parked on another virtual desktop has a rect outside every
    screen, and a screen grab of it would read the wrong pixels.
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
        return True
    return not (right <= vx or left >= vx + vw or bottom <= vy or top >= vy + vh)


def _comparable(box: list[int], other: list[int]) -> bool:
    """True when a UIA box could be the very same thing as this text.

    Chromium exposes whole-window containers as elements; a container is not
    a duplicate of one line of text, so only comparable sizes may dedup.
    """
    if other[3] > 3 * max(box[3], 8):
        return False
    return other[2] <= 4 * max(box[2], 16)


def _within_window(box: list[int], window: dict[str, Any]) -> bool:
    """True when a box lies inside the window frame.

    Chromium on a virtual desktop reports nodes far outside the visible
    rectangle; those coordinates would send an agent to the wrong place.
    """
    size = window.get("size") or [0, 0]
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        return True
    return (box[0] + box[2] > 0 and box[1] + box[3] > 0
            and box[0] < width and box[1] < height)


#: Chromium names its view shells "Pane", "ContentsView", "SideEdgeView" -
#: structure, not content.  Such names carry no information for a reader.
SHELL_NAME_SUFFIXES = ("View", "Pane", "Layer", "Surface", "Container")


