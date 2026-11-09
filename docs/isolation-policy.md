# 隔离策略：什么时候必须离开共享桌面

- 方向：鼠标集群（多个 AI Agent 同桌协作、尽量不打扰用户）
- 判定代码：`openeyes/agent/isolation.py`（`evaluate_isolation`）
- 配套测试：`tests/test_isolation.py`
- 实机核查日期：2026-11-09

## 0. 这份文档解决什么

同桌的前提是共享同一个 Windows 会话：**一个前台窗口、一根系统指针、一个人**。本方向前五步——动作账本、投递阶梯与抢前台预算、跨进程租约、Agent 光标窗口、注意力预算与冲突调度——都在这同一个前提里做优化：少抢前台、少动指针、让用户少被打扰。

但有些工作调不好。调不好的时候，继续在共享桌面上硬调，代价由用户承担：每一次抢前台、每一次把指针挪走，都是一次打扰。于是必须有一条明确的界线——越过它，就换环境。

本文档给出三样东西：

1. 隔离阶梯 L0–L5（往哪儿走）；
2. 可判定的升级条件（什么时候走）；
3. 降级路径（什么时候能回来）。

判定逻辑不是散文，而是代码：`openeyes.agent.isolation.evaluate_isolation()`。人读的 `reasons` 与机器读的 `level` 出自同一次计算，不会各说各话。

## 1. L0–L5 隔离阶梯

| 级 | 手段 | 隔离了什么 | 适用场景 | 代价 | 本方向现状 |
| --- | --- | --- | --- | --- | --- |
| L0 | 语义化优先（UIA Invoke / 原生接口） | 不隔离任何东西——只是不碰指针 | 控件暴露 UIA 操作、原生命令行、URL scheme、Store AUMID | 无；只有探测与兼容性成本 | **已实现** |
| L1 | 租约串行化物理输入 | 指针使用权（跨进程） | 必须物理点击，但可以排队 | 等待延迟；租约竞争 | **已实现** |
| L2 | Agent 光标窗口与身份 | 视觉上的"谁在动" | 多 Agent 同屏，用户要能分辨谁在动 | 屏幕占用；实现复杂度；本机不能用分层窗口实现（§4.2 第 4 条） | 代码已写，**本机不可见**，待改造 |
| L3 | 独立会话 / 独立桌面 | 前台窗口（可以有自己的前台） | 语义路径大面积失效；打扰预算反复耗尽 | 另起会话的成本；用户看不见，回报要另设通道 | 未实现（只有判定与文档） |
| L4 | VM 隔离 | 指针、屏幕、桌面全部分开 | 真并行（多个 Agent 必须同时动手） | 启动一台机器的时间与内存 | 未实现（本机无 VM 设施，见 §4） |
| L5 | 无头隔离（无可见桌面） | 连渲染都没有 | 批处理、无人值守 | 失去视觉反馈与截图证据链 | 未实现 |

### L0 语义化优先

**适用场景**：目标控件能被无障碍接口直接操作——UIA 的 Invoke/Value/Selection 模式，或者应用自己提供的命令行、URL scheme、可执行文件、Store AUMID。感知侧走 `uia → cdp → vision` 的降级链，交付侧走 `invoke → message → pixel` 的投递阶梯。

**代价**：探测开销，以及"部分控件只暴露一部分属性"的兼容性债。`native_surfaces` 的意义正在于此——"没找到"必须和"没找过"区分开。

**现状**：`openeyes/backends/uia.py`、`openeyes/core/resolve.py`、`openeyes/core/native.py` 已实现。L0 是本方向的地基：L0 越结实，L1/L2 的压力越小。

**本机约束**：L0 本身仍然可行——语义调用（UIA Invoke、原生接口）不依赖任何窗口合成。但 L0 的**视觉层**（Agent 光标窗口）不能靠分层窗口实现：本机的分层窗口不被 DWM 合成到屏幕上（§4.2 第 4 条），本机做光标窗口必须走非分层 + 窗口区域（`SetWindowRgn`）路线。

### L1 租约串行化物理输入

