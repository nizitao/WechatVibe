# Laya 好感度评分：诊断、训练与验证

结论：**没有微调 Laya 的 ONNX 权重**。仓库没有训练侧工程、优化器和标注集，`model.onnx`
是按 SHA-256 固定的推理产物。本次训练的是“关系概率 → 0~100 好感度”的评分头，以及关系分类
问题的提示词；两者都用同一份手写标注语料做选择，并在独立测试集上只评估一次。

## 1. 修改前的问题

真实模型探针与 100 条语料暴露了三类错误：

| 表现 | 例子 | 修改前 |
| --- | --- | --- |
| 普通事务被判成冷淡/拒绝 | “会议改到下午三点” | 31 分，distant |
| 普通事务被判成拒绝 | “快递放前台了” | 15 分，rejecting |
| 朴素拒绝被判成疏离 | “我们到此为止吧” | 4 分，但部分隐含拒绝只到 distant |
| 暧昧/求爱被判成友善 | “你愿意做我女朋友吗” | 81 分，刚过 romantic 下限 |
| 含蓄暧昧被判成友善 | “今晚的月色很美，可我一直在想你” | 74 分，warm |

这些误差不在 `RELATIONSHIP_WEIGHT`。调高 `romantic` 权重同时会抬高 warm，不能把
`warm 0.58 / romantic 0.23` 的分布和真正的 `romantic 0.73` 分开。根本问题是关系分类本身，
其次才是时间聚合。

旧版聚合还有两个缺陷：

1. **历史惯性**：线性权重 `0.5 → 1.0`，45 条消息里最老的 40 条仍占约 86% 权重。
   40 条友好后接 5 条拒绝只从 63 降到 57。
2. **置信度平权**：近均匀分布与高置信分布等权，模糊消息把分数拉回中性。
3. **双实现不一致**：TypeScript 已用指数聚合，Python 持久化仍用线性公式，界面读取后者。

## 2. 手写训练语料

`tests/fixtures/laya-affinity-corpus.jsonl`，551 条中文消息，**全部手写合成，不含用户聊天**：

| 类别 | 每类条数 | 每类 train / validation / test |
| --- | ---: | --- |
| rejecting | 92 | 56 / 16 / 20 |
| distant | 96 | 60 / 16 / 20 |
| neutral | 94 | 58 / 16 / 20 |
| warm | 136 | 92 / 24 / 20 |
| romantic | 133 | 89 / 24 / 20 |
| 合计 | 551 | 355 / 96 / 100 |

2026-11-01 分四轮扩充：

1. 每类 20 → 56 条（总 100 → 280）；train 40、validation 12，test 4 冻结。
2. 每类 56 → 76 条（总 280 → 380），追加 100 条；只加 train 80 + validation 20。
3. 正面增补（总 380 → 460），追加 **warm 40 + romantic 40**；只加 train 64 + validation 16。
4. 独立测试集与边界修正（总 460 → 551）：
   - 新增 test 80 条（每类 16），旧 test 20 条保留，test 合计 100 条；
   - 根据 validation 错误补 11 条 train：distant 4、neutral 2、warm 4、romantic 1。

第三轮**有意打破五类等量**：用户要求补充正面语料，因此不再同时堆叠拒绝/疏离样本。
最终训练集正面类 181/355（51%）；test 仍严格 20/类，不受训练集偏斜影响。

第三轮新增覆盖：

| 类别 | 新增场景 |
| --- | --- |
| warm | 替班、协作收尾、资料整理、方案讲解、带路、照顾家中植物、聚餐安排、耐心倾听、工作把关 |
| romantic | 周末与节日、分享好消息、共同旅行、见家人、挑房、养宠物、日常早安、余生陪伴、未来规划 |

前两轮的新增覆盖：

| 类别 | 新增场景 |
| --- | --- |
| rejecting | 退回礼物、搬家断联、拒绝复合、已有伴侣、托人传话、楼下蹲守、社交圈切割 |
| distant | 退出聚会、降低聊天频率、只保留同事关系、需要独处、延迟回应、暂停周末安排 |
| neutral | 取件码、燃气费、排班、消防演练、车次/登机口、体检报告、物业费、会议链接 |

近重复校验：

- 第三轮 80 条与原有 380 条：最高 `SequenceMatcher` 相似度 **0.727**，平均最近邻 **0.557**。
- 第四轮 80 条 test 与其余 471 条：最高相似度 **0.727**，平均最近邻 **0.52**，
  没有 ≥0.75 的近似对，新增文本 **52.7%** 的字符二元组此前未出现。

文本全局唯一；测试加入字符二元组 Jaccard < 0.65 的近重复闸门，并为每类设置独立数量断言。

