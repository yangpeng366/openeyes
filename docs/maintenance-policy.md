# OpenEyes maintenance policy（维护策略）

> Codifies the 2026-08-28 simplified maintenance flow for the
> self-maintained `yangpeng366/openeyes` repository. Keep this doc in
> sync with any future flow decision recorded in the project row.

## TL;DR / 一句话

- **docs / tests / examples / skills** → fast-forward straight to `main`.
- **source / release / permissions / dependencies / destructive changes** → PR
  (or isolated verification) with the full test gate.

## Daily docs-only path（日常 docs-only 直推 main）

Per the 2026-08-28 row decision, the Round 95 PR handoff is **no longer a
daily threshold**. Changes restricted to the following paths are pushed
directly to `main` (no PR, no waiting on external authorization):

- `docs/**` — runbooks, patrol evidence, design notes.
- `tests/**` — new / fixed test cases (the suite must remain green; never
  drop below `65 / 65`).
- `examples/**` — runnable showcases.
- `skills/**` — the repository-local Codex skill surface, which must stay
  SHA-256 identical to the installed skill at
  `E:\AI-Portable\codex-home\skills\openeyes\SKILL.md`.
- Single-line docs edits in `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`,
  `architecture.md`, `capability-contract.md`, `dsh-web-acceptance.md`,
  and this `maintenance-policy.md` itself.

Lightweight gate before pushing a docs-only commit:

```powershell
python -m pytest tests/ -q          # must remain 65 / 65 (or higher)
git status                          # must show only the intended file(s)
git diff --check                    # must be clean
# first three bytes of any new file must NOT be EF BB BF (no BOM)
```

The intended outcome is an `origin/main` fast-forward; no PR is required
for docs-only changes.

## Risky-change path（风险变更走 PR）

The following classes of change **must** go through a PR or isolated
verification before reaching `main`, regardless of the relaxed docs-only
rule:

- Source code under `openeyes/**` — new primitives, breaking refactors,
  backend changes.
- Release artifacts — tagged releases, GitHub Releases, dist wheels.
- Permissions, credentials, or `.codex/` / `.codex-plugin/` plumbing.
- Dependency changes in `pyproject.toml` / lock files.
- Anything destructive — deleting tracked files, rewriting git history,
  force pushes, deleting remote refs / branches / tags.

For these, open a PR from a feature branch, run the full test gate, and
request an explicit user decision before the PR is merged or the side
effect is taken.

## Patrol evidence convention（巡检证据落点）

Patrol rounds append their evidence to the bottom of
`docs/dsh-web-acceptance.md` under a clearly named `## Patrol evidence —`
or `## Round N patrol evidence` header. The acceptance runbook itself
occupies the top of that file (sections 1–4 + Pass criteria). If a future
flow change redirects the evidence log, update both this policy doc and
the runbook header so the convention is self-documenting.

### Deferred live-acceptance cadence

When live acceptance is deferred solely because `127.0.0.1:3080` is not
listening, a later patrol performs one TCP listener check instead of
rerunning the full probe suite. If the port remains closed and no
trigger fired, return an inspected result, preserve the recorded recheck
time, and do not append duplicate patrol evidence. Re-run the full probe
set when the recheck time arrives, when `3080` starts listening, or when
`origin/main`, the acceptance candidate, or the user decision changes.

## Superseded PR-handoff branches（已废弃的 PR handoff 分支）

The 2026-08-28 decision retired the Round 95 PR-handoff machinery as a
daily threshold. Round 97 completed the corresponding local
housekeeping: `analysis/round-95-package`,
`analysis/round-96-pr-handoff`, and `analysis/round-93-candidate` no
longer exist as local refs. Their commits remain recoverable from the
standard reflog window. Remote refs were intentionally left untouched
because remote ref deletion remains a destructive action.

If the same stale handoff branches reappear, remove the local refs only
after confirming that their commits are subsumed by `main` or safely
recoverable. Keep remote branches, tags, and history rewrites on the
risky-change path.

## Provenance / 决策来源

- **2026-08-28** — Feishu project row decision: docs-only updates
  fast-forward `main`; source / release / permissions / dependencies /
  destructive changes still go through PR or isolated verification; the
  Round 95 PR handoff is no longer a daily threshold.
- **2026-08-28 baseline** — `origin/main = aa608b3427ae226953709f0f2fa384e14e9fdf17`,
  `pytest tests/ = 65 / 65`.
