# Round 364 - 2026-09-18T19:00:00+08:00 - gate-readiness probe

- Command: `python examples\dsh-gate-readiness.py` with the default 2.0 second timeout.
- Exit code: 2; `ready=false`.
- Result: `{"gate_url":"http://127.0.0.1:3080/","ready":false,"status_code":null,"error":"<urlopen error timed out>","next_recheck":"2026-09-10T16:30:00+08:00"}`
- Action: One read-only probe only. Do not start dsh, run section 4, or send `browser_click`. Keep the existing fixed recheck at 2026-09-30T12:00:00+08:00.
- Blocker (high): `http://127.0.0.1:3080/` timed out; wait for HTTP 200 before running `docs/dsh-web-acceptance.md` section 4.