> 训练集正负样本不再等量，`scripts/laya-score-train.ts` 的提示词选择使用
> **5 类宏平均区间命中 + 宏平均召回**，避免 warm/romantic 多数类带来虚假优势。
> test 严格 20/类，普通准确率与宏平均等价。

每条包含 `id`、`text`、`label`、`target`（目标分）和 `split`。语料刻意覆盖容易混淆的边界：
事务通知、简短确认、礼貌拒绝、减少互动的疏离、含蓄暧昧。它不是心理测量数据，只用于工程标定。

> 注意：下文 §5-§6 的 Laya 提示词实测指标来自 280 条语料池，是历史对照。
> 当前生产关系分类已换成训练好的 `relationship-nb`（551 条语料，test 95%），
> 见 `docs/laya-relationship-nb.md`。不要再把 280 条时代的 55% 当作当前准确率。

## 3. 训练方法

脚本：`scripts/laya-score-train.ts`

1. 对每个候选提示词各跑 551 条真实 Laya 推理；
2. 按 **train** 的区间命中、分类准确率、MAE 选提示词；
3. 用选中的提示词构造 10 个对话场景，在 **validation** 上选半衰期；
4. 用 **test 只评估最终的一组配置**，不做二次选择。

场景覆盖：友好后拒绝、冷淡后热恋、忽冷忽热、纯冷淡、纯友好、纯拒绝、长期友好后新拒绝等。
提示词候选：旧英文 baseline、现网中文 production-v2、英文 guardrails、英文 precise，以及 §6.1 的
边界候选。所有候选都保留在脚本中，便于以后用真实标注集重跑。

训练命令：

```bash
# CPU
npm run train:laya-score

# Windows 显卡（WebGPU）
node --import tsx scripts/laya-score-train.ts --provider=webgpu

# 只校验语料均衡，不加载模型
node --import tsx scripts/laya-score-train.ts --dry-run
```

## 4. 实际改动

- `electron/laya/options.ts`：关系题改为中文、加入“先判明确拒绝，再判明确爱慕，最后区分事务与疏离”
  的顺序规则；导出 `RELATIONSHIP_QUESTION_VERSION=relationship-v2`。
- `electron/analysis.ts`：`ANALYSIS_VERSION` 纳入关系题版本和评分头版本，旧缓存自动失效。
- `electron/laya/scoring.ts`：指数半衰期从 10 条调到 **8 条**，仍保留置信度加权；无权重下限。
- `electron/api-portrait-classifier.ts`：API 画像输出 `scoreConfidence`，分类器版本纳入关系题版本。
- Python 全链路统一：
  - `profile_state.py`：`scoreExp = Σ score·confidence·r^age`、`confidenceExp = Σ confidence·r^age`；
  - `batch_state.py`：逐条、批量、乱序 tail 都用闭式更新；
  - `result_store.py`：`summary_v1` 新增 `score_exp` / `confidence_exp`，旧汇总自动重建一次；
  - `api_portrait_statistics.py`：统计版本升到 2，接收置信度并校验新字段；
  - `backend_contracts.py`：新状态用 `scoreExp / confidenceExp`，旧状态回退 v1，不混算。
- `message_results.py`：保留关系分布，供 Python 计算置信度；旧响应缺该字段时按置信度 1 兼容。

TypeScript 与 Python 的公式现在相同（无 floor）：

```text
r = 0.5 ** (1 / 8)
weighted = Σ score·confidence·r^age / Σ confidence·r^age
affinity = round(50 + 50·clamp(weighted, -1, 1))
```

乱序插入的历史消息也用闭式更新，不需要重扫全部历史：

```text
state' = r^m · state + (1 - r^m) · tail + new_block
```

## 5. 优化前后对比

最终测试集（20 条，未参与选择）：

| 配置 | 区间命中 | 分类准确率 | MAE | 10 个对话场景命中 |
| --- | ---: | ---: | ---: | ---: |
| v1：baseline + 线性权重 | 60~65% | 45% | 13.10~13.15 | 60% |
| v2：production + 半衰期 8 | **70%** | **55%** | **11.60** | **70%** |

> 前四列在 280 条语料上复跑仍是 70% / 55% / 11.600。**场景列只在当次语料内可比**：场景由 train 行
> 循环拼接，train 从每类 12 条扩到 40 条后内容已变，v2 重算为 60%，不是回归（§6.4）。

提示词在训练集上的变化：

| 提示词 | 训练区间 | 训练准确率 | 训练 MAE |
| --- | ---: | ---: | ---: |
| baseline | 65.0% | 60.0% | 11.50 |
| production | **75.0%** | **68.3~70.0%** | **9.68** |
| guardrails | 75.0% | 66.7% | 9.68 |
| precise | 70.0% | 70.0% | 10.38 |