**适用场景**：语义路径走不通，只能物理点击，但这件事可以排队。`InteractionLease` 在 Windows 上是排他 `CreateFileW` 打开锁文件：进程死了锁自动释放，不留脏状态。同一层还有抢前台预算（`FocusBudget`）——预算不是"不许抢"，而是"抢的次数要记账、要可拒绝"。

**代价**：等待延迟；多 Agent 同时要指针时会退化成串行；抢前台的次数仍然是打扰，只是被限量。

**现状**：`openeyes/agent/lease.py`、`openeyes/agent/profile.py` 已实现。

### L2 Agent 光标窗口与身份

**适用场景**：多个 Agent 都要在用户的屏幕上动，用户需要一眼看出是谁在动。Agent 光标窗口是"画出来的"，不是"驱动出来的"：它不碰系统指针、不抢前台，用户自己的窗口既不被遮挡焦点，也不被抢走。

**代价**：屏幕占用；绘制与身份映射的实现复杂度；它治的是"看得懂"，不治"不打扰"。

**本机约束**：现有实现（`openeyes/agent/overlay.py`）用的是分层窗口（`WS_EX_LAYERED` + `ULW_ALPHA`）。本机实测：**分层窗口不被 DWM 合成到屏幕上**（§4.2 第 4 条），所以这套实现在这台机器上画出来的东西用户根本看不见。可行路线是**非分层顶层窗口 + 窗口区域（`SetWindowRgn`）**——用形状裁出光标轮廓，代价是失去逐像素透明与抗锯齿边缘。改造 L2 不在本轮范围内（本轮只做判定与文档），但改造之前，L2 在本机**不可验收**。

**现状**：代码已写（`openeyes/agent/overlay.py`）。非分层 + `SetWindowRgn` 的改造已有并行改动在途（工作区未提交），本轮没有验证它的效果——改造完成并实测通过之前，L2 在本机仍算**未验收**。

### L3 独立会话 / 独立桌面

**适用场景**：语义路径大面积失效，或者打扰预算反复耗尽。此时问题已经不是"怎么少打扰"，而是"这份工作在这个桌面上没法不打扰"。给 Agent 一个属于自己的会话，它抢的是自己的前台，不是用户的。

**代价**：另起会话或虚拟桌面的成本；用户看不见那边发生了什么，状态回报要另设通道；本机的"桌面 2"还有权限与 LUID 的硬约束（见 §4.2）。

**现状**：未实现。本方向目前只提供**判定**：达到条件时明确说"该换环境了"，而不是继续硬调。

### L4 VM 隔离

**适用场景**：真并行。多个 Agent 必须同时动手时，共享桌面在物理上就是不够的——只有一个前台窗口和一根系统指针（§4.2）。

**代价**：启动一台机器的时间与内存；环境编排；截图与状态回传要走网络。

**现状**：未实现。本机没有现成设施（§4.1），因此 L4 的定位是"条件具备时再上"，近期不投入。

### L5 无头隔离

**适用场景**：批处理与无人值守。屏幕上根本没有东西可看时，视觉反馈是纯开销。

**代价**：失去视觉反馈与截图证据链；出错时没有现场可看；`act_and_verify` 这类"做完再证明"的手段受限。

**现状**：未实现。

## 2. 什么时候必须升级到 L3 及以上

三条触发条件与 `evaluate_isolation()` 一一对应，全部是**闭区间**判定：实测等于阈值即触发。

### 2.1 真并行 → L4（VM）

**条件**：`needs_parallel=True` 且 `concurrent_writers >= parallel_min_agents`（默认 2）。

**为什么是 L4 而不是 L3**：并行不是排队问题。同一桌面只有一个前台窗口和一根系统指针，两个 Agent 同时动手只能互相抢，互相抢就是打扰用户。独立会话也救不了——会话里仍然只有一根系统指针（§4.2 第 3 条）。

**判定示例**：

```python
from openeyes.agent.isolation import evaluate_isolation

evaluate_isolation(needs_parallel=True, concurrent_writers=3)
# escalate=True, level='vm'
# reasons[0]: 真并行：阈值 concurrent_writers >= 2，实测 concurrent_writers=3（needs_parallel=True）；同一桌面只有一个前台窗口和一根系统指针
```

