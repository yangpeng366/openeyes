## Round 601 - 2026-09-22
- 落地：`eyes record replay <jsonl> [--json]` 安全 dry-run CLI；输出动作计划、跳过数与错误数，不发送真实输入。
- 文件：`openeyes/cli/main.py`、`tests/test_cli_record.py`、`README.md`；提交 `2704c56`。
- 命令：`python -m pytest -q`（198 passed）；`eyes record replay examples/record-out/trajectory-edge-real.jsonl --json`（5 planned / 0 skipped / 0 errors）。
- BOM 检查：新增/修改源文件前三字节非 `EF BB BF`。
- 方向前三项（bridge、JEV backend、真实 Edge 5 步 schema 双向对齐）已由 Round 600 验证闭环。

## 下一轮要看的具体点
- 为 `hybrid_generate` 真实 LLM 适配边界补 mock-transport 集成测试，验证 key 存在、超时与 fallback 契约。