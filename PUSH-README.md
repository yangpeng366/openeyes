# Push to GitHub — manual steps

## Current state

- Local commit `534a303` (29 files, 1984 insertions) is ready.
- Remote configured: `ssh://git@ssh.github.com:443/yangpeng366/openeyes.git` (uses SSH port 443 to bypass GFW; PAT and HTTPS not needed)
- SSH key already configured: `C:/Users/47037/.ssh/id_ed25519_yp_x240` (authenticates as `yangpeng366`)

## What's blocked right now

The Edge browser + `git credential fill` + curl all fail to reach
`api.github.com` because:
1. Direct HTTPS to github.com is blocked by GFW
2. mihomo on 127.0.0.1:7890 is up but WARP is blocked (see today's notes in
   `memory/2026-08-11.md`), so it cant proxy GitHub either
3. ghproxy.com (the github mirror in pip/git config) is also timing out

SSH port 443 to ssh.github.com works, but only AFTER the repo exists on
GitHub. So the only missing step is creating the empty repo on GitHub.

## Option A: User creates the repo in browser (30 seconds)

When you have any working GitHub access (e.g. via VPN / mobile hotspot /
mihomo with a working node):

1. Open https://github.com/new
2. Repository name: `openeyes`
3. Description: "OpenEyes - AI-friendly computer-use primitives (capture/detect/click) CLI + MCP server. Cross-platform via platform accessibility tree. UIA-first (free, no ML), pluggable vision backend, Vimium-style grid fallback. MIT."
4. Public
5. **Do not** initialize with README / .gitignore / license (we have local content)
6. Click "Create repository"

Then I (Codex) push via:

```powershell
cd E:\gitAll\openeyes
git push -u origin main
```

## Option B: Wait for network to recover

If mihomo WARP gets fixed, the auto-deploy skill (`D:\gitAll\auto-deploy\github-do.js`) can create the repo automatically; I run it then push.

## Option C: Skip push for now

The local commit is safe. The repo is reproducible from `02_项目推进/openeyes/DESIGN.md` + these 29 files. We can push later.