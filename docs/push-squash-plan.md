# feat/decision-jev-layer 推送前 squash 计划

> Round 606 落档。用户显式授权前 **不** `git push`。
> 当前分支相对 `origin/feat/decision-jev-layer` 约 ahead 50。

## 推荐 5 主题 squash

1. **record schema verifier** — `examples/verify_record_schema.py` + `tests/test_verify_record_schema.py` + Round 600 tracking
2. **hybrid_generate transport contract** — `openeyes/record/hybrid*` + `tests/test_record.py` Transport 用例（03351cc 族）
3. **decision / jev shadow** — `openeyes/decision*` / Jev scorer fallback + related tests
4. **debug-experience + event-driven dsh gate** — `docs/debug-experience.md` Round 604-606 段；gate 策略说明
5. **tracking files refresh** — `last` / `last-message` / `last-round` / `.codex/*` 巡检跟踪（可最后单独 commit）

## 执行顺序（授权后）

```powershell
# 1) 再跑一次稳定性门禁
python -m pytest tests/ -q --deselect tests/test_launch_debug_edge.py::test_helper_help_exits_zero_and_prints_usage

# 2) 交互 rebase / squash（示例：保留主题边界）
git rebase -i origin/feat/decision-jev-layer

# 3) 推送（需用户显式授权）
git push -u origin feat/decision-jev-layer
```

## 明确不并入

- `test_launch_debug_edge` flaky 修复（独立 issue）
- 真实 `TYPESAFE_API_KEY` smoke（本机 key 未配）
- dsh Section 4 acceptance（event-driven，待 :3080 恢复）