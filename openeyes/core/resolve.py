"""分级解析链 —— 把「结构化优先于截图」落成可执行代码。

2026 年 computer-use 的共识是：能走 API / CLI / DOM 就别截图，截图只当
最后手段。openEyes 早已具备三种结构化出口（UIA 无障碍树、CDP DOM、
Lightpanda CDP），本模块把它们串成一条 *可降级* 的解析链：

    uia  ->  cdp  ->  vision

每一级失败都留痕（attempts），调用方既拿到元素，也知道「为什么退到了
下一级」——这比直接看图点坐标可审计得多，也便于上层决策层降权。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from openeyes.core.schema import Element

#: Backend preference order. Structured backends first, pixels last.
DEFAULT_ORDER: tuple[str, ...] = ("uia", "cdp", "vision")


@dataclass
class Attempt:
    """One rung of the chain, and why it did or did not answer."""

    backend: str
    ok: bool
    count: int = 0
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "ok": self.ok,
            "count": self.count,
            "reason": self.reason,
        }


@dataclass
class ResolveResult:
    """Elements plus the trail of how they were obtained."""

    elements: list[Element] = field(default_factory=list)
    used_backend: str | None = None
    attempts: list[Attempt] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "used_backend": self.used_backend,
            "attempts": [a.to_dict() for a in self.attempts],
            "elements": [e.to_dict() for e in self.elements],
        }

def _try_uia(hwnd: int, max_depth: int) -> list[Element]:
    from openeyes.core.selector import detect_elements as uia_detect

    return uia_detect(hwnd, restore=True, max_depth=max_depth)


def _try_cdp(port: int, url_contains: str | None) -> list[Element]:
    from openeyes.backends import cdp

    conn = cdp.connect(port=port, url_contains=url_contains)
    return cdp.scan_dom(conn)


def _try_vision(
    image_path: str | None,
    vision_parser: Callable[[str], Sequence[Element]] | None,
    vision_elements: Sequence[Element] | None,
) -> list[Element]:
    if vision_elements is not None:
        return list(vision_elements)
    if image_path and vision_parser is not None:
        return list(vision_parser(image_path))
    raise LookupError("no vision source (need image_path + vision_parser, or vision_elements)")


def resolve_elements(
    *,
    hwnd: int | None = None,
    port: int | None = 9222,
    url_contains: str | None = None,
    image_path: str | None = None,
    vision_parser: Callable[[str], Sequence[Element]] | None = None,
    vision_elements: Sequence[Element] | None = None,
    order: Sequence[str] = DEFAULT_ORDER,
    max_depth: int = 12,
) -> ResolveResult:
    """Walk the backend chain and return the first non-empty element set.

    A rung is skipped when its required input is missing (no ``hwnd`` for
    UIA, no browser for CDP, no image for vision) and when it raises. Every
    rung leaves an :class:`Attempt` behind, so the caller can explain why
    the result came from the backend it did.
    """
    result = ResolveResult()
    for backend in order:
        try:
            if backend == "uia":
                if not hwnd:
                    raise LookupError("no hwnd given for uia")
                elements = _try_uia(hwnd, max_depth)
            elif backend == "cdp":
                if not port:
                    raise LookupError("no port given for cdp")
                elements = _try_cdp(port, url_contains)
            elif backend == "vision":
                elements = _try_vision(image_path, vision_parser, vision_elements)
            else:
                raise LookupError(f"unknown backend {backend!r}")
        except Exception as exc:
            result.attempts.append(Attempt(backend, ok=False, reason=str(exc) or exc.__class__.__name__))
            continue

        if not elements:
            result.attempts.append(Attempt(backend, ok=True, count=0, reason="no elements"))
            continue

        result.attempts.append(Attempt(backend, ok=True, count=len(elements)))
        result.elements = list(elements)
        result.used_backend = backend
        return result

    return result