### 2.2 语义路径大面积失效 → L3（独立会话 / 独立桌面）

**条件**：`semantic_miss_rate >= semantic_miss_rate_threshold`（默认 0.5）。

**为什么是 L3**：语义路径还在，只是"大面积"失效而不是全部失效，说明这个应用确实只有物理点击能通。但既然语义路径不可靠，Agent 就会退化成"打鼠标的人"，每次动作都要抢前台。给它自己的会话，它抢的是自己的前台。

**口径**：语义失效率 = 语义路径失败次数 ÷ 总尝试次数。失败包含 `NO_SEMANTIC_PATH`（控件不提供无指针操作）与 `BACKGROUND_UNAVAILABLE`（语义路径够不着目标）。

### 2.3 打扰预算反复耗尽 → L3

**条件**：`budget_exhausted >= budget_exhausted_threshold`（默认 3 次/分钟）。

**为什么是 L3**：预算被拒一次是正常的——预算就是用来拒的。被拒三次以上，说明这份工作在这个桌面上没法不打扰人。换到自己的会话，代价从"打断用户"变成"多开一个环境"，后者可控得多。

**但要诚实**：L3 解决的是"抢前台"，不是"要人的注意力"。独立会话能让 Agent 在别处打断，而不必从用户手里抢前台；它不能让用户少被打断。所以 L3 在这一条上是**止血**，不是根治——根治是让这份工作本身少要人看。

**口径**：一分钟内"预算被拒"的次数，也就是**本来会打扰到用户、却被预算挡住**的次数。两个来源，含义不同、不可混用：

| 计数器 | 它数的是什么 | 出处 |
| --- | --- | --- |
| `FocusBudget.refused` | 想抢前台、被预算拒绝的次数（仅在预算 > 0 时计入，见下） | `openeyes/agent/profile.py`，与 `BUDGET_EXCEEDED` 同源 |
| `AttentionBudget.disturbance` | 想打断用户（要一次注意力）、被预算拒绝的次数（仅在预算 > 0 时计入） | `openeyes/agent/attention.py` |

**口径的关键**：只有"真的会被打扰、但被挡住了"才算数。`AttentionBudget.take(False)`（本次没有任何东西要给用户看）**不消耗额度、也不计入 `refused`**——那一次根本没人被打扰，拿它当打扰证据，等于把"安静"误判成"打扰过多"。

**不要用 `AgentPresence.refused`** 做隔离度量：它与 `AttentionBudget.refused` 记的是同一批事件（Agent 想被看见、却被预算挡下），唯一差别是「预算从未发放」时它照记、而 `disturbance` 不记——那种情况说明 Agent 配置错了，不是桌面不够大。隔离层只读上表两个。

**零预算不算"耗尽"**：`per_minute = 0` 的预算从未发放，`AttentionBudget.exhausted` 对它也返回 False。它的拒绝次数说明的是"这个 Agent 配置错了"（预算说别打扰，它偏要打扰），不是"桌面不够大"——把它算成打扰证据，等于把配置问题判成隔离问题，判据本身就错了。所以上表两个计数都只在**预算确实发放过**（`per_minute > 0`）时才计入；`AttentionBudget.disturbance` 已经替你做了这个判断。

### 2.4 优先级、阈值与度量的出处

| 指标 | 默认阈值 | 升级到 | 取值口径 | 出处 |
| --- | --- | --- | --- | --- |
| `semantic_miss_rate` | 0.5 | L3 | 语义失败次数 ÷ 总尝试次数 | `NO_SEMANTIC_PATH` / `BACKGROUND_UNAVAILABLE` 计数 |
| `budget_exhausted` | 3 次/分钟 | L3 | 滑动一分钟内**预算被拒**的次数（预算 > 0 才有意义） | `FocusBudget.refused`（抢前台被拒）或 `AttentionBudget.disturbance`（打断被拒）；**不含** `AgentPresence.refused`（§2.3） |
| `needs_parallel` + `concurrent_writers` | 2 | L4 | 同时要动手的 Agent 数 | 租约等待方计数（`LEASE_TIMEOUT` 只是争抢，不等于真并行） |

