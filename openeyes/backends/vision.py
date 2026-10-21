"""解析式视觉后端 —— 像素兜底层的「结构化」实现。

设计依据（2026 年 computer-use 共识）：截图不是入口，结构化元素才是。
若让模型直接看图点坐标，等于退回「看图点击」；本模块把像素先经 *屏幕
解析* 转成与 UIA / CDP 完全同构的 ``Element[]``，再交给同一套 selector /
hints / decision 消费。

OmniParser v2（微软）官方输出形如::

    [[[x1, y1, x2, y2], "button"], [[10, 20, 60, 40], "text"], ...]

本模块以该格式为默认输入，并支持若干常见变体（带置信度、boxes/labels
并列、逐项 dict）。调用方可覆盖 ``id2label`` 映射。

坐标语义：解析结果为 *图像像素坐标*；``Element.bbox`` 统一为 *窗口相对
坐标*，故提供 ``origin=(dx, dy)`` 偏移参数（整屏截图取 ``(0, 0)``，窗口
内截图取窗口左上角）。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from openeyes.core.schema import BBox, Center, Element, ElementState


class VisionUnavailableError(RuntimeError):
    """Raised when no screen parser is reachable or configured."""


class ScreenParser(Protocol):
    """A screen parser turns one image into structured elements."""

    def parse(self, image_path: str) -> list[Element]:  # pragma: no cover
        ...


#: OmniParser v2 label id -> label text. Callers may override it with the
#: exact mapping shipped by the checkpoint they run.
DEFAULT_ID2LABEL: dict[int, str] = {
    0: "text",
    1: "title",
    2: "icon",
    3: "image",
    4: "button",
    5: "link",
    6: "input",
    7: "checkbox",
    8: "other",
}

#: label text -> openeyes control_type (same vocabulary as UIA / CDP backends).
LABEL_TO_CONTROL_TYPE: dict[str, str] = {
    "button": "Button",
    "btn": "Button",
    "icon": "Button",
    "toggle": "Toggle",
    "link": "Hyperlink",
    "input": "Edit",
    "textbox": "Edit",
    "textfield": "Edit",
    "searchbox": "SearchBox",
    "text": "Text",
    "title": "Text",
    "label": "Text",
    "paragraph": "Text",
    "checkbox": "CheckBox",
    "radiobutton": "RadioButton",
    "combobox": "ComboBox",
    "menuitem": "MenuItem",
    "tab": "Tab",
    "image": "Image",
    "icon_img": "Image",
    "other": "Custom",
}

#: control types that accept user input. Vision output is noisier than UIA,
#: so the selector layer treats only these as clickable candidates.
INTERACTIVE_CONTROL_TYPES: frozenset[str] = frozenset({
    "Button", "Edit", "CheckBox", "RadioButton", "Hyperlink", "ComboBox",
    "SearchBox", "Toggle", "MenuItem", "Tab",
})


def _label_to_control_type(label: str) -> str:
    return LABEL_TO_CONTROL_TYPE.get((label or "").strip().casefold(), "Custom")

def _normalize_bbox(bbox: Sequence[Any], bbox_format: str) -> tuple[float, float, float, float] | None:
    """Return (x1, y1, x2, y2) or None when the box is unusable."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    try:
        x1, y1, x3, y3 = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return None
    if bbox_format == "xywh":
        return (x1, y1, x1 + x3, y1 + y3)
    return (x1, y1, x3, y3)


def _iter_detections(payload: Any) -> Iterable[tuple[Any, Any, Any]]:
    """Normalize the supported detection payload shapes into (bbox, label, score).

    Supported shapes:

    * ``[[[x1, y1, x2, y2], "button"], ...]``            (OmniParser v2 native)
    * ``[[[x1, y1, x2, y2], "button", 0.87], ...]``       (with confidence)
    * ``{"boxes": [...], "labels": [...], "scores": [...]}``
    * ``{"detections": [...]}``                           (recursed into)
    * ``[{"bbox": [...], "label": "button", "score": 0.9}]``
    """
    if payload is None:
        return
    if isinstance(payload, Mapping):
        if "detections" in payload:
            yield from _iter_detections(payload["detections"])
            return
        boxes = payload.get("boxes") or payload.get("bboxes")
        labels = payload.get("labels") or payload.get("label") or []
        scores = payload.get("scores") or payload.get("score") or []
        if isinstance(labels, str):
            labels = [labels]
        if isinstance(scores, (int, float)):
            scores = [scores]
        if boxes:
            for index, box in enumerate(boxes):
                label = labels[index] if index < len(labels) else None
                score = scores[index] if index < len(scores) else None
                yield (box, label, score)
        return
    if isinstance(payload, (list, tuple)):
        for item in payload:
            if isinstance(item, Mapping):
                box = item.get("bbox") or item.get("box")
                label = item.get("label") or item.get("name") or item.get("text")
                score = item.get("score")
                yield (box, label, score)
                continue
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                box = item[0]
                label = item[1]
                score = item[2] if len(item) > 2 else None
                yield (box, label, score)


