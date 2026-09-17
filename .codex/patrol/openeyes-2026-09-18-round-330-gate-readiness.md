# OpenEyes auto-patrol — round-330 — gate-readiness probe

- 时间: 2026-09-18T06:20:56+08:00 Asia/Shanghai
- 项目: OpenEyes
- 仓库: `E:\gitAll\openeyes`
- 模式: codex-auto-patrol-loop stdintalk NEW #1（2026-09-11T10:00:00+08:00 触发条件已满足，沿 round-285..329 续行）

## 探测

- 命令: `python examples\dsh-gate-readiness.py --next-recheck 2026-09-30T12:00:00+08:00`（沿用脚本默认 `--timeout 2.0`；显式 `--next-recheck` 与 round-291 设定的 FIXED recheck 对齐，沿用 round-329 已恢复的覆盖）
- 退出码: 2（脚本内部 `sys.exit(2)` 标记非 ready；PowerShell 包裹层 echo `EXIT=2`）
- 结果:
  ```json
  {"gate_url": "http://127.0.0.1:3080/", "ready": false, "status_code": null, "error": "<urlopen error timed out>", "next_recheck": "2026-09-30T12:00:00+08:00", "next_action": "Wait for the dsh web host to return HTTP 200, then rerun this probe."}
  ```
- 本轮采用脚本默认 `--timeout 2.0`：TCP SYN 无回包，与 round-289 / round-290 / round-292..329 的 timeout 同源；与 round-291 的 WinError 10061 积极拒绝互见，源相同：3080 端口监听缺位 + AnyVPN 路由抖动。
- 按 stdintalk NEW 触发条件，本轮**仅**执行一次只读探测即止，不做早复探。

## 处置

- 沿用 round-231..329 结论：不启 dsh、不跑 section 4、不发 browser_click。
- 本轮按 NEW 触发条件（>= 2026-09-11T10:00:00+08:00）跑一次只读探测即止，不做早复探；下一窗口维持 round-291 设定的 FIXED 2026-09-30T12:00:00+08:00（round-292..329 已延续该窗口）。
- 仓库工作区未提交改动（`CHANGELOG.md` / `README.md` / `openeyes/__init__.py` / `pyproject.toml` / `tests/test_smoke.py` 修订 + `docs/RECORD_DESIGN.md` / `openeyes/record/` / `tests/test_record.py` / `examples/record_*.py` / `examples/out/` / `examples/record-out/` 新增）维持「与本轮 gate-readiness 无关、本轮不携入」，留待后续人工评审后再处理。
- 本轮 commit 仅含 round-330 单一证据（round-329 已提交于 7380255，无积压）。

## Blocker

- high: http://127.0.0.1:3080/ 持续不可连接（与 round-249 / round-251 / round-254 / round-255 / round-256 / round-257 / round-264 / round-265 / round-266 / round-268 / round-269 / round-273 / round-274 / round-275 / round-276 / round-277 / round-278 / round-279 / round-280 / round-281 / round-282 / round-283 / round-284 / round-285 / round-286 / round-287 / round-288 / round-290 / round-291 同源 WinError 10061；与 round-250 / round-252 / round-253 / round-259 / round-260 / round-262 / round-263 / round-267 / round-270 / round-271 / round-272 / round-289 / round-292 / round-293 / round-294 / round-295 / round-296 / round-297 / round-298 / round-299 / round-300 / round-301 / round-302 / round-303 / round-304 / round-305 / round-306 / round-307 / round-308 / round-309 / round-310 / round-311 / round-312 / round-313 / round-314 / round-315 / round-316 / round-317 / round-318 / round-319 / round-320 / round-321 / round-322 / round-323 / round-324 / round-325 / round-326 / round-327 / round-328 / round-329 timed out 互见，源相同：3080 端口监听缺位 + AnyVPN 路由抖动）。
- 建议: 复探间隔 >= 7 天，避免短期内重复触发只读探测。