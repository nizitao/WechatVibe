# WechatVibe（nizitao 的个人 fork）

上游仓库：[tswawa/WechatVibe](https://github.com/tswawa/WechatVibe)。

本 fork 在上游 **v1.2.4** 的基础上，放进了我们自己提给上游的修复和一套自用改动，
方便自己构建、也给朋友用。上游的功能说明、下载安装、隐私与免责声明请以上游仓库为准。

## 我们的改动

### 一、提给上游的 PR

| PR | 内容 | 状态 |
| --- | --- | --- |
| [#13](https://github.com/tswawa/WechatVibe/pull/13) | 逐条情绪 / 意图：宽情绪题（粗粒度 19.7% 到 64.3%）、`caring` / `surprised` 选项（38.9% 到 57.2%）、悬空判断句守卫、schema `generic-v9` 到 `generic-v10`；顺带修掉 [#14](https://github.com/tswawa/WechatVibe/issues/14) 的 API 逐条标签 | **已合并**（只采纳 API 标签部分，见下） |
| [#10](https://github.com/tswawa/WechatVibe/pull/10) | 本地分析多 worker 并行 + 弹性负载调节 | **已合并**（`d5c0fbe`，随 1.2.4 发布，合入后又做了调整） |
| [#18](https://github.com/tswawa/WechatVibe/pull/18) | 服务号 / 系统会话不进会话列表（本机 203 降到 149） | **已合并**（`a9e51b0`） |
| [#19](https://github.com/tswawa/WechatVibe/pull/19) | 一键把全部会话加进侧边栏 | **已合并**（`cd61328`；上游保留「全部添加」，去掉首次启动自动全选，并修掉手动移除的会话又被加回来的问题） |
| [#20](https://github.com/tswawa/WechatVibe/pull/20) | 只读的整账号分析进度 / 耗时接口 | **已合并**（`da3cec4`） |
| [#21](https://github.com/tswawa/WechatVibe/pull/21) | 后台分析全部会话的开关、侧栏总进度与并行档位 | **已合并**（`fcdd150`；上游改成默认关闭，并修了进度显示） |
| [#17](https://github.com/tswawa/WechatVibe/pull/17) | 消息内嵌图片直接显示（点击放大） | 开放；上游表示改动面较大、后续自己做，本 fork 保留该功能 |
| [#22](https://github.com/tswawa/WechatVibe/pull/22) | 单条消息右键「重新生成测评」（只重算这一条并覆盖已保存结果） | 开放；本 fork 已并入 `main` |
| [#23](https://github.com/tswawa/WechatVibe/pull/23) | 每条消息显示选项数 1 / 2 / 3：默认 1 与上游现状逐条一致，选 2 / 3 才列出概率最高的前 N 个候选（各带百分比，与第一名接近的标「相近」）；只影响本地模型路径，不改标签 schema。定位是**给用户放权**，同时算**前几个版本多候选显示的再重构** | 开放；本 fork 已并入 `main` |
| [#31](https://github.com/tswawa/WechatVibe/pull/31) | 修 [#24](https://github.com/tswawa/WechatVibe/issues/24) 的崩溃类问题：进度记录与明细不一致时，尾巴证据的标签不再直接下标，改用 `setdefault` 兜底，整轮分析不会 KeyError 后永久失败；附 5 个复现用例（修复前 4/5 失败） | 开放；**修复分支可单独构建使用**（见下） |

**#13 的采纳情况**（上游在 1.2.3 里手工移植）：

- **采纳**：API 模式逐条标签改用短编号逐条输出（prompt 约 1000 降到 440 字节），这条同时修掉 #14；上游另补上了乱序、重复与流式结果的对应。
- **未采纳（本 fork 保留）**：逐条显示改用宽情绪题、`caring` / `surprised` 选项、标签 schema `generic-v10`。上游 1.2.4 仍未采纳这块。
- **未采纳（已丢弃）**：画像输出的容错解析。上游指出 `I:70` 会被算成偏 E 70%（方向反了），且画像已改为程序统一计分，这段逻辑不再需要。

**#10 按 review 修掉的问题**（已随上游 1.2.4 发布）：

- `subprocess` 从未导入，探测 `nvidia-smi` 一直抛 `NameError` 又被 `except` 吞掉，显卡 / 显存检测实际从未生效；
- 每次采样都新建 `psutil.Process()`，`cpu_percent(interval=None)` 恒为 0，自身占用扣不掉；
- 上限降到 0 时，**已经阻塞在 `tasks.get()` 上**的 worker 仍会取走新任务（现改为取到任务后复查上限，超限就放回队列）；
- 监控线程退出后句柄没清空，账号清除失败后 resume 时弹性模式不会重启。

服务号过滤、会话全选、统计接口与结果库 WAL 已从这个 PR 移出，分别走 #18 / #19 / #20。

### 二、自用改动（已并入 `main`）

上游 1.2.4 采纳了 #10 / #18 / #19 / #20 / #21，这几块**一律改用上游实现**，本 fork 不再维护自用版。
如今跟着 v1.2.4 走的自用改动只剩下面这些（都是上游没有的）：

- **消息内嵌图片**：直接显示、点击放大；图片密钥在后端后台派生并落盘，首次仍需在微信里点开一张图 (→ [#17](https://github.com/tswawa/WechatVibe/pull/17))；
- **聊天页「刷新」按钮**：手动重新读取会话列表、当前会话与画像；
- **标签跟随**：发起一次逐条标签后，短时间轮询几次分析读取，新消息的标签尽早出现；
- **宽情绪题 8 桶**：逐条显示用 `EMOTION_QUESTION`（`caring` 取代 `affectionate`，`surprised` 从 `amused` 拆出），schema 升到 `generic-v10` 以强制刷新旧缓存；
- **悬空判断句扩展**：`我是` / `这是` / `我这是` 这类悬空判断句不出标签。

另外，还在上游走 PR 的两块**也已经并进 `main`**：#22 单条消息右键「重新生成测评」、#23 每条消息显示选项数。它们不是 fork 独占 —— 上游一旦采纳，就按上面同一口径改用上游实现。（#17 内嵌图片虽然也开着 PR，但上游已表示自己另做，所以仍按上一条由本 fork 保留。）

`feat/selfuse-on-1.2.4` 上 `tsc`、Node 测试与 Python 全套通过。

### 三、本轮工作区改动（尚未提交）

下面两块目前只在工作区里（分支 `perf/optimize-tokenizer`），既没并入 `main` 也没开 PR：

- **手选若干条消息，只分析它们**：聊天输入框上方新增「选择消息」，进去后每条可分析的消息左侧出现勾选框，勾完点「分析选中」，就只对勾中的这几条做逐条情绪 / 意图分析——**本地 Laya 与 API 模式都支持**。选择期间会暂停「整个消息窗口的自动分析」，退出后恢复；勾选范围限在当前已加载的窗口内（最多 80 条），超出的会在工具条上标出条数。人物画像累计与沟通建议不受影响，仍按整段历史计算。隐私口径不变：手选只是把同一段窗口里被勾中的消息交给原本那条链路——本地模式仍然不出本机，API 模式向上游发送什么仍以上游说明为准，勾选不会扩大或缩小发送范围。
- **修「测试连接成功，但保存并启用失败」**：设置 → 模型来源里，编辑一条**已保存的** API 配置后点「保存并启用」，过去会被后端以 400 `invalid model source request` 拒绝，界面显示「启用失败」，而紧挨着的「测试连接」却成功。原因是它把「连接字段」和「配置 ID」塞进了同一个请求，后端规定这两者二选一（带 ID 时不重新探测，切换才够快）。现在改成先保存这条配置、再按 ID 切换。新建配置不受影响，仍是一次调用。

### 四、与上游的已知分歧

保留自用判定逻辑会带来两处可预期的不一致，下次升级上游时需要留意：

- 情绪题是 **8 个桶**（`caring` 取代 `affectionate`，`surprised` 从 `amused` 拆出），上游是 7 个。
  因此 `tests/api-portrait-classifier.test.ts` 的两处断言按本 fork 的题库调整过（59 改 60、`affectionate` 改成 `caring`）。
- 逐条显示走宽情绪题 `EMOTION_QUESTION`，上游的逐条显示仍走 18 个立场词；标签 schema 本 fork 是 `generic-v10`，上游是 `generic-v9`。

升级上游时的做法（v1.2.4 这轮实际用的）：从目标版本开分支，上游已经覆盖的功能**直接取上游**，
只按功能重贴上面这些 fork 独占块；比对时用 `git diff -w` 过滤行尾噪声，最后跑 `tsc`、Node 与 Python 全套。
v1.2.0 上那份原始补丁仍原样存档在 [`selfuse/v1.2.0`](https://github.com/silicon-sbt/WechatVibe/tree/selfuse/v1.2.0) 分支，仅作历史对照，不再维护。

## 可以单独用的修复分支（基于上游 main，不含自用改动）

这两个分支从 `origin/main`（上游）切出，各自只带一个提交、**没有夹带本 fork 的自用改动**，可以直接构建来救急；正式合并仍走上游 PR。

| 分支 | 内容 | 上游状态 |
| --- | --- | --- |
| [`fix/state-tail-keyerror`](https://github.com/silicon-sbt/WechatVibe/tree/fix/state-tail-keyerror) | 修 [#24](https://github.com/tswawa/WechatVibe/issues/24) 的三处 `KeyError`（`batch_state.py` / `profile_state.py` / `result_store.py`）：进度被清、明细还在时不再整轮失败，健康状态数值逐字节不变 | [PR #31](https://github.com/tswawa/WechatVibe/pull/31) 开放 |
| [`fix/key-scan-budget`](https://github.com/silicon-sbt/WechatVibe/tree/fix/key-scan-budget) | 修 [#30](https://github.com/tswawa/WechatVibe/issues/30)：config cipher 扫描预算从 2 GiB / 4 GiB 提到 4 GiB / 8 GiB，微信进程内存涨大后不再「账号永远未就绪」 | 未开 PR —— 留给 issue 作者提，避免撞车 |

用法：`git clone -b <分支> https://github.com/silicon-sbt/WechatVibe.git`，然后按上游 README 构建。两个修复也都已经打在本机现役安装目录里验证过（bridge `result=ready required=5 matched=5`、`/api/health state=ready`）。

## 分支

| 分支 | 内容 |
| --- | --- |
| `main` | 跟随上游 v1.2.4 + 「自用改动」 |
| `feat/selfuse-on-1.2.4` | 本轮升级分支（自用改动搬到 1.2.4） |
| `feat/selfuse-on-1.2.3` | 上一轮升级分支（已并入 `main`，历史） |
| `feat/selfuse-on-1.2.2` | 更早的升级分支（历史） |
| `selfuse/v1.2.0` | v1.2.0 + 旧的原始自用补丁（存档，不再维护） |
| `feat/inline-image` | PR #17 消息内嵌图片（已并入 `main`，PR 仍开放） |
| `feat/message-regenerate` | PR #22 单条消息重新生成（已并入 `main`，PR 开放） |
| `feat/label-option-count` | PR #23 每条消息显示选项数（已并入 `main`，PR 开放） |
| `feat/parallel-analysis-workers` | PR #10（已合并，可归档） |
| `fix/emotion-question-options` | PR #13（已合并，可归档） |
| `feat/service-account-filter` | PR #18（已合并，可归档） |
| `feat/conversation-add-all` | PR #19（已合并，可归档） |
| `feat/analysis-overview` | PR #20（已合并，可归档） |
| `feat/background-sweep-ui` | PR #21（已合并，可归档） |
| `fix/state-tail-keyerror` | PR #31（基于上游 main 的单提交，可单独构建） |
| `fix/key-scan-budget` | #30 的扫描预算修复（基于上游 main 的单提交，未开 PR） |

## 许可

沿用上游 [Apache-2.0](LICENSE) 许可证。