def _resolve_label(label: Any, id2label: Mapping[Any, str] | None) -> str:
    """Map a numeric label id through ``id2label``; string labels pass through."""
    if label is None:
        return ""
    if isinstance(label, str):
        return label
    table = id2label if id2label is not None else DEFAULT_ID2LABEL
    try:
        return str(table.get(int(label), ""))
    except (TypeError, ValueError):
        return ""

def elements_from_detections(
    detections: Any,
    *,
    id2label: Mapping[Any, str] | None = None,
    origin: tuple[int, int] = (0, 0),
    score: float = 0.9,
    bbox_format: str = "xyxy",
    limit: int = 500,
) -> list[Element]:
    """Convert raw screen-parsing detections into openeyes ``Element[]``.

    Args:
        detections: payload in any of the shapes listed in ``_iter_detections``.
        id2label: optional label-id -> label-text table (defaults to
            :data:`DEFAULT_ID2LABEL`).
        origin: ``(dx, dy)`` offset converting image pixels to window-relative
            coordinates.
        score: confidence assigned when a detection carries none. Vision
            elements are intentionally scored below 1.0 so the decision layer
            can prefer structured backends.
        bbox_format: ``"xyxy"`` (default) or ``"xywh"``.
        limit: maximum number of elements returned.
    """
    if bbox_format not in {"xyxy", "xywh"}:
        raise ValueError(f"unsupported bbox_format: {bbox_format!r}")

    dx, dy = int(origin[0]), int(origin[1])
    out: list[Element] = []
    for box, raw_label, raw_score in _iter_detections(detections):
        if len(out) >= limit:
            break
        norm = _normalize_bbox(box, bbox_format)
        if norm is None:
            continue
        x1, y1, x2, y2 = norm
        width = int(round(x2 - x1))
        height = int(round(y2 - y1))
        if width <= 0 or height <= 0:
            continue
        label = _resolve_label(raw_label, id2label)
        control_type = _label_to_control_type(label)
        confidence = float(raw_score) if isinstance(raw_score, (int, float)) else float(score)
        confidence = min(max(confidence, 0.0), 1.0)
        left = int(round(x1)) + dx
        top = int(round(y1)) + dy
        out.append(Element(
            backend="vision",
            control_type=control_type,
            name=label,
            bbox=BBox(left, top, width, height),
            center=Center(left + width // 2, top + height // 2),
            automation_id="",
            class_name=f"vision:{label}" if label else "vision",
            score=confidence,
            hint=None,
            interactive=control_type in INTERACTIVE_CONTROL_TYPES,
            state=ElementState(),
            parent_chain=[],
        ))
    return out

class OmniParserBackend:
    """Screen parser backed by OmniParser v2 (微软).

    Two modes:

    * **file mode** — ``detections_path`` points at a saved detection JSON.
      No model, no network; this is the offline / replay path.
    * **endpoint mode** — ``endpoint`` is an HTTP service that accepts
      ``{"image": "<base64 png>"}`` and answers with any of the supported
      detection shapes.

    Both modes funnel through :func:`elements_from_detections`, so the
    resulting elements are identical regardless of where the parse happened.
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        detections_path: str | Path | None = None,
        timeout: float = 30.0,
        id2label: Mapping[Any, str] | None = None,
        origin: tuple[int, int] = (0, 0),
        score: float = 0.9,
        bbox_format: str = "xyxy",
        limit: int = 500,
    ) -> None:
        if endpoint and detections_path:
            raise ValueError("choose either endpoint or detections_path, not both")
        self.endpoint = endpoint
        self.detections_path = Path(detections_path) if detections_path else None
        self.timeout = float(timeout)
        self.id2label = id2label
        self.origin = origin
        self.score = score
        self.bbox_format = bbox_format
        self.limit = limit

    # -- helpers ---------------------------------------------------------

    def _to_elements(self, payload: Any) -> list[Element]:
        return elements_from_detections(
            payload,
            id2label=self.id2label,
            origin=self.origin,
            score=self.score,
            bbox_format=self.bbox_format,
            limit=self.limit,
        )

    def _post_json(self, url: str, body: dict) -> Any:
        import urllib.request

        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    # -- public API ------------------------------------------------------

    def parse_file(self, path: str | Path) -> list[Element]:
        """Parse a saved detection JSON (no model required)."""
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return self._to_elements(raw)

    def parse(self, image_path: str) -> list[Element]:
        """Parse one image into structured elements."""
        if self.detections_path is not None:
            return self.parse_file(self.detections_path)
        if not self.endpoint:
            raise VisionUnavailableError(
                "OmniParserBackend needs either endpoint= or detections_path="
            )
        image_bytes = Path(image_path).read_bytes()
        body = {"image": base64.b64encode(image_bytes).decode("ascii")}
        try:
            payload = self._post_json(self.endpoint, body)
        except Exception as exc:  # network / HTTP / JSON failures
            raise VisionUnavailableError(
                f"vision endpoint {self.endpoint!r} failed: {exc}"
            ) from exc
        return self._to_elements(payload)


def detect_elements(
    image_path: str,
    *,
    parser: ScreenParser | None = None,
    **kwargs: Any,
) -> list[Element]:
    """Convenience wrapper: parse one image into ``Element[]``."""
    if parser is None:
        parser = OmniParserBackend(**kwargs)
    return parser.parse(image_path)