**优先级**：多条同时触发时，只要有一条是 vm 级，`level` 就是 `vm`；否则为 `session`。`reasons` 里每条都写清「条件 / 阈值 / 实测」，可以直接对人讲。

**调度冲突不是打扰**：`Scheduler`（`openeyes/agent/scheduler.py`）记录 Agent 之间对同一目标的争抢，以及高优先级抢占。那是 **Agent 对 Agent** 的冲突，不是对用户的打扰，因此**不计入 `budget_exhausted`**。抢占恰恰是在同桌面内把"并行"化解成"串行"的机制，与隔离判定不冲突：隔离只在**业务上真的要求同时动手**（`needs_parallel`）时才介入。若拿"调度器报了冲突"去推 `needs_parallel`，等于把一次正常的排队误判成需要隔离。

**阈值为什么可以调**：`IsolationConfig` 把三个阈值都做成配置。默认偏保守（要过半失效率、要在预算上连续被拒三次），因为升级本身有成本——换环境的时间、另起会话的注意力，都是代价。宁可多共享一会儿桌面，也别动不动就搬家。

## 3. 降级路径

### 3.1 降级不需要第二个函数

判定是纯函数：同一套度量重新评估即可。输入变好，`level` 就回到 `none`。这是刻意设计的——升级是"止损"，降级是"复检"，两者用同一把尺子，避免两套逻辑各说各话。

这条由测试钉住：`tests/test_isolation.py` 的 `test_the_verdict_can_come_back_down`——先用触发的度量升到 `vm` / `session`，再用同样好的度量重新评估，断言回到 `none` 且 `reasons` 为空。

### 3.2 三条恢复信号（与三条触发条件一一对应）

| 触发条件 | 恢复信号 | 判定回到 | 可落回的实际层级 |
| --- | --- | --- | --- |
| 语义路径大面积失效 | 语义失效率回落到阈值以下并保持稳定 | `none` | L0 |
| 打扰预算反复耗尽 | 滑动窗口内 `refused` / `disturbance`（§2.3）归零 | `none` | L0 / L1 |
| 真并行 | 并行需求消失（`concurrent_writers` 回到 1，或 `needs_parallel` 不再成立） | `none` | L2 |

### 3.3 降级的纪律

- **一次偶然成功 ≠ 恢复**。建议以连续 N 次评估为准（N ≥ 3），中途任一次反弹就重新计数。
- **逐级来**。降级顺序与升级相反：L5 → L4 → L3 → L2 → L1 → L0，不要从 L4 一步跳回 L0。
- **先确认环境还在**。会话或 VM 可能已经被回收，降级前先看一眼它是否可用。
- **降级也要记账**。把降级写进动作账本（`ActionLedger`），与升级同等对待——否则事后没人说得清"什么时候回到共享桌面上的"。
- **不降级也可能是对的**。如果环境已经就绪，而重建环境的成本高于打扰成本，留在 L3/L4 是主动选择，不是失败。这种情况要在账本里注明理由。

### 3.4 处置速查

| 情形 | 处置 |
| --- | --- |
| `level='vm'`，但本机没有 VM 设施 | 降级到"串行 + 显式排队"的工作模式，并在账本里记明"真并行不可得"；不要假装能并行 |
| `level='session'`，语义路径已恢复 | 先降回 L0/L1 试跑，连续 3 次评估稳定后再撤掉独立会话 |
| `level='session'`，预算仍反复耗尽 | 说明工作本身就必须打扰人：要么改流程（少做几次），要么承认需要独立环境 |
| 指标读数缺失或为 0 | 不要按"没有证据"处理成"没有问题"；缺读数应视为不可判定，先补度量 |

## 4. 本机现状（2026-11-09 实机核查）

### 4.1 环境事实

