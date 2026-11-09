"""When one shared desktop is no longer enough, and the agent should leave it.

A mouse cluster shares one Windows desktop: one foreground window, one
system pointer, one human. Every step of the ladder in
``docs/isolation-policy.md`` exists to make that sharing bearable -
invoke the control, lease the pointer, draw an overlay cursor. But some
work cannot be made bearable, and the failure looks the same every
time: the agent keeps taking the foreground away from the human, or
waits forever for a turn it will never get.

This module turns that mismatch into a verdict a caller can act on and
explain. It is inert, like the rest of the agent layer: it decides, it
never moves an agent, never reads a screen and never touches a
desktop. The three triggers are the three ways the shared desktop
breaks down:

* **真并行** - two writers on one desktop is not a scheduling problem.
  The desktop has one foreground window and one system pointer, so
  parallel writers can only serialise against each other forever. That
  needs another machine, not a better scheduler.
* **语义路径大面积失效** - when most controls can only be reached by
  physically moving the pointer, the agent degenerates into a
  mouse-poker and keeps stealing the foreground. A separate session
  gives it a foreground of its own to steal.
* **打扰预算反复耗尽** - a budget that is refused over and over means
  the job cannot be done without interrupting the human. A separate
  session is cheaper than the interruption.

The verdict says *how far* to move: ``session`` for a foreground of its
own, ``vm`` when even the pointers have to be separate. It also says
*why*, in words a human can be told: every reason names the condition,
the threshold and the measurement that tripped it. A recommendation a
user cannot understand is a recommendation they will not follow.

The numbers that feed the verdict come from the rest of the agent
layer, and their meaning matters as much as their value. A budget
refusal counts as disturbance because somebody would have been
disturbed: the human would have lost the foreground, or would have
had to look at the screen. A request that was never made - an agent
with nothing to say - disturbs nobody, and counting it would turn a
quiet minute into a loud one. So the caller counts *refusals* of a
budget that was actually granted, never announcements that did not
land and never attempts against a budget of zero.

Clashes between agents are not disturbance either. The scheduler
orders the agents inside one desktop: a preemption means one agent
pushed another aside, and the user saw nothing. That is contention,
and contention is what the scheduler is there to absorb - it turns
parallel work into serial work inside the shared desktop. It becomes
a reason to leave the desktop only when the *work* demands two
agents acting at the same time, which is what ``needs_parallel``
says.

Going down again needs no second function: the same measurement, taken
once the situation has improved, returns ``none``. The ladder is
climbed and descended with the same evaluation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Stay on the shared desktop.
LEVEL_NONE = "none"
#: Move to a separate session / separate desktop: its own foreground.
LEVEL_SESSION = "session"
#: Move to a full VM: its own pointers, its own screen.
LEVEL_VM = "vm"

#: Every level this package can return, weakest first.
ALL_LEVELS = (LEVEL_NONE, LEVEL_SESSION, LEVEL_VM)

#: How much the levels differ. A vm trigger outranks a session trigger.
_LEVEL_RANK = {level: rank for rank, level in enumerate(ALL_LEVELS)}


@dataclass
class IsolationConfig:
    """The thresholds that decide when to leave the shared desktop.

    The defaults are deliberately conservative in both directions: a
    desktop is only left when the evidence is strong (half the
    semantic path is gone, or the budget keeps refusing), because
    moving to a separate environment costs real time and real
    attention - the very thing this direction tries to save.
    """

    #: 语义路径失效率 above this means the semantic path is no longer
    #: the way to work. A rate of 0.5 means half the attempts could not
    #: reach their control without the pointer.
    semantic_miss_rate_threshold: float = 0.5
    #: how many refusals of the disturbance budget within one minute
    #: mean the budget is not a budget any more. Only real refusals
    #: count: a request that was never made disturbs nobody.
    budget_exhausted_threshold: int = 3
    #: how many agents that have to act at the same time count as real
    #: parallelism - one agent cannot be parallel with itself
    parallel_min_agents: int = 2

    def __post_init__(self) -> None:
        if not 0 < self.semantic_miss_rate_threshold <= 1:
            raise ValueError(
                "semantic_miss_rate_threshold must be in (0, 1]; "
                "0 would escalate on every evaluation, including a clean one")
        if self.budget_exhausted_threshold < 1:
            raise ValueError("budget_exhausted_threshold must be at least 1")
        if self.parallel_min_agents < 1:
            raise ValueError("parallel_min_agents must be at least 1")


@dataclass
class IsolationVerdict:
    """The answer: leave the desktop or stay, and why.

    ``escalate`` says whether the environment has to change at all,
    ``level`` says how far, and ``reasons`` says what tipped it. An
    empty ``reasons`` with ``escalate`` false is the normal state: the
    agent may stay on the shared desktop.
    """

    #: must the environment change?
    escalate: bool
    #: "none" / "session" / "vm"
    level: str
    #: the conditions that tripped, in words meant to be read aloud
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """The verdict as plain data, for a JSON reply or a log line."""
        return {
            "escalate": self.escalate,
            "level": self.level,
            "reasons": list(self.reasons),
        }


def _stronger(level: str, other: str) -> str:
    """The level that goes further away from the shared desktop."""
    if _LEVEL_RANK.get(other, 0) > _LEVEL_RANK.get(level, 0):
        return other
    return level


def _reason(condition: str, threshold: str, measured: str, why: str) -> str:
    """One line a human can be told: condition, threshold, measurement."""
    return f"{condition}：阈值 {threshold}，实测 {measured}；{why}"


def evaluate_isolation(*, needs_parallel: bool = False,
                       semantic_miss_rate: float = 0.0,
                       budget_exhausted: int = 0,
                       concurrent_writers: int = 1,
                       config: IsolationConfig | None = None) -> IsolationVerdict:
    """Should this work stay on the shared desktop, and if not, where to?

    The three measurements are the three symptoms of a desktop that is
    too small for the job:

    * ``semantic_miss_rate`` - the share of attempts that could not
      reach their control without the pointer. Where it comes from is
      the caller's business; the ledger and the resolve trail both
      carry the numbers.
    * ``budget_exhausted`` - how often a disturbance budget refused an
      action in the last minute. A refusal counts because somebody
      would have been disturbed; a request that was never made does
      not, so this is a count of refusals of a budget that was
      granted - ``AttentionBudget.disturbance`` or
      ``FocusBudget.refused`` - and never a count of announcements
      that did not land.
    * ``concurrent_writers`` - how many agents want to act at the same
      time. It only counts when ``needs_parallel`` says the work is
      genuinely concurrent: two agents that happen to be awake are not
      parallel work.

    The thresholds are inclusive - a measurement that sits exactly on
    the threshold trips it. Escalation is decided by the strongest
    level that tripped: a vm condition wins over a session condition,
    and every condition that tripped is kept in ``reasons``.

    Nothing here acts. The caller decides what to do with the verdict,
    and can put the reasons in front of a human as they are.
    """
    cfg = config if config is not None else IsolationConfig()
    if not 0.0 <= semantic_miss_rate <= 1.0:
        raise ValueError("semantic_miss_rate must be between 0.0 and 1.0")
    if budget_exhausted < 0:
        raise ValueError("budget_exhausted may not be negative")
    if concurrent_writers < 0:
        raise ValueError("concurrent_writers may not be negative")

    reasons: list[str] = []
    level = LEVEL_NONE
    escalate = False

    if needs_parallel and concurrent_writers >= cfg.parallel_min_agents:
        escalate = True
        level = LEVEL_VM
        reasons.append(_reason(
            condition="真并行",
            threshold=f"concurrent_writers >= {cfg.parallel_min_agents}",
            measured=f"concurrent_writers={concurrent_writers}（needs_parallel=True）",
            why="同一桌面只有一个前台窗口和一根系统指针",
        ))

    if semantic_miss_rate >= cfg.semantic_miss_rate_threshold:
        escalate = True
        level = _stronger(level, LEVEL_SESSION)
        reasons.append(_reason(
            condition="语义路径失效率",
            threshold=f"{cfg.semantic_miss_rate_threshold:.2f}",
            measured=f"{semantic_miss_rate:.2f}",
            why="语义路径大面积失效，只能靠物理点击",
        ))

    if budget_exhausted >= cfg.budget_exhausted_threshold:
        escalate = True
        level = _stronger(level, LEVEL_SESSION)
        reasons.append(_reason(
            condition="打扰预算耗尽次数",
            threshold=f"{cfg.budget_exhausted_threshold} 次/分钟",
            measured=f"{budget_exhausted} 次",
            why="打扰预算反复耗尽，继续留在同桌面只会一直打扰人",
        ))

    return IsolationVerdict(escalate=escalate, level=level, reasons=reasons)
