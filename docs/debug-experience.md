# OpenEyes debug experience（巡检调试经验沉淀）

> 用途：连续多轮巡检踩到的根因、关键命令、踩坑统一沉淀在此，方便后续轮巡快速恢复上下文，不必每次从零摸探。

---

## DSH web gate :3080 不可达（Rounds 516-590+ 持续同质 evidence）

### 现象

- `python examples/dsh-gate-readiness.py` 持续退出码 `2`，`ready=false`、`status_code=null`、`error=<urlopen error timed out>`。
- `Get-NetTCPConnection -LocalPort 3080 -State Listen` 无任何输出（`listener_count=0`）。
- `Get-Process -Name dsh` 报 `Cannot find a process with the name "dsh"`（`dsh_count=0`）。
- docs/dsh-web-acceptance.md Section 4（`browser_click` dry-run）自 Round 516 起连续 75+ 轮无法执行。

### 根因

- 外部服务 `dsh web host` 自 Round 516 起未恢复；HNS 或 EDR 根因仍未复核。
- 本机侧无可控手段：不启动服务、不修改 :3080 端口、不发送 browser_click。
- 验证脚本 `examples/dsh-gate-readiness.py` 故意 read-only：见脚本顶部 docstring。

### 关键命令（巡逻标准）

```powershell
# readiness 探针（read-only，HTTP 超时 2s）
python examples/dsh-gate-readiness.py --next-recheck <ISO-8601>

# 端口监听检查
Get-NetTCPConnection -LocalPort 3080 -State Listen

# 进程检查
Get-Process -Name dsh
```

### 关键约定

