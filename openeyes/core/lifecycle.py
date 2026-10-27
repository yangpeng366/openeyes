"""应用生命周期原语 —— 窗口没了，程序也退了，怎么把它拉回来并证明它活了。

会话超时时 App 会自己关窗口；只盯着「窗口还在不在」会得到一句
「没了」，却没说清是**窗口被关**还是**整个进程退了**。这两种情况的
补救完全不同：前者要恢复窗口（不抢焦点），后者要重启程序。

本模块给出四个原语，正好对上这四件事：

* :func:`app_state`      —— 判定 App 现在处于哪个状态（running / window-gone /
  not-running），并给出证据。
* :func:`restore_window` —— 把最小化或隐藏的窗口唤回来，且**不抢焦点**
  （``SW_SHOWNOACTIVATE``），用户手上的事不被打断。
* :func:`launch_app`    —— 拉起一个 App（可执行文件、Microsoft Store 的
  AUMID、或 ``shell:AppsFolder`` 链接）。
* :func:`ensure_app`    —— 组合拳：查状态 → 需要就拉起 → **等它真的可用**，
  并按 openeyes 一贯的「动作送达 ≠ 生效」原则，只在观测到 App 真的活着
  时才报 ``ok=True``。

一切跨平台无关的部分（状态判定、超时与重试语义）都在这里；平台相关的
窗口与进程动作走 :mod:`openeyes.backends.lifecycle`。
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from openeyes.core.verify import wait_until

#: App 处于这些状态时，窗口存在且可交互。
STATE_RUNNING = "running"
#: 进程还在，但窗口被关掉或最小化到没有可交互的树。
STATE_WINDOW_GONE = "window-gone"
#: 进程本身不在了。
STATE_NOT_RUNNING = "not-running"
#: 状态查不出来（平台不支持、权限不足等）。
STATE_UNKNOWN = "unknown"

#: :func:`ensure_app` 认为「可以用了」的判据。
#: 默认是「有窗口」；调用方可以换成更严的（例如 UIA 树非空、按钮就位）。
ReadyCheck = Callable[[int], bool]


def _backend():
    """Platform backend for window / process actions.

    Kept as a function so tests can substitute a fake backend.
    """
    from openeyes.backends import lifecycle as backend
    return backend

@dataclass
class AppState:
    """App 的当前状态与判定证据。

    Attributes:
        state: 上述四个状态常量之一。
        hwnd: 找到的窗口句柄；没有则为 0。
        pid: 窗口所属进程 id；没有则为 0。
        detail: 人类可读的判定依据，便于事后审计。
        evidence: 判定过程中采集的原始事实（窗口标题、进程名等）。
    """

    state: str
    hwnd: int = 0
    pid: int = 0
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def running(self) -> bool:
        """进程是否还在（无论窗口有没有）。"""
        return self.state in (STATE_RUNNING, STATE_WINDOW_GONE)

    @property
    def usable(self) -> bool:
        """现在是否可以对这个 App 下手（有可交互的窗口）。"""
        return self.state == STATE_RUNNING

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "hwnd": self.hwnd,
            "pid": self.pid,
            "detail": self.detail,
            "evidence": self.evidence,
            "running": self.running,
            "usable": self.usable,
        }


@dataclass
class EnsureResult:
    """:func:`ensure_app` 的结果。

    ``ok`` 为真**只**表示期望状态被观测到了：App 已拉起（或本来就在），
    且通过了 :func:`ensure_app` 的 ``ready`` 判据。「进程已启动」这种
    送达性事实不算数。
    """

    ok: bool
    state: str
    action: str = "none"
    hwnd: int = 0
    pid: int = 0
    attempts: int = 0
    elapsed: float = 0.0
    error: str | None = None
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "state": self.state,
            "action": self.action,
            "hwnd": self.hwnd,
            "pid": self.pid,
            "attempts": self.attempts,
            "elapsed": self.elapsed,
            "error": self.error,
            "detail": self.detail,
            "evidence": self.evidence,
        }


# --------------------------------------------------------------------- 状态
def app_state(*, title_contains: str | None = None,
              class_name: str | None = None,
              process_name: str | None = None,
              window: int = 0) -> AppState:
    """判定 App 当前状态。

    判定顺序刻意如此：先看**进程**在不在，再看**窗口**在不在。窗口没了
    但进程还在，说明 App 只是把窗关了（会话超时常见）；两者都没了，才
    需要重新拉起。

    ``window`` 给定时直接以该窗口为准（跳过按标题/类名查找）。
    """
    backend = _backend()

    if window:
        return backend.state_for_window(window)

    process_alive = False
    process_detail = ""
    if process_name:
        process_alive, process_detail = backend.process_alive(process_name)
    # The window lookup is scoped to the same process, so a title-only
    # match cannot return another application's window and contradict the
    # process evidence.
    window_info = backend.find_window(title_contains=title_contains,
                                      class_name=class_name,
                                      process_name=process_name)

    if window_info is not None:
        hwnd = int(getattr(window_info, "hwnd", 0) or 0)
        pid = int(getattr(window_info, "pid", 0) or 0)
        minimized = bool(getattr(window_info, "minimized", False))
        # A minimised window has an empty accessibility tree, so it is not
        # usable until it is restored - but its handle is still known, so
        # restoring it (without focus) is enough.
        return AppState(
            state=STATE_WINDOW_GONE if minimized else STATE_RUNNING,
            hwnd=hwnd,
            pid=pid,
            detail=("window is minimised" if minimized else "window found"),
            evidence={
                "title": getattr(window_info, "title", ""),
                "class_name": getattr(window_info, "class_name", ""),
                "minimized": minimized,
                "process_name": process_name or "",
                "process_alive": process_alive,
                "process_detail": process_detail,
            },
        )

    if process_alive:
        return AppState(
            state=STATE_WINDOW_GONE,
            hwnd=0,
            pid=0,
            detail=process_detail or "process alive but no window",
            evidence={"process_name": process_name or "",
                      "process_alive": True},
        )

    if process_name:
        return AppState(
            state=STATE_NOT_RUNNING,
            hwnd=0,
            pid=0,
            detail=process_detail or "process not found",
            evidence={"process_name": process_name, "process_alive": False},
        )

    return AppState(
        state=STATE_UNKNOWN,
        hwnd=0,
        pid=0,
        detail="neither a window nor a process could be identified",
        evidence={"title_contains": title_contains or "",
                  "class_name": class_name or ""},
    )


# --------------------------------------------------------------------- 唤回
def restore_window(hwnd: int) -> dict:
    """把最小化/隐藏的窗口唤回来，且不抢用户焦点。

    最小化的 UWP 窗口无障碍树是空的，唤回之前读不到任何元素；本函数用
    ``SW_SHOWNOACTIVATE`` 恢复它，既填上控件树，又不夺走前台窗口。
    """
    backend = _backend()

    return backend.restore_window(hwnd)


# --------------------------------------------------------------------- 拉起
def launch_app(*, exe: str | None = None,
               aumid: str | None = None,
               args: Sequence[str] = (),
               working_dir: str | None = None) -> dict:
    """拉起一个 App。

    三种入口，按可靠性排序：

    * ``aumid`` —— Microsoft Store / MSIX 应用的
      ``FamilyName!AppId``（Any VPN 即
      ``AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App``）。
    * ``exe`` —— 可执行文件路径，附带可选参数。
    * 都没有 —— 走 ``shell:AppsFolder`` 链接。

    返回 ``{"launched": bool, "method": str, "detail": str, "pid": int}``。
    ``launched`` 只说明「启动动作已发出」，不代表 App 已经可用 —— 要证明
    它活了，用 :func:`ensure_app`。
    """
    backend = _backend()

    return backend.launch_app(exe=exe, aumid=aumid, args=tuple(args),
                               working_dir=working_dir)


# --------------------------------------------------------------------- 组合
def ensure_app(*, title_contains: str | None = None,
               class_name: str | None = None,
               process_name: str | None = None,
               exe: str | None = None,
               aumid: str | None = None,
               args: Sequence[str] = (),
               ready: ReadyCheck | None = None,
               timeout: float = 30.0,
               interval: float = 0.5,
               restore: bool = True,
               dry_run: bool = True) -> EnsureResult:
    """确保 App 处于可用状态，并**证明**它可用。

    步骤：

    1. 查状态。已经在跑且窗口可用 → 直接返回（``action="none"``）。
    2. 进程还在但窗口没了 → 先无焦点地恢复窗口；恢复不出来再拉起。
    3. 进程没了 → 拉起（``action="launched"``）。
    4. 等 App 真的可用：窗口出现，且通过 ``ready`` 判据（默认只看窗口）。

    ``dry_run=True``（默认）时只报告将要做什么，绝不拉起进程、绝不恢复
    窗口。``ready`` 判据抛异常按「还没好」处理，不会中断等待。
    """
    backend = _backend()

    started = time.monotonic()
    attempts = 0
    action = "none"
    evidence: dict[str, Any] = {}

    def current_state() -> AppState:
        return app_state(title_contains=title_contains,
                         class_name=class_name,
                         process_name=process_name)

    state = current_state()
    evidence.update(state.evidence)

    if state.usable and ready is None:
        return EnsureResult(ok=True, state=state.state, action="none",
                            hwnd=state.hwnd, pid=state.pid, attempts=attempts,
                            elapsed=time.monotonic() - started,
                            detail="app already usable",
                            evidence=evidence)

    # 补救的顺序：先「无焦点恢复」（窗口还在，只是最小化/隐藏），恢复不回来
    # 再「拉起」（进程没了，或 App 自己把窗关了）。没有句柄可恢复时，直接拉起。
    can_restore = bool(state.hwnd) and restore and not state.usable

    def usable_now() -> bool:
        nonlocal attempts, state
        attempts += 1
        state = current_state()
        evidence.update(state.evidence)
        if not state.usable:
            return False
        if ready is None:
            return True
        try:
            return bool(ready(state.hwnd))
        except Exception:
            return False

    if dry_run:
        planned = "wait" if state.usable else ("restore" if can_restore else "launch")
        detail = ("would restore the window without stealing focus, then "
                  "relaunch it if that does not help"
                  if can_restore else
                  "would launch the app and wait for it to become usable")
        return EnsureResult(ok=False, state=state.state, action=planned,
                            hwnd=state.hwnd, pid=state.pid, attempts=attempts,
                            elapsed=time.monotonic() - started,
                            detail=detail, evidence=evidence)

    if can_restore:
        action = "restore"
        restore_report = backend.restore_window(state.hwnd)
        evidence["restore"] = restore_report
        if restore_report.get("ok"):
            # 恢复后给它一点时间把控件树填回来；填回来了就不必重启
            if wait_until(usable_now, timeout=min(3.0, timeout),
                          interval=interval):
                return EnsureResult(ok=True, state=state.state, action=action,
                                    hwnd=state.hwnd, pid=state.pid,
                                    attempts=attempts,
                                    elapsed=time.monotonic() - started,
                                    detail="app is usable again after a "
                                           "non-activating restore",
                                    evidence=evidence)
        else:
            action = "launch"

    if action != "launch":
        action = "launch"
        launch_report = backend.launch_app(exe=exe, aumid=aumid,
                                           args=tuple(args))
        evidence["launch"] = launch_report
        if not launch_report.get("launched"):
            return EnsureResult(ok=False, state=state.state, action=action,
                                hwnd=0, pid=0, attempts=attempts,
                                elapsed=time.monotonic() - started,
                                error=launch_report.get("detail")
                                or "launch failed",
                                detail="launch did not start the app",
                                evidence=evidence)

    # 只有观测到 App 真的可用，才算成功。
    ok = wait_until(usable_now, timeout=timeout, interval=interval)

    return EnsureResult(
        ok=ok,
        state=state.state,
        action=action,
        hwnd=state.hwnd,
        pid=state.pid,
        attempts=attempts,
        elapsed=time.monotonic() - started,
        error=None if ok else f"app did not become usable within {timeout:.1f}s",
        detail=("app is usable" if ok else
                "app was launched but never became usable"),
        evidence=evidence,
    )



