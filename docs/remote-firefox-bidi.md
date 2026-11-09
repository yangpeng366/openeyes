# Remote Firefox over SSH (WebDriver BiDi)

Drive a Firefox that lives on another machine - typically an Ubuntu helper
box reached over SSH - from an OpenEyes control machine. The browser page
is a real, visible, logged-in session; the agent only reads its DOM.

```
control machine (Windows/Linux)              remote box (Ubuntu)
┌──────────────────────────────┐             ┌─────────────────────────────┐
│ eyes browser scan --protocol │  ssh -L     │ firefox --profile <dedup>  │
│   bidi --port 19222 ...      │ ─────────►  │   --remote-debugging-port   │
│ openeyes.core.tunnel        │  19222 →    │            9222             │
└──────────────────────────────┘  9222      └─────────────────────────────┘
```

Both ends are loopback-only: the forward binds `127.0.0.1` on the control
machine and the Remote Agent binds `127.0.0.1` on the remote box. A debug
port is a privileged interface into a logged-in browser session, so it is
never exposed on a routable address.

---

## The three steps

### 1. Start the browser on the remote box

The Remote Agent is not a separate process: the browser itself has to be
started with `--remote-debugging-port`. Use the shipped launcher, which
also picks a profile path the installed Firefox flavour allows:

```bash
# on the Ubuntu box
bash examples/remote_firefox_launch.sh --port 9222 https://example.com/login
# or, against a live instance that already runs:
bash examples/remote_firefox_launch.sh --port 9222          # reports and exits
```

The window stays **visible**. A headless instance leaves no session a human
can look at, and an SMS login wall is exactly where a human needs to watch.

### 2. Open the forward from the control machine

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 19222:127.0.0.1:9222 yp-x240
```

Or let OpenEyes own it:

```python
from openeyes.core.tunnel import SshTunnel

with SshTunnel("yp-x240", local_port=19222, remote_port=9222) as tunnel:
    ...   # the local port is open only now
```

`ExitOnForwardFailure=yes` is not optional. Without it ssh keeps running
after the forward failed to bind, and the caller stares at a local port
that never opens while the real cause scrolls past in the log.

### 3. Drive the page

With the `eyes` CLI:

```powershell
eyes browser tabs  --protocol bidi --port 19222 --host-header localhost:9222
eyes browser scan  --protocol bidi --port 19222 --host-header localhost:9222 --pretty
eyes browser click --protocol bidi --port 19222 --host-header localhost:9222 --name-contains "Resend code" --go
```

Or with the bundled example, which opens the tunnel for you:

```powershell
python examples/remote_firefox_bidi.py --ssh-host yp-x240 --scan
python examples/remote_firefox_bidi.py --ssh-host yp-x240 --click "Resend code" --go
python examples/remote_firefox_bidi.py --ssh-host yp-x240 --type 123456 --go
```

Every state-changing verb stays a dry-run until `--go` is passed.

---

## Facts that cost time

| Fact | Consequence |
|---|---|
| Firefox 129+ dropped the CDP HTTP endpoints (`/json/version` answers 404) | BiDi only: the websocket lives at `ws://host:port/session` |
| Firefox compares the request's `Host` header with the address the agent was told to listen on | Behind a forward the ports differ - pass `--host-header localhost:9222` |
| The Remote Agent exists only inside a browser started with `--remote-debugging-port` | Restarting the browser is the only way to change the port |
| `--remote-allow-hosts localhost` is required (Chromium's analogue is `--remote-allow-origins=*`) | Without it the websocket handshake is refused |
| A snap Firefox may only use a profile under `$HOME` | `$HOME/snap/firefox/common/<name>`, otherwise `$HOME/.openeyes/<name>` |
| `firefox --version` prints snapd mount warnings | Harmless; it is not the reason a launch failed |
| `DISPLAY=:0` belongs to the logged-in seat, not to an SSH session | Export it explicitly when launching over SSH |
| A login page may keep **two steps** in the DOM at once | A click can land on the hidden step's button - check `document.elementFromPoint(x, y)` before clicking |
| The BiDi session ends when the client disconnects | The browser keeps running and keeps its cookies; the next run reconnects to the same profile |

### Read the value back before you submit

A delivered keystroke is not a typed value. Login pages with one input per
digit (an SMS code split over six boxes) are the classic case: type the
code, read the six inputs back, and only then press the button.

```python
conn.evaluate("Array.from(document.querySelectorAll('.code-input')).map(i => i.value)")
```

The same discipline as everywhere else in OpenEyes: a delivered click is
not an effective click, and the proof is the business state (the URL, the
next screen, the value in the field) - not the fact that the action was
sent.

### Proving a login worked

Do not stop at "the click went through". The proof is the destination:

- the URL moved from `accounts.<tenant>` to the tenant's own domain,
- the messenger DOM is present (a known element enumerates),
- the session survives a reconnect: a new BiDi session against the same
  profile still shows the logged-in state.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `curl /json/version` returns 404 | Firefox has no CDP endpoints any more | Use BiDi (`--protocol bidi`), not CDP |
| Forward up, but no tabs | Remote Agent not running / wrong port | Start Firefox with `--remote-debugging-port <port>` |
| `403` or handshake refused | Host header does not match the remote address | `--host-header localhost:<remote-port>` |
| `ssh exited with code 255` before the forward was ready | Auth refused, host unreachable, or the port is taken | Read the stderr in the error message; the forward spec names the ports |
| Local port opens but the browser is not there | Another process already held the local port | Pick another `--local-port`; `ExitOnForwardFailure` turns this into a clear error |
| Page renders, but nothing is clickable | The forward died mid-run | Keep `ServerAliveInterval=30`; check the tunnel is still up |

---

## Files

| Path | Role |
|---|---|
| `openeyes/core/tunnel.py` | `SshTunnel`: owns the forward, fails with the cause |
| `examples/remote_firefox_bidi.py` | tunnel + scan/click/type in one command |
| `examples/remote_firefox_launch.sh` | remote side: Firefox with the Remote Agent, visible window |
