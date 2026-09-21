## Round 594 - 2026-09-22T07:33:26+08:00
- 方向：OpenEyes Record；沉淀临时 Edge runner 为可复现 example。
- 落地：examples/record_edge_demo.js（自包含 Node 脚本，vendored bridge 到 examples/lib/）。
- 去绝对路径：Edge 路径候选内置 + EDGE_PATH 环境变量；puppeteer-core 走标准 require + PUPPETEER_MODULE_PATH 回退。
- 实测：headless Edge 录制 19 原始事件步（click×8 + type×1 + hotkey×10），JSONL 写入成功。
- 验证：Python Trajectory.load 19 步加载 OK；pytest 188 passed。
- 提交：43787dd [auto-patrol] round 594。

## 下一轮要看的具体点
- 用真实 TYPESAFE_API_KEY 手工 smoke TypesafeJevScorer 路径。
- 评估 feat/decision-jev-layer 本地 ahead 179 是否推送 origin。
- 考虑在 bridge 层增加 semantic click 合并（mousePressed+Released → 单步）。

## blocker
- low：本机仍未配置 TYPESAFE_API_KEY，真实 TypeSafe JEV 无法实测。