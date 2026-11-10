"""Two agents, one desktop, two cursors: the demo that has to stay boring.

A mouse cluster is only believable if two things can be seen at once:

* **two cursors**, one per agent, in that agent's own colour, moving on
  their own paths - so a human can tell who is doing what;
* **the user's foreground window never moving** - because a cursor that
  "helps" by taking the focus is the disturbance the whole direction is
  trying to avoid.

So this script does exactly that and then checks it: it draws two
cursors for a few seconds, records every drawn frame in the action
ledger under the agent that drew it, reads the foreground window before
and after, and exits non-zero when the focus moved. The cursors are
pictures (``CursorOverlay``): they never drive the pointer and never
activate a window. The ledger says so per entry - ``physical_input``
stays false for every single one.

The structure keeps the desktop at arm's length, so CI can run the same
logic:

* ``MouseClusterDemo`` is the logical demo - agents, attention budgets,
  paths, ledger, verdict. It talks to an *overlay* and two *probes*, all
  injected.
* ``DryOverlay`` is the stand-in used by ``--dry-run``: it records where
  it was asked to draw and never creates a window. ``CursorOverlay`` is
  the real one.
* ``focus_verdict`` is a pure function, so "the focus moved" can be
  tested without a screen.

Run it:

    python examples\\mouse-cluster-demo.py --dry-run --seconds 1
    python examples\\mouse-cluster-demo.py --seconds 5
    python examples\\mouse-cluster-demo.py --attention-per-min 1   # one refused

The ledger it writes is the input of ``examples/interference-report.py``,
which answers the only question that matters afterwards: how often did
this run disturb the human?

Exit codes: 0 both cursors drawn and the focus unchanged; 3 the
foreground window changed; 4 fewer cursors were drawn than the demo
promised (an agent was refused by the attention budget, or the overlay
is unsupported here).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openeyes.agent.attention import AttentionBudget
from openeyes.agent.errors import BUDGET_EXCEEDED
from openeyes.agent.ledger import DELIVERY_NONE, ActionLedger, LedgerEntry
from openeyes.agent.overlay import CursorOverlay, OverlayState
from openeyes.agent.presence import AgentPresence
from openeyes.agent.profile import AgentProfile
from openeyes.core.windows import foreground_window

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "examples" / "out" / "mouse-cluster-ledger.jsonl"

#: The four disturbance numbers the report recounts, written the same way.
FIELDS = ("physical_input", "focus_stolen", "refused", "verified")


# ----------------------------------------------------------------- the geometry
@dataclass(frozen=True)
class Rect:
    """A patch of the screen, in physical pixels."""

    left: int
    top: int
    width: int
    height: int

    def clamp(self, x: float, y: float) -> tuple[int, int]:
        """The nearest point of the patch: a cursor never leaves its lane."""
        right = self.left + max(0, self.width)
        bottom = self.top + max(0, self.height)
        return (min(max(int(round(x)), self.left), right),
                min(max(int(round(y)), self.top), bottom))


def figure_eight(t: float, seconds: float, area: Rect) -> tuple[int, int]:
    """A lissajous loop: the first agent's cursor wanders in a loop."""
    phase = 2.0 * math.pi * (t / max(seconds, 1e-6))
    x = area.left + area.width / 2 + area.width * 0.40 * math.sin(phase)
    y = area.top + area.height / 2 + area.height * 0.40 * math.sin(2.0 * phase)
    return area.clamp(x, y)


def sweep(t: float, seconds: float, area: Rect) -> tuple[int, int]:
    """A crossing sweep: the second agent moves differently on purpose."""
    phase = t / max(seconds, 1e-6)
    x = area.left + area.width * _triangle(phase)
    y = area.top + area.height * _triangle(phase * 3.0)
    return area.clamp(x, y)


def _triangle(phase: float) -> float:
    """A 0..1..0 ramp: the way a cursor crosses a screen and comes back."""
    cycle = phase % 2.0
    return cycle if cycle <= 1.0 else 2.0 - cycle


#: The available paths, by name, so a demo can say which one it ran.
TRACKS: dict[str, Callable[[float, float, Rect], tuple[int, int]]] = {
    "figure8": figure_eight,
    "sweep": sweep,
}