- **不早期复探**：HTTP 超时已确认可达性失败，复探只消耗本机额度，不产生新信息。
- **单轮仅一次 probe + 三条命令**；round 间保持相同模板，避免 drift。
- **docs/dsh-web-acceptance.md 末尾追加 Round N evidence**：每轮 `<= 9 行`，含时间戳、命令、原始输出、旁证、阻塞、下一步；连续同质 70+ 轮后建议改为恢复事件触发或归档历史 round note。
- **last / last-message / last-round** 跟踪文件每轮刷新；不动 .codex/* 那份（那是另一份路径，独立刷新）。

### 不在 DSH 阻塞下继续推进的策略

- record/、jev、edge schema 等本地工作（feat/decision-jev-layer）继续推进；与 DSH 阻塞无关。
- pytest -q 必须保持 >= 196 passed（含 round 600 schema verifier 6 条、round 602 hybrid_generate transport 4 条）；任何下降立即隔离。
- TYPESAFE_API_KEY / LLM_API_KEY 本机未配，real LLM 路径仅靠 monkeypatch 覆盖（low blocker）。

### 解除阻塞的判定

仅当以下全部成立方可执行 Section 4：

1. `python examples/dsh-gate-readiness.py` 返回 HTTP 200（exit code 0）。
2. `Get-NetTCPConnection -LocalPort 3080 -State Listen` 至少一条记录。
3. 用户或服务负责人确认服务已恢复（飞书通知 / 群消息 / 显式 OK）。

恢复后第一轮需要重新跑完整 probe suite（不复用历史 not-ready evidence）。

---

## pytest 偶发 flaky：test_launch_debug_edge

### 现象

- full suite `-q` 时偶发失败；单独运行 pass。
- 与 round 602 改动无关，单独 `--deselect` 可过。

### 应对

- 巡检 full suite 用 `python -m pytest tests/ -q --deselect tests/test_launch_debug_edge.py::test_helper_help_exits_zero_and_prints_usage` 跳过。
- 不要在本仓 commit 里"顺手修"——属历史 flaky，需独立 issue。
- 完整调用：`python -m pytest tests/ -q --deselect tests/test_launch_debug_edge.py::test_helper_help_exits_zero_and_prints_usage`

---

## AnyVPN 保活实操（2026-10-26）：送达 ≠ 生效

实战对象：Any VPN（MSIX 包 `AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App`）免费会话保活，脚本 `examples/anyvpn_keepalive.py`。落地提交 `6d57753`、`c646c76`。

### 现象

- 点击报成功，但会话照样掉线；旧脚本的判据是"按钮名字变了"。
- 续期总是踩在掉线那一刻：免费会话 20 秒到期，而脚本 `interval=20`，等于每次都点�� `0:00 left`。
- 会话超时后 Any VPN **自己关掉窗口**（进程还在，窗口没了），脚本只能退出。
- 连上后 Any VPN 以 `RouteMetric 1` 抢下默认路由，此时 `223.5.5.5` / `www.baidu.com` 实测不通；会话过期、路由回落后恢复。出口 IP 实测仍是本地运营商（`182.150.63.187`，`loc=CN`），即"连上了但流量没走隧道"——**此条待复测**。

### 根因（按可复用程度排序）

1. **动作送达不等于业务生效。** 按钮的可访问名是**活的倒计时**（`0:18 left - click to reset`），每秒自变；拿"名字变了"当成功判据等于永远判真。这是产品级信任问题，不是脚本 bug。
2. **坐标语义不统一。** DPI 不感知进程里 UIA 报物理像素，而 `GetWindowRect` / `SetCursorPos` 报虚拟化坐标，两套坐标系混用 → 点击偏几个像素、甚至点到别的控件。
3. **元素身份不稳。** 按可见名选元素在活的 UI 上不可靠；`automation_id`（`KeepFreeAliveButton`）才是稳定锚点。
4. **只有像素点击这一种动作方式。** 抢焦点、动用户鼠标，且 `SetCursorPos` 后紧跟点击可能落在**旧光标位置**（窗口还没处理 `WM_MOUSEMOVE`）；XAML/UWP 还会忽略零时长点击。
5. **周期任务被写成了固定间隔。** 正确形态是读状态、判阈值、到点才动手。
6. **应用生命周期是自动化的隐形边界。** UWP 应用会自己退，自动化必须能识别并重建。
7. **同一控件在不同时刻语义不同。** `ConnectButton` 连接后标签变 `Disconnect`；不做前置断言就可能在会话活着时把它点掉。

### 关键结论（可直接复用）

- **判据要用状态语义，不要用标签文本。** 续期成功 = 倒计时**回跳**（`9s → 19s`），不是"名字变了"。
- **点击优先 UIA Invoke 图案**：`click_element()` 走 Invoke，不需要像素、不抢焦点、不动用户鼠标；像素点击只作降级。
- **恢复窗口用 `SW_SHOWNOACTIVATE`（4）**：最小化窗口的控件树是空的，但 `SW_RESTORE` 会抢前台；`SW_SHOWNOACTIVATE` 既重建控件树又不动前台（实测前台始终是原窗口）。
- **动作前校验控件当前语义**：标签是 `Disconnect` 时拒绝点击 `ConnectButton`。
- **调用方要能区分"动作已投递"和"状态已改变"**：日志与报告必须分两级。
- **落成原语**：`openeyes.core.verify.act_until(act, expect, retries=N)`——执行 → 轮询期望 → 不成立换策略重试 → 返回 `{ok, attempts, result, elapsed, error}`。AnyVPN 脚本已改为它的参考实现。

### 复现命令

```powershell
python examples\anyvpn_keepalive.py --go --bootstrap --trigger 10   # 保活
python examples\anyvpn_disconnect.py                               # 只断开
python -m pytest tests/test_keepalive_logic.py tests/test_verify.py -q
```

---

## 实战元宝桌面客户端（2026-10-27）：深度默认值是真缺口

用户点名要「测点其他功能，比如 openeyes 操作元宝桌面客户端」。元宝是
Tauri + WebView2（`yuanbao.exe` v2.90.0.60），这一测就测出两个真问题。

### 问题 1：`max_depth=12` 够不到 WebView2 的 DOM

UIA 深度 12 时元宝只有 **25** 个元素，止步于 `BrowserRootView`；深度 25
时是 **387** 个元素，整个 DOM 露出来了（对话列表、发送按钮、输入区）。

更要命的是**发送按钮只在深度 20 才出现**：

| 深度 | 元素数 | 找到 `yuanbao-send-btn` |
| --- | --- | --- |
| 12 | 25 | ❌ |
| 20 | 255 | ✅ |
| 25 | 387 | ✅ |

于是 `act_and_verify` 报了 miss，证据是 `names: []` —— 看起来像
「这个控件不存在」，其实只是**没看够深**。这是最坏的一种失败：报错
把「没找」说成了「没有」。

**修法**（根因，不是打补丁）：默认深度 12 → 30（`uia.DEFAULT_MAX_DEPTH`），
core / CLI（`--depth`）/ MCP（`depth=`）三处统一，`eyes detect
--automation-id` 可按稳定 id 直查。

### 问题 2：WebView2 应用，UIA 拿不到 DOM

UIA 能看到按钮（Invoke 可用），但**输入框不是 Edit 控件**——它是个自定义
div，树上只有占位符文字「发送消息」。要往里打字、要读 DOM 结构，就得上
CDP。

WebView2 在创建浏览器进程时读 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`，
所以带这个环境变量启动外壳就能开 DevTools 端口。已落
`openeyes/backends/webview2.py` + `eyes webview describe|launch`。

**实测坑**：CCX Desktop（Wails）自己设了 WebView2 浏览器参数，**覆盖了
环境变量**，端口不开；命令行 `--remote-debugging-port` 也不行（Wails 要
在 `wails.Run` 里设）。元宝未测（Tauri 是否覆盖需实测）。

> 所以 `eyes webview describe` 存在的意义就是：**端口不通时说清怎么通**，
> 而不是让人猜。

### 实机通过

```
eyes detect --window 1380868 --automation-id yuanbao-send-btn --pretty
  -> [Group] (1714,1068) 37x37 '发送'

click_by_id(dry_run=true)          -> ok, method=dry-run, center 精确定位
act_and_verify(dry_run=false)      -> ok=true, attempts=1, elapsed 2.03s
```

点击真的送到了：元宝回了「👌 收到～ 看起来你是确认了一下，没发送消息」
（空输入时点击后的真实回应）。全程 Invoke，**未动用户鼠标**。

### 元宝的锚点与可操作面

| 目标 | UIA 锚点 | 能否操作 |
| --- | --- | --- |
| 发送按钮 | `yuanbao-send-btn` | ✅ Invoke 稳定 |
| 输入框 | 无（自定义 div） | ❌ 需 CDP |
| 侧栏会话 | 仅有可见名 | ⚠️ 名字当锚点，可用但不稳 |

---

## 保活改用 supervise + 打包应用的身份（2026-10-28）

### 保活脚本终于用了通用循环

`examples/anyvpn_keepalive.py` 的自写循环是仓里最后一块没用
`openeyes.core.supervise` 的地方。改写后：读倒计时 → 进续期窗才动 →
自证倒计时**回跳** → 按剩余时间排下一轮。外围脚手架（重启/重连/隐藏/
截图）不变。

**每次运行现在都带信任数字**：

```
[openeyes] summary: rounds=3 renewed=0 missed=0 unknown=0 failed=0 ...
[openeyes] trust: false_positive_rate=0.00% interference_rate=0.00% ...
```

### 改写中暴露的三个语义 bug

1. **dry-run 变成了会动作** —— 为拿「动作会报什么 verdict」而调用了
   `act()`，等于 dry-run 也执行了动作。改为独立的只读 `preview` 钩子，
   动作永不调用（这条被测试当场抓住）。
2. **动作失败 ≠ 续期失败** —— 「App 根本不在」原先被记成 missed（像是
   点了没生效）。实为 delivery failure，应记 failures。
3. **两套账** —— 脚本自己记一套、supervise 记一套，数字对不上。改为
   **以 supervise 报告为唯一口径**。

### 打包应用的身份：Any VPN 实测

| 事实 | 值 |
| --- | --- |
| 显示名 | Any VPN |
| 真实进程 | `VpnGui.exe`（在 `WindowsApps\AnywaySoftInc.AnyVPN_...\`） |
| 窗口宿主 | `ApplicationFrameHost.exe`（类名 `ApplicationFrameWindow`） |

于是：

- 按显示名查进程 → **找不到**（进程叫 VpnGui）
- 按显示名过滤窗口 → **也找不到**（窗口不属该进程）

**修法**：进程检查识别「进程来自哪个包」（按包目录名匹配，忽略标点与
版本后缀）；窗口查找以**窗口身份（标题/类名）为主**，属主进程只作次要
信号，且框架宿主一律放行。

实测：`app_state(title_contains="Any VPN", process_name="Any VPN")` 从
`not-running` 变为 `running, hwnd=4333408`。

> 这条坑对所有 Store/UWP 应用通用 —— 飞书、钉钉、元宝同理。

---

## 度量而不是相信：误报率与干扰度（2026-10-27）

保活脚本「日志一片 verified，会话其实在掉」这件事，靠人眼看不出来——
脚本自己没撒谎，是**判据**撒谎。修好判据之后还需要回答第二个问题：
这个循环**值不值得留在别人的桌面上**。

### 两个数字

- **误报率**：循环报告成功、但状态其实没变的比例。
- **干扰度**：循环打扰用户的比例（动鼠标、抢焦点）。

### 实测（Notepad 菜单，4 轮）

| 路径 | 有效 | 误报率 | 动鼠标 |
| --- | --- | --- | --- |
| Invoke | 4/4 | **0%** | 0 |
| 像素 | 0/4 | **100%** | 3/4 |

像素那一行是本轮最大的收获：点击**送达了**（`landed=True`，光标确实落在
控件上），菜单却没开——因为窗口不在前台。**若把「送达」当「成功」，这轮
会报四次成功、实际什么都没发生。**

### 归因：算自动化的账，不算桌面的账

第一版度量工具给出的数字是假的：误报率 100%、干扰率 50%。查下来两个坑：

1. **光标本就会动** —— 这台机器上有人在用鼠标。把 before/after 的光标差
   记成「自动化干扰」是冤枉它。改为：只有自动化**真的驱动了指针**
   （像素点击且 landed）才算动鼠标。
2. **焦点本来就在目标上** —— `foreground_after == hwnd` 不等于「被抢」。
   正确判据是「自动化把焦点从别处**抢到**目标上」。

修正后：Invoke 干扰率 0%，像素 100%。数字终于可用。

### 工具

`examples/measure-interference.py`：可指定 `--method`、`--rounds`、
`--menu-probe`（内置场景：打开真实菜单，以菜单项数量增长作有效性判据），
并支持调用方自带 `effective` / `reset` 探针。

> 附带一条纪律：**有效性判据不能是「标签变了」**。那正是 AnyVPN 的旧错
> 判据——活的倒计时每秒自变。默认判据只作兜底，真实场景要传自己的探针。

---

## 应用生命周期原语（2026-10-27）：把「窗口没了」拆成两种病

第 6 条根因（"应用生命周期是自动化的隐形边界"）此前只有 AnyVPN 脚本里的
零散代码兜着，现已抽成通用原语 `openeyes.core.lifecycle`。

### 现象

会话超时后 App 自己关掉窗口。此时一句「窗口没了」是**不够的诊断**：

- 进程还在、窗口没了 → App 只是关窗，补救是**恢复窗口**（不抢焦点）。
- 进程也没了 → 补救是**重新拉起**。

两者都表现为「找不到窗口」，但一个该 restore、一个该 launch。判断错了，
要么白等（对着死进程轮询），要么误重启一个本来活着的 App。

### 原语

| 函数 | 作用 |
|---|---|
| `app_state(title_contains=, class_name=, process_name=)` | 判定 `running` / `window-gone` / `not-running` / `unknown`，并给出证据 |
| `restore_window(hwnd)` | `SW_SHOWNOACTIVATE` 唤回，**不抢焦点** |
| `launch_app(exe=, aumid=)` | 拉起 App（Store 应用走 `shell:AppsFolder`） |
| `ensure_app(..., ready=)` | 组合拳：查状态 → 恢复/拉起 → **等它真的可用** |

`ensure_app` 沿用 openEyes 一贯的「送达 ≠ 生效」原则：`ok=True` 只在
**观测到 App 真的可用** 时成立，launch 动作发出不算数；`ready` 判据可
再收紧（例如「UIA 树非空」「按钮已就位」）。

### 判定顺序

先看**进程**在不在，再看**窗口**在不在：

- 进程在 + 窗口在 + 未最小化 → `running`（直接可用）。
- 进程在 + 窗口最小化 → `window-gone`（控件树是空的，需 restore）。
- 进程在 + 找不到窗口 → `window-gone`（无句柄可恢复，直接 launch）。
- 进程不在 → `not-running`（launch）。

### 出口

```powershell
eyes app state --title-contains "Any VPN" --process-name "Any VPN"
eyes app ensure --title-contains "Any VPN" --aumid "AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App" --go
```

MCP 侧新增 `app_ensure`（工具面 15 → 16，默认 `dry_run=true`）。

### 复现命令

```powershell
python -m pytest tests/test_lifecycle.py -q
python -m openeyes.cli.main app state --title-contains "Any VPN" --process-name "Any VPN"
```

---

## Branch / commit 推进约定

- 主巡检分支：`feat/decision-jev-layer`（已 ahead origin 47+ commits）。
- docs / tests / examples / skills 单文件改动可直推 main（见 `docs/maintenance-policy.md`）。
- source / release / permissions / dependencies / destructive changes → PR + 用户决策。
- 巡检 round 不主动 git push；本地 commit 后待用户显式授权。

---


---

## Round 604 stability re-run (2026-09-29)

- pytest tests/test_record.py::TestHybridGenerateTransport -q => 4 passed。
- pytest tests/ -q --deselect tests/test_launch_debug_edge.py::test_helper_help_exits_zero_and_prints_usage => 201 passed, 1 deselected（17.42s）。
- 结论：Round 603 状态持续；仍未配置 TYPESAFE_API_KEY，real LLM path 阻塞未解除。

## feat/decision-jev-layer 推送前风险复盘

- ahead origin 已达 48 commits（含 Round 600-604）；推送窗口一旦扩大将难以拆 PR，建议先 squash 成 4-5 个主题提交再推 origin。
- 推荐主题：record schema verifier、hybrid_generate transport contract、decision/jev shadow、debug-experience doc、tracking files refresh。
- 在 user 显式放权前不主动 git push。

---

## Round 605 event-driven dsh gate (2026-10-08)

- 现象：lastExit=2, consecutiveFailures=2, resumeCount=4；DSH 仍超时，listener_count=0，dsh_count=0。
- 决策：依据 ad-hoc 备注（516-590 连 75 轮同质 evidence），本轮正式切到 **event-driven dsh gate**。
- 后续策略：
  - 不再按固定间隔复探 python examples/dsh-gate-readiness.py。
  - 仅当 (a) :3080 出现 Listen，(b) dsh 进程被重启，或 (c) 用户在飞书 / Bitable 显式确认服务恢复 时，才重跑 readiness probe。
  - 触发后跑 python examples/dsh-gate-readiness.py，HTTP 200 才进入 docs/dsh-web-acceptance.md Section 4。
- 巡检脚本（Start-CodexAutoPatrolLoop.ps1）可参考 state/projects/OpenEyes.json 的 dshGateMode=event-driven 字段跳过本项目 readiness 子步，避免连续 exit 2 触发 thread 重置。

---

## Round 606 patrol loop dshGateMode wiring (2026-10-09)

- 落地：.codex-auto-patrol/Start-CodexAutoPatrolLoop.ps1 读取 state/projects/<Project>.json 的 dshGateMode / dshGateResume。
- 当 dshGateMode=event-driven 时，把硬规则注入子 agent prompt：禁止固定间隔复探 readiness；仅 listener / dsh 进程 / dshGateResume=true 才允许重跑。
- OpenEyes state 已写：dshGateMode=event-driven，dshGateResume=false。
- 旁证本轮：listener_count=0，dsh_count=0；未跑 readiness probe。
- 另落 docs/push-squash-plan.md：5 主题 squash 推送计划（授权前不 push）。
## Provenance




- 2026-09-22：DSH 阻塞连 75+ 轮后首次系统沉淀 debug 经验；201 passed, 1 deselected（历史 flaky 单独排除）。
- 2026-10-26：AnyVPN 保活实操沉淀"送达 ≠ 生效"这条产品级教训，并落成 `openeyes.core.verify.act_until` 原语（AnyVPN 脚本改为参考实现）。