- 操作系统：**Microsoft Windows 11 家庭版 Insider Preview**，`Win32_OperatingSystem.Version = 10.0.26340`，`BuildNumber = 26340`，单会话，RDP 默认关闭（`fDenyTSConnections = 1`），无 Sandbox，`vmms` 未注册，无 docker / VBoxManage / qemu，内存 13.9 GB，WSL2 Ubuntu-22.04（Stopped）
- **坑**：`Get-ComputerInfo` 的 `WindowsProductName` 在这台 Win11 机器上仍报旧值 `Windows 10 Home`，`WindowsVersion` 报 `2009`——这两个是陈旧字段（取自旧版注册表键），不能当产品名用。认准 `Win32_OperatingSystem.Caption`（`Microsoft Windows 11 家庭版 Insider Preview`）与 `Version`（`10.0.26340`）。`WindowsEditionId = Core`、`OsBuildNumber = 26340` 这两个字段是准的，可以用。
- 显卡与输出：AMD 独显 2240x1400 为唯一活动输出（dxcam 只枚举到 output 0）；在册两个虚拟显示器驱动 `OrayIddDriver`（向日葵）与 `Todesk Virtual Display Adapter`，但无活动分辨率的虚拟显示器

**结论**：真并行只能走 VM；近期聚焦「语义化 + 租约 + Agent 光标窗口」。

### 4.2 四条硬事实

1. **桌面 2（CreateDesktop）只能藏，不能并行**。每个会话只有一个桌面收硬件输入，`SendInput` 只进前台队列；跨桌面投递需 `SE_TCB_NAME` 权限且 LUID 匹配。
2. **虚拟显示器只解决渲染与占屏，不解决输入互斥**。多一块屏不等于多根指针。
3. **虚拟键鼠造不出多根系统指针**。软件层的键鼠驱动仍然汇入同一根系统指针与同一个前台队列。
4. **本机的分层窗口（`WS_EX_LAYERED`）不会被 DWM 合成到屏幕上**（2026-11-09 实测）。
   - 验证方式：`ULW_ALPHA`（三种调用顺序：先 `ShowWindow` 再设 ULW / 先设 ULW 再 `ShowWindow` / 设 ULW 后 `SetWindowPos` + `SWP_FRAMECHANGED`）、`ULW_COLORKEY`、`SetLayeredWindowAttributes` 的 `LWA_ALPHA` 与 `LWA_COLORKEY`、带与不带 `HWND_TOPMOST`——**全部无效**。
   - 取景三方交叉验证一致：GDI `BitBlt`（`SRCCOPY|CAPTUREBLT`）、Pillow `ImageGrab`、DXGI 桌面复制（dxcam 0.3.0，output 0，2240x1400）——都看不到分层窗口。
   - 对照组：同一台机器上**普通（非分层）顶层窗口合成完全正常**——100x60 纯色窗实测 6000/6000 像素全中。
   - 结论：**不是"取景方式不对"，是真的没被合成**。
   - **对 L0 的直接影响**：L0 仍然可行——语义调用（UIA Invoke、原生接口）不依赖任何窗口合成。但 **L0 的视觉层不能用分层窗口实现**：本机做 Agent 光标窗口必须走**非分层顶层窗口 + 窗口区域（`SetWindowRgn`）**路线。
   - **可能相关的线索（未证实因果）**：本机在册 `OrayIddDriver`（向日葵）与 `Todesk Virtual Display Adapter` 两个虚拟显示器驱动，且 AMD 独显 2240x1400 是唯一活动输出（dxcam 只枚举到 output 0）。这**可能**与 DWM 的合成分支有关，但**目前没有证据支持因果关系**，只作为线索记录。

### 4.3 由现状推出的取舍

- **L3 在本机只能解决"别打扰用户"，解决不了"真并行"**。所以 §2.1 的真并行触发只能落到 L4，没有中间选项。
- **L4 目前没有现成设施**：Hyper-V 未装、`vmms` 未注册、无 docker / VBox / qemu，13.9 GB 内存再挤一台 VM 会与用户自己的应用抢资源。L4 定为"条件具备时再上"，近期不投入。
- **近期主战场是 L0–L2**，正好是本方向已实现的三级：把语义化命中率拉高、把物理输入串行化、把 Agent 光标窗口做好，能推迟甚至避免升级。但 L2 在本机要先解决"分层窗口不被合成"这一条（§4.2 第 4 条），否则它连验收都过不了。
- **WSL2 存在且已装 Ubuntu-22.04**（当前 Stopped），可作为将来"无头隔离"（L5）的候选载体，但那是另一个方向的议题。

## 5. 不要做什么