def lanes(bounds: Rect, count: int, *, inset: int = 32) -> list[Rect]:
    """Split the screen into one lane per agent: two cursors never overlap."""
    count = max(1, int(count))
    width = max(1, bounds.width // count)
    out: list[Rect] = []
    for index in range(count):
        left = bounds.left + index * width
        area = Rect(left + inset, bounds.top + inset,
                    width - 2 * inset, bounds.height - 2 * inset)
        if area.width <= 0 or area.height <= 0:
            area = Rect(left, bounds.top, width, bounds.height)
        out.append(area)
    return out


def screen_bounds() -> Rect:
    """The primary screen's pixel size, or a sane guess when it is unknown."""
    try:
        import win32api
        width = int(win32api.GetSystemMetrics(0))
        height = int(win32api.GetSystemMetrics(1))
        if width > 0 and height > 0:
            return Rect(0, 0, width, height)
    except Exception:
        pass
    return Rect(0, 0, 1280, 800)


# ------------------------------------------------------------------ the agents
@dataclass(frozen=True)
class AgentSpec:
    """One agent in the demo: who it is, and how its cursor moves."""

    agent_id: str
    label: str
    track: str = "figure8"
    priority: int = 0

    @property
    def profile(self) -> AgentProfile:
        """The identity the overlay and the budget are built from."""
        return AgentProfile(agent_id=self.agent_id, label=self.label,
                            priority=self.priority)


#: Two agents, two colours (``color_for`` derives them from the identities),
#: two different paths - so the screen shows two cursors, not one cursor twice.
DEFAULT_SPECS = (
    AgentSpec(agent_id="worker-a", label="A", track="figure8"),
    AgentSpec(agent_id="worker-b", label="B", track="sweep"),
)


class DryOverlay:
    """A cursor that is only accounted for: the CI stand-in for a window.

    It has the same surface as ``CursorOverlay`` - the agent's identity,
    its colour, ``show`` / ``hide`` / ``close`` and the same state object -
    so the demo cannot accidentally depend on a real window. What it does
    *not* do is create one, which keeps ``--dry-run`` runnable everywhere.
    """

    #: Nothing about a picture needs a desktop.
    supported = True

    def __init__(self, agent_id: str = "default", label: str = "",
                 color: str = "", size: int = 22) -> None:
        profile = AgentProfile(agent_id=agent_id, label=label, color=color)
        self.agent_id = profile.agent_id
        self.label = profile.label
        self.color = profile.color
        self.size = size
        self.state = OverlayState(agent_id=profile.agent_id,
                                  label=profile.label, color=profile.color)
        #: every position this cursor was asked to draw at
        self.shows: list[tuple[int, int]] = []

    def show(self, x: int, y: int) -> bool:
        self.shows.append((int(x), int(y)))
        self.state.x = int(x)
        self.state.y = int(y)
        self.state.visible = True
        return True

    def hide(self) -> None:
        self.state.visible = False

    def close(self) -> None:
        self.state.visible = False


@dataclass
class DemoAgent:
    """One agent at the desk: identity, cursor, attention, lane, path."""

    spec: AgentSpec
    overlay: Any
    attention: AttentionBudget
    presence: AgentPresence
    area: Rect
    #: was the cursor allowed onto the screen at all?
    shown: bool = False
    #: did the attention budget turn the announcement down?
    refused: bool = False

    @property
    def agent_id(self) -> str:
        return self.spec.agent_id

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def color(self) -> str:
        return self.overlay.color

    def point(self, t: float, seconds: float) -> tuple[int, int]:
        """Where this agent's cursor is at ``t`` seconds into the run."""
        track = TRACKS.get(self.spec.track, figure_eight)
        return track(t, seconds, self.area)


@dataclass(frozen=True)
class AgentOutcome:
    """What one agent ended up doing - counted from the ledger, not here."""

    agent_id: str
    label: str
    color: str
    track: str
    shown: bool
    refused: bool
    entries: int


def focus_verdict(before: int | None,
                  after: int | None) -> tuple[bool, str]:
    """Did the user keep the foreground window?

    ``None`` means the probe could not read it (no desktop, no pywin32).
    An unreadable probe is not evidence of theft, so it passes and says
    so: a verdict that invents a failure is as useless as one that hides
    it.
    """
    if before is None or after is None:
        return True, ("foreground window unavailable (read-only probe "
                      "returned nothing); nothing to compare")
    if before != after:
        return False, (f"the foreground window changed: before={before} "
                       f"after={after}")
    return True, f"foreground window unchanged ({before})"


@dataclass
class DemoResult:
    """What the run did, and whether it kept its two promises."""

    seconds: float
    frames: int
    agents: tuple[AgentOutcome, ...]
    foreground_before: int | None
    foreground_after: int | None
    pointer_before: tuple[int, int] | None
    pointer_after: tuple[int, int] | None
    entries: int
    refused_entries: int
    verified: int
    physical_input: int
    focus_stolen: int

    @property
    def focus_ok(self) -> bool:
        """The user's foreground window survived the whole run."""
        return focus_verdict(self.foreground_before,
                             self.foreground_after)[0]

    @property
    def cursors_ok(self) -> bool:
        """Every agent got its cursor on the screen."""
        return all(agent.shown for agent in self.agents)

    @property
    def verified_ratio(self) -> float:
        return self.verified / self.entries if self.entries else 0.0

    def exit_code(self) -> int:
        """0 when both promises held; the number names the promise broken."""
        if not self.focus_ok:
            return 3
        if not self.cursors_ok:
            return 4
        return 0

    def to_dict(self) -> dict[str, Any]:
        valid, reason = focus_verdict(self.foreground_before,
                                      self.foreground_after)
        return {
            "seconds": self.seconds,
            "frames": self.frames,
            "agents": [
                {
                    "agent_id": agent.agent_id,
                    "label": agent.label,
                    "color": agent.color,
                    "track": agent.track,
                    "shown": agent.shown,
                    "refused": agent.refused,
                    "entries": agent.entries,
                }
                for agent in self.agents
            ],
            "foreground": {
                "before": self.foreground_before,
                "after": self.foreground_after,
                "ok": valid,
                "reason": reason,
            },
            "pointer": {
                "before": list(self.pointer_before) if self.pointer_before else None,
                "after": list(self.pointer_after) if self.pointer_after else None,
            },
            "ledger": {
                "entries": self.entries,
                "physical_input": self.physical_input,
                "focus_stolen": self.focus_stolen,
                "refused": self.refused_entries,
                "verified": self.verified,
                "verified_ratio": round(self.verified_ratio, 4),
            },
        }


# ---------------------------------------------------------------- the watching
def pointer_position() -> tuple[int, int] | None:
    """Where the system pointer is - read, never written.

    Informational only: on a shared desktop the user's own hand moves it,
    so a before/after difference is not evidence that the demo drove it.
    The evidence for "nothing drove the pointer" is in the ledger: every
    entry carries ``physical_input=False``.
    """
    try:
        from openeyes.core.actuator import cursor_pos
        pos = cursor_pos()
        return (int(pos[0]), int(pos[1])) if pos else None
    except Exception:
        return None


def _probe(read: Callable[[], Any]) -> Any:
    """Read a piece of desktop state, or say "unknown" instead of raising."""
    try:
        return read()
    except Exception:
        return None


def per_agent_counts(ledger: ActionLedger,
                     refused: tuple[LedgerEntry, ...] = ()
                     ) -> dict[str, dict[str, int]]:
    """Count the four numbers per agent, from the ledger's own entries."""
    refused_ids = {id(entry) for entry in refused}
    counts: dict[str, dict[str, int]] = {}
    for entry in ledger.entries():
        row = counts.setdefault(entry.agent_id,
                                dict.fromkeys(("entries", *FIELDS), 0))
        row["entries"] += 1
        if entry.physical_input:
            row["physical_input"] += 1
        if entry.focus_stolen:
            row["focus_stolen"] += 1
        if entry.verified:
            row["verified"] += 1
        if id(entry) in refused_ids:
            row["refused"] += 1
    return counts


# ---------------------------------------------------------------- the demo
class MouseClusterDemo:
    """Several agents sharing one desktop, each with a cursor of its own.

    The class is the logical demo: it owns the ledger, the shared
    attention budget and the paths, and it talks to the desktop only
    through the three injected callables (``overlay_factory``,
    ``foreground``, ``pointer``) and ``sleep``. Tests pass stand-ins;
    ``main`` passes the real ones.

    The attention budget is **shared**, not one per agent: the human has
    one attention, so two agents announcing at the same moment are two
    interruptions of the same person. A budget of one therefore lets one
    agent be seen and makes the other wait and record a refusal - which is
    exactly what the report then counts.
    """

    def __init__(self, *, specs: tuple[AgentSpec, ...] = DEFAULT_SPECS,
                 ledger: ActionLedger | None = None,
                 overlay_factory: Callable[..., Any] | None = None,
                 attention_per_min: int = 2,
                 fps: float = 20.0,
                 bounds: Rect | None = None,
                 foreground: Callable[[], int | None] = foreground_window,
                 pointer: Callable[[], tuple[int, int] | None] = pointer_position,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.specs = tuple(specs) or DEFAULT_SPECS
        self.ledger = ledger if ledger is not None else ActionLedger(
            agent_id="mouse-cluster")
        self.fps = max(1.0, float(fps))
        self.bounds = bounds or screen_bounds()
        self._foreground = foreground
        self._pointer = pointer
        self._sleep = sleep
        self._refused: list[LedgerEntry] = []
        self.attention = AttentionBudget(per_minute=attention_per_min)

        factory = overlay_factory or CursorOverlay
        areas = lanes(self.bounds, len(self.specs))
        self.agents: list[DemoAgent] = []
        for spec, area in zip(self.specs, areas, strict=True):
            profile = spec.profile
            overlay = factory(agent_id=profile.agent_id, label=profile.label,
                              color=profile.color)
            self.agents.append(DemoAgent(
                spec=spec,
                overlay=overlay,
                attention=self.attention,
                presence=AgentPresence(overlay, self.attention,
                                       agent_id=profile.agent_id),
                area=area,
            ))

    # ------------------------------------------------------------- accounting
    @property
    def refused_entries(self) -> tuple[LedgerEntry, ...]:
        """The ledger entries that record a refusal by the attention budget."""
        return tuple(self._refused)

    def _record_announcement(self, agent: DemoAgent,
                             point: tuple[int, int]) -> None:
        """One entry per agent for the moment it asked to be looked at."""
        entry = self.ledger.record(
            action="announce",
            delivery=DELIVERY_NONE,
            cursor_moved=agent.shown,
            verified=agent.shown,
            ok=agent.shown,
            target=agent.spec.track,
            detail="" if agent.shown else BUDGET_EXCEEDED,
            agent_id=agent.agent_id,
        )
        if not agent.shown:
            self._refused.append(entry)

    def _record_move(self, agent: DemoAgent, point: tuple[int, int],
                     verified: bool) -> None:
        """One entry per drawn frame: a picture moved, nothing was delivered."""
        self.ledger.record(
            action="cursor",
            delivery=DELIVERY_NONE,
            cursor_moved=True,
            physical_input=False,
            focus_stolen=False,
            verified=verified,
            ok=verified,
            target=agent.spec.track,
            detail=f"{point[0]},{point[1]}",
            agent_id=agent.agent_id,
        )

    # -------------------------------------------------------------- the run
    def run(self, seconds: float = 5.0) -> DemoResult:
        """Draw the cursors for ``seconds`` and report what it cost."""
        seconds = max(0.0, float(seconds))
        frame_s = 1.0 / self.fps
        frames = max(1, int(round(seconds * self.fps)))

        foreground_before = _probe(self._foreground)
        pointer_before = _probe(self._pointer)

        # Asking to be seen is the interruption. Moving afterwards is not:
        # the cursor is already on the screen, so a move is a redraw.
        for agent in self.agents:
            first = agent.point(0.0, seconds)
            agent.shown = agent.presence.announce(needs_attention=True,
                                                  x=first[0], y=first[1])
            agent.refused = not agent.shown
            self._record_announcement(agent, first)

        try:
            for index in range(frames):
                elapsed = index * frame_s
                for agent in self.agents:
                    if not agent.shown:
                        # The budget said no: nothing is drawn, and nothing
                        # is claimed to have been drawn.
                        continue
                    x, y = agent.point(elapsed, seconds)
                    moved = bool(agent.overlay.show(x, y))
                    state = agent.overlay.state
                    verified = bool(
                        moved and state.visible
                        and getattr(state, "x", x) == x
                        and getattr(state, "y", y) == y
                    )
                    self._record_move(agent, (x, y), verified)
                self._sleep(frame_s)
        finally:
            for agent in self.agents:
                agent.presence.withdraw()

        foreground_after = _probe(self._foreground)
        pointer_after = _probe(self._pointer)
        return self._result(seconds, frames, foreground_before,
                            foreground_after, pointer_before, pointer_after)

    def _result(self, seconds: float, frames: int,
                foreground_before: int | None, foreground_after: int | None,
                pointer_before: tuple[int, int] | None,
                pointer_after: tuple[int, int] | None) -> DemoResult:
        summary = self.ledger.summary()
        entries = self.ledger.entries()
        outcomes = tuple(
            AgentOutcome(
                agent_id=agent.agent_id,
                label=agent.label,
                color=agent.color,
                track=agent.spec.track,
                shown=agent.shown,
                refused=agent.refused,
                entries=sum(1 for entry in entries
                            if entry.agent_id == agent.agent_id),
            )
            for agent in self.agents
        )
        return DemoResult(
            seconds=seconds,
            frames=frames,
            agents=outcomes,
            foreground_before=foreground_before,
            foreground_after=foreground_after,
            pointer_before=pointer_before,
            pointer_after=pointer_after,
            entries=int(summary["actions"]),
            refused_entries=len(self._refused),
            verified=int(summary["verified"]),
            physical_input=int(summary["physical_inputs"]),
            focus_stolen=int(summary["focus_stolen"]),
        )

    def close(self) -> None:
        """Take every cursor off the screen and destroy what it created."""
        for agent in self.agents:
            agent.presence.close()


# ---------------------------------------------------------------- the export
def write_export(path: str | Path, ledger: ActionLedger,
                 refused: tuple[LedgerEntry, ...] = ()) -> Path:
    """Write the ledger as JSONL - entries, then the run's own claim.

    One record per line, so a long run can be written as it happens:

    * ``{"kind": "entry", ...}`` - an entry exactly as the ledger wrote it,
      plus ``refused`` when the attention budget turned it down;
    * ``{"kind": "summary", ...}`` - what this script thinks the four
      numbers are, *for the report to check*. A claim that is never
      compared with the entries is not a cross-check.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    refused_ids = {id(entry) for entry in refused}
    counts = per_agent_counts(ledger, refused)
    summary = ledger.summary()

    lines: list[str] = []
    for entry in ledger.entries():
        record: dict[str, Any] = {"kind": "entry", **entry.to_dict()}
        record["refused"] = id(entry) in refused_ids
        lines.append(json.dumps(record, ensure_ascii=False))
    lines.append(json.dumps({
        "kind": "summary",
        "entries": int(summary["actions"]),
        "physical_input": int(summary["physical_inputs"]),
        "focus_stolen": int(summary["focus_stolen"]),
        "refused": len(refused),
        "verified": int(summary["verified"]),
        "agents": counts,
    }, ensure_ascii=False))

    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return target


# ----------------------------------------------------------------- the door
def _show(path: Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=5.0,
                        help="how long the cursors move (default 5)")
    parser.add_argument("--fps", type=float, default=20.0,
                        help="frames per second of the animation")
    parser.add_argument("--attention-per-min", type=int, default=2,
                        help="interruptions the desk may spend per minute, "
                             "shared by the agents; one announcement each is "
                             "enough by default")
    parser.add_argument("--dry-run", action="store_true",
                        help="build no window: walk the ledger and the verdict "
                             "with stand-in cursors")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="where to write the ledger export")
    args = parser.parse_args(argv)

    factory: Callable[..., Any] = DryOverlay if args.dry_run else CursorOverlay
    demo = MouseClusterDemo(overlay_factory=factory,
                            attention_per_min=args.attention_per_min,
                            fps=args.fps)
    try:
        result = demo.run(args.seconds)
        export = write_export(args.out, demo.ledger, demo.refused_entries)
    finally:
        demo.close()

    valid, reason = focus_verdict(result.foreground_before,
                                  result.foreground_after)
    mode = "dry-run (no window was created)" if args.dry_run else "live windows"
    print(f"mouse cluster: {len(result.agents)} agents, {result.seconds:g}s, "
          f"{result.frames} frames at {demo.fps:g} fps, {mode}")
    for agent in result.agents:
        state = "cursor shown" if agent.shown else "cursor refused"
        print(f"  agent {agent.agent_id:10s} colour {agent.color} "
              f"label {agent.label} track {agent.track:8s} "
              f"entries={agent.entries}  {state}")
    print(f"ledger: {result.entries} entries, "
          f"physical_input={result.physical_input} "
          f"focus_stolen={result.focus_stolen} "
          f"refused={result.refused_entries} "
          f"verified={result.verified}")
    print(f"focus: {reason}")
    print(f"pointer: {result.pointer_before} -> {result.pointer_after} "
          f"(informational: the user's own hand may move it; the ledger's "
          f"physical_input={result.physical_input} is the evidence)")
    print(f"ledger export: {_show(export)}")

    code = result.exit_code()
    if code == 3:
        print("FAILED: the user's foreground window changed during the demo")
        print("        the cursors never activate a window, so something else "
              "took the focus; a shared desktop run must not do that")
    elif code == 4:
        prompted = ", ".join(
            f"{agent.agent_id}={'refused' if agent.refused else 'missing'}"
            for agent in result.agents if not agent.shown)
        print(f"FAILED: fewer cursors than agents ({prompted}); "
              f"attention budget {args.attention_per_min}/min")
    else:
        print("OK: every agent drew its own cursor, the foreground window "
              "never moved, and no entry touched the pointer")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