production 与 guardrails 训练区间相同，production 分类准确率更高，选 production。验证集上
production 区间 70%、准确率 65%。

离线分布场景（`scripts/laya-score-eval.ts`）：

| 指标 | v1 | v2 |
| --- | ---: | ---: |
| 12 场景区间命中 | 10/12 | **12/12** |
| pairwise 排序一致率 | 77.8% | **94.4%** |
| 友好后追加 3 条拒绝 | −4 | **−8** |
| 暧昧后追加 20 条低置信噪声 | −10 | **−8** |

（最后一行 2026-11-01 用当前 `scoring.ts` 复跑为 −8；本文早先记录的 −6 是旧评分版本的数值，已更正。）

半衰期选择：validation 上 6 和 8 都是 80% 命中；6 在低置信噪声场景掉到 52 分并越过档位，
8 保持 54 分且离线 12/12，因此同分时选更长的 8。

## 6. 仍未解决

最终测试集 9 个分类错误：

- 4 条明确暧昧全部被判 `warm`，分数 74~77；“愿意一起过七夕”“想陪你到老”也偏保守。
- 2 条拒绝被判 `distant`，分数 27/28，虽仍在 rejecting 分数带内，但标签不对。
- 2 条疏离被判 `neutral`，分数 48/55。
- 1 条航班延误被判 `distant`，分数 30。

这些是分类器语义边界问题，继续调聚合权重会伤害其他类别。下一步应扩大真实人工标注集，
再决定是否用小型可训练关系分类器替换这一条问题；那属于新模型训练，不是本次评分头标定。

### 6.1 提示词边界实验：已饱和（100 条语料时代）

> 本节数字来自**每类 20 条**的旧语料；扩到 280 条后重跑的结果见 §6.3，结论不同（v3 曾短暂胜出又被 test 否决）。

对上述四类边界各加一条规则，共 10 个候选，**只用 train 选择、validation 确认，test 未参与**
（`--train-only`，因此本节没有新的 test 数字）：

| 候选 | train 区间 | train 准确率 | train MAE | val 区间 | val 准确率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| hard-reject | 0.767 | 0.667 | 9.750 | 0.700 | 0.600 |
| production-v2 | 0.750 | 0.683 | 9.683 | 0.700 | 0.650 |
| no-warm-affection | 0.750 | 0.633 | 10.883 | 0.700 | 0.600 |
| implicit-romance | 0.733 | 0.700 | 10.750 | 0.800 | 0.650 |
| pure-logistics | 0.733 | 0.683 | 10.517 | 0.600 | 0.500 |
| boundary-all（被截断） | 0.717 | 0.700 | 9.350 | 0.750 | 0.650 |
| order-reversed | 0.717 | 0.683 | 10.117 | 0.700 | 0.650 |
| space-request | 0.683 | 0.583 | 11.500 | 0.650 | 0.600 |
| compact-v3 | 0.667 | 0.683 | 10.700 | 0.750 | 0.650 |
| baseline | 0.650 | 0.600 | 11.567 | 0.650 | 0.650 |

结论：**没有采纳任何新提示词**。最好的 hard-reject 只多命中 1 条（45→46/60），验证准确率反而
0.650→0.600、MAE 9.683→9.750，落在 60 条样本的噪声内；`compact-v3` 用同等 token 预算覆盖了全部
四类边界，train 区间仍掉到 0.667。生产提示词保持 `relationship-v2`，test 因此没有重跑。

### 6.2 指令 head 预算：256 token 由指令和选项共享

`buildPrefix`（`electron/laya/prompt.ts`）把 `<type> question: <instructions>` 截到
`head_max_len - 选项 token`，**超出即从尾部静默截断**，追加在末尾的规则根本不会进模型。
`head_max_len = 256`，`relationship-v2` 已用 176 + 10（选项），余量约 70 token。

这直接解释了三次无效实验：

- `boundary-all` 477 字符 → 262 token，被截到 246，正好丢掉末尾的 romantic/warm 规则；
- 给五个选项加中文释义后选项占 106 token，指令预算从 256 掉到 150，`checklist-v3` 与
  `gloss+no-warm` 实际只喂了半段指令——与 `options.ts` 里"per-option 描述会回归"的旧结论
  一样不可信，因为那次很可能也是被截断。

现网题目余量（`--budget` 输出）：

| 题目 | 指令 token | 可用 | 选项 token |
| --- | ---: | ---: | ---: |
| emotion | 185 | 194 | 62 |
| intent | 57 | 226 | 30 |
| relationship | 176 | 245 | 10 |
| self_quality | 32 | 192 | 64 |

`emotion` 只剩 9 token 余量，任何追加都会静默丢尾。改提示词前先跑
`node --import tsx scripts/laya-score-train.ts --budget`。