- **不要用 RawInput 多鼠标自绘冒充多根指针**。RawInput 是"读"不是"造"；自绘出来的是画面，不是输入，用户的指针还是只有一根。
- **不要为省事抢前台**。抢前台就是打扰用户；预算机制的存在正是为了让"抢"成为记账在案的例外，而不是默认路径。
- **不要在隔离环境里仍然共享同一份输入**。两个环境共用同一根系统指针，等于没有隔离，只是换了个说法。
- **不要把"多显示器"当成"多根指针"**。第二块屏换了显示输出，没换输入队列。
- **不要用虚拟显示器冒充并行方案**（§4.2 第 2 条）。
- **不要把桌面 2 当成隔离方案**。它连"藏"都要 `SE_TCB_NAME` + LUID 匹配，且只藏不并。
- **不要在降级未确认前把判定清零**。一次偶然成功不等于恢复（§3.3）。
- **不要在没有实测数字时说"已经隔离"**。本文档与 `reasons` 的规矩一致：结论要带数字。
- **不要只凭一种取景方式就断定窗口没被合成**。分层窗口那条结论是靠 GDI、Pillow、DXGI 三方交叉验证加普通窗口对照组得出的；只测一种取景方式，容易把"取景方式不对"误判成"没被合成"。
- **不要用分层窗口做 Agent 光标窗口**（本机）。可行路线是非分层顶层窗口 + `SetWindowRgn`。

## 6. 与既有接口的衔接

```python
from openeyes.agent.isolation import evaluate_isolation

verdict = evaluate_isolation(
    needs_parallel=len(writers) > 1,
    semantic_miss_rate=misses / max(1, attempts),
    budget_exhausted=budget.refused,
    concurrent_writers=len(writers),
)
if verdict.escalate:
    log(verdict.to_dict())          # {'escalate': True, 'level': 'vm', 'reasons': [...]}
    for reason in verdict.reasons:
        tell_the_user(reason)        # 原样转述，不要转述成"建议换环境"
```

要点：

- 三个度量分别来自动作账本与 resolve 轨迹、**打扰预算**（`FocusBudget.refused` / `AttentionBudget.disturbance`，见 §2.3）、租约等待方；本模块不读盘、不读屏，只吃数字。
- `LEASE_TIMEOUT` 只说明有争抢，不等于真并行——只有 `needs_parallel` 成立时它才计入 `concurrent_writers`。
- `reasons` 是给人看的：它已经是完整句子，调用方不要再翻译成"建议换环境"这种没有信息量的话。
- 判定是只读的、可重复的：同一组输入永远得到同一个结论，便于复盘。

## 7. 相关文件

| 文件 | 作用 |
| --- | --- |
| `openeyes/agent/isolation.py` | 隔离判定：`IsolationConfig`、`IsolationVerdict`、`evaluate_isolation` |
| `openeyes/agent/profile.py` | `AgentProfile`、`FocusBudget`（预算口径来源） |
| `openeyes/agent/lease.py` | `InteractionLease`（跨进程物理输入租约） |
| `openeyes/agent/ledger.py` | `ActionLedger`（动作与打扰的记账，升级/降级都应入账） |
| `openeyes/agent/errors.py` | 结构化错误码：语义失效率与预算耗尽的判定依据 |
| `openeyes/agent/overlay.py` | `CursorOverlay`（L2 Agent 光标窗口；现为分层窗口实现，本机不可见，见 §4.2） |
| `openeyes/agent/attention.py` | `AttentionBudget`（打断预算；`disturbance` 是隔离层的度量来源，`refused` 是它的超集口径：预算从未发放时的拒绝也算在其中） |
| `openeyes/agent/scheduler.py` | `Scheduler`（Agent 之间的目标争抢与抢占；冲突数**不**计入打扰预算） |
| `openeyes/agent/presence.py` | `AgentPresence`（把注意力预算与光标窗口粘在一起；其 `refused` 只统计"想被看见却被挡下"，与 `AttentionBudget.refused` 同源，不作隔离度量） |
| `docs/capability-contract.md` | 工具契约与投递阶梯（`invoke → message → pixel`） |
| `docs/isolation-policy.md` | 本文 |
