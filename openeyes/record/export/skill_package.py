"""Package a recording as a self-contained, loadable skill directory.

:func:`to_codex_skill` renders the prompt-facing ``SKILL.md``. A skill that can
also be *executed* needs the recording it describes plus a body that names the
exact replay entry point, so this module writes::

    <skill-dir>/
        SKILL.md          # front-matter + step table + "Run it"
        trajectory.jsonl  # the source recording, byte-for-byte

The copy is deliberately raw: the trajectory is never re-serialised, so floats,
timestamps, unknown fields and non-ASCII text reach the replayer unchanged.
Nothing here changes :func:`to_codex_skill`; the run section is inserted into
its rendered text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..event import Trajectory
from .codex_skill import to_codex_skill

SKILL_FILENAME = "SKILL.md"
TRAJECTORY_FILENAME = "trajectory.jsonl"

# The front-matter line to_codex_skill() emits, e.g.  name: "openeyes-record-x"
_NAME_RE = re.compile(r'^name:\s*"?(.*?)"?\s*$', re.MULTILINE)

# Where the run section goes: just ahead of the closing "Notes" section.
_NOTES_MARKER = "## Notes\n"


class SkillExportError(RuntimeError):
    """Raised when the target skill directory cannot be used as requested."""


@dataclass
class SkillExport:
    """What one export produced, in the order a caller reads it."""

    skill_dir: str
    skill_md: str
    trajectory: str
    steps: int
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_dir": self.skill_dir,
            "skill_md": self.skill_md,
            "trajectory": self.trajectory,
            "steps": self.steps,
            "name": self.name,
        }


def export_skill_dir(
    trajectory_path: str | Path,
    skill_dir: str | Path,
    *,
    description: str = "",
    model_hint: str = "",
    force: bool = False,
) -> SkillExport:
    """Write ``SKILL.md`` + ``trajectory.jsonl`` into `skill_dir`.

    `force` overwrites those two files in an already populated directory; it
    never deletes anything else. Every failure is raised as a
    :class:`SkillExportError` with the offending path in the message, and
    nothing is written before the directory has been accepted.
    """
    source = Path(trajectory_path).expanduser()
    target = Path(skill_dir).expanduser()

    # Read first: a broken trajectory must fail before anything is written.
    try:
        trajectory = Trajectory.load(str(source))
        raw = source.read_bytes()
    except OSError as exc:
        raise SkillExportError(f"cannot read trajectory {source}: {exc}") from exc
    except ValueError as exc:
        raise SkillExportError(f"not a trajectory JSONL file: {source}: {exc}") from exc

    if target.exists() and not target.is_dir():
        raise SkillExportError(
            f"skill dir is not a directory: {target}",
        )
    if target.is_dir() and not force and any(target.iterdir()):
        raise SkillExportError(
            f"skill dir is not empty: {target} "
            f"(pass --force to overwrite {SKILL_FILENAME} and {TRAJECTORY_FILENAME})",
        )

    target.mkdir(parents=True, exist_ok=True)
    resolved = target.resolve()

    trajectory_file = resolved / TRAJECTORY_FILENAME
    trajectory_file.write_bytes(raw)

    skill_text = to_codex_skill(trajectory, description=description, model_hint=model_hint)
    body = _with_run_section(skill_text, resolved)
    skill_md = resolved / SKILL_FILENAME
    skill_md.write_text(body, encoding="utf-8", newline="\n")

    match = _NAME_RE.search(body)
    return SkillExport(
        skill_dir=str(resolved),
        skill_md=str(skill_md),
        trajectory=str(trajectory_file),
        steps=len(trajectory.steps),
        name=match.group(1) if match else trajectory.name,
    )


def _with_run_section(skill_text: str, skill_dir: Path) -> str:
    """Insert the execution entry point above the closing ``## Notes``."""
    section = _render_run_section(skill_dir)
    if _NOTES_MARKER in skill_text:
        head, _, tail = skill_text.partition(_NOTES_MARKER)
        return head + section + "\n" + _NOTES_MARKER + tail
    return skill_text.rstrip("\n") + "\n\n" + section


def _render_run_section(skill_dir: Path) -> str:
    """The part of SKILL.md that makes the export runnable as-is."""
    trajectory = skill_dir / TRAJECTORY_FILENAME
    lines = [
        "## Run it",
        "",
        "The recording this skill describes ships next to this file as",
        f"`{TRAJECTORY_FILENAME}`, so the folder is self-contained.",
        "",
        "Preview the replay plan first. `eyes record replay` is a **dry-run**:",
        "it plans every step and never sends input.",
        "",
        "```powershell",
        f'eyes record replay "{trajectory}" --json',
        "```",
        "",
        "To drive the live application, replay the same file through the",
        "Python API after reviewing that plan:",
        "",
        "```python",
        "from openeyes.record import Trajectory",
        "from openeyes.record.replayer import Replayer",
        "",
        f'report = Replayer(dry_run=False).replay(Trajectory.load(r"{trajectory}"))',
        'print(report.to_dict()["summary"])',
        "```",
        "",
    ]
    return "\n".join(lines)