### 6.3 语料扩到 280 条后：v3 候选被 test 否决

2026-11-01 把语料从每类 20 条扩到 56 条（train 40 / validation 12 / **test 仍为 4，冻结不动**），
共 280 条，train 200 / validation 60 / test 20。test 行完全没变，所以下面的 test 数字与旧版本直接可比。

在 200 train / 60 validation 上，`implicit-romance`（即 v3）**五个指标全部优于 v2**：

| 候选 | train 区间 | train 准确率 | train MAE | val 区间 | val 准确率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| hard-reject | 0.720 | 0.640 | 11.270 | 0.617 | 0.533 |
| **implicit-romance（v3）** | 0.705 | **0.660** | **11.785** | **0.700** | **0.600** |
| production-v2 | 0.695 | 0.630 | 12.030 | 0.650 | 0.583 |

按"train 选择、validation 确认"：train 区间最高的 `hard-reject` 在 validation 上全面退步
（0.720→0.617、0.640→0.533），确认失败；顺位到 v3，验证通过。于是把 v3 写进
`electron/laya/options.ts`（`RELATIONSHIP_QUESTION_VERSION=relationship-v3`），跑一次 test：

| 配置 | 区间 | 分类 | MAE | 场景 |
| --- | ---: | ---: | ---: | ---: |
| v2（冻结 20 条） | 70% | **55%** | **11.60** | 70%\* |
| v3 | 70% | 45% | 12.00 | 90%\* |

\* 场景命中由 train 行循环拼接，语料扩容后样本池变了，**这一列与旧值不可比**；只有前 20 条
test 的区间/分类/MAE 可比。

结论：**回退到 v2**。v3 的"邀请一起过节或单独约会→romantic"规则在没见过的纯事务请求上过度触发：
`你到了给我发个位置。`→romantic 81、`密码已经重置。`→rejecting 36、`明天上午我有空。`→warm 72，
4 条 test neutral 全错。romantic 侧只把 `我对你不只是朋友的感觉。`从 77 抬到 86，仍未改变分类。

教训：**200 条 train 上"五个指标全胜"仍然可以完全不迁移到 20 条 test。** 扩大评估集提高了分辨率，
但没有消除规则过拟合；test 作为发布闸门不可省。

### 6.4 半衰期在扩容后不再被验证集区分

280 条语料下，验证集场景命中 4/6/8/10/12/16 **六条半衰期全部是 80%**——10 个场景的分辨率就是 10%，
"同分取更长"于是机械地给出 16。生产值仍是 **8**，依据是 `scripts/laya-score-eval.ts` 的离线噪声场景
（6 掉到 52 分，8 保持 54 分），而不是这张表。

脚本现在会在同分时打印提示。**这一列不要跨语料版本比较**：场景由 train 行循环拼接，train 从每类
12 条扩到 40 条后，场景内容本身就换了，v2 的 test 场景命中从旧值 70% 变成 60% 不是回归。


## 7. GPU / CUDA

- 当前 Windows 的 `onnxruntime-node@1.30.0` 只提供 CPU、DirectML、WebGPU；其官方兼容表
  明确 **CUDA 仅 Linux x64**。本机没有 CUDA Toolkit，`nvcc` 也不存在。
- 实测 DirectML 在该 ONNX 图的 `Reshape` 节点失败，不能作为稳定路径。
- WebGPU 可用：100 条 × 4 提示词训练，**CPU 122.6s，WebGPU 32.2~38.0s，约 3.2~3.8×**；
  选中配置和最终测试指标一致（浮点输出有微小差异）。
- 要使用真正的 CUDA，需要在 Linux 或 Python `onnxruntime-gpu` 环境重跑；当前 Node 训练入口
  会明确拒绝 `--provider=cuda`，不会静默退回 CPU。

## 8. 验证与回滚

```bash
npm run typecheck
node --import tsx --test tests/laya-affinity-corpus.test.ts tests/laya-scoring-calibration.test.ts
.venv/Scripts/python.exe bridge/test_affinity_calibration.py
.venv/Scripts/python.exe bridge/test_incremental_reconciliation.py
.venv/Scripts/python.exe bridge/test_api_portrait_statistics.py
.venv/Scripts/python.exe bridge/test_message_results.py
npm run train:laya-score -- --provider=webgpu
```

本次回归：完整 Node 测试 **834/834**；完整 Python 测试 **82 个文件全部通过**
（`ALL_PYTHON_TESTS_PASSED`，约 397s），其中 `test_real_backend.py` **81/81**。

回滚：把 `AFFINITY_CALIBRATION.version` 升为新值并恢复 `halfLifeMessages: 10`；关系题改回
`relationship-v1` 英文版。Python 的 `scoreExp` 字段可保留，旧读取器会忽略未知字段。
