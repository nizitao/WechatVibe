# 关系分类训练：relationship-linear（v2）

> 2026-11-07 起，本地分析的 relationship 信号由这个训练模型提供。
> v1 多项式朴素贝叶斯（`relationship-nb`）已退役，相关文件已删除。
> 这是独立的小模型，**没有微调 Laya ONNX 权重**。

## 1. 模型

| 项 | 值 |
| --- | --- |
| 算法 | 多项逻辑回归（full-batch GD + L2 + 幅度剪枝） |
| 特征 | 带边界符 `^…$` 的 Unicode 字符 1–4 gram，二值出现特征 |
| 训练集 | `split=train`，495 条 |
| 验证集 | `split=validation`，136 条 |
| 测试集 | `split=test`，300 条（60/类） |
| L2 / epochs / 剪枝 | 0.001 / 1200 / \|w\| < 0.02 置零 |
| 概率温度 | 0.35（仅验证集拟合，不改变 argmax） |
| 词表 / 导出权重 | 12,498 gram / 10,578 个非零 gram 条目 |
| 模型版本 | `relationship-linear-v2-35a840fb` |
| 产物 | `electron/laya/relationship-linear-model.json`（447 KB） |
| 推理 | `electron/laya/relationship-linear.ts` |
| 训练 | `scripts/train-relationship-linear.py` |
| 报告 | `docs/laya-relationship-linear-report.json` |

## 2. 为什么换掉 v1 NB

| 对比 | v1 NB（旧） | v2 线性 |
| --- | --- | --- |
| 特征 | 字符 1–3 gram，无边界 | 字符 1–4 gram + 句首句尾边界 |
| train 5 折 CV（同一 415 条语料） | 87.0% | 89.4% |
| validation（当时 96 条） | 94.8% | 97.4% |
| 扩到 200 条独立 test | 旧 NB 口径 91% 以下 | 91.0% |

NB 的 `-n·log(den)` 长度项和偏向 warm/romantic 的类别先验，在句式变长、类别均衡的
评估集上会系统性偏向多数类。判别式 LR 直接学决策边界，且边界符能捕捉句首/句尾语气。

v1 的 95%（100 条 test）是较小评估集上的历史数字，**不能与 v2 的 300 条 test 直接比较**。

## 3. 训练方法

1. 在 train 上做 5 折交叉验证选 L2：`1e-4 / 3e-4 / 1e-3`。本轮三档 CV 同为 88.48%，
   平手时选更强正则。epochs 与剪枝阈值由上一轮网格搜索固定，不在本轮重选。
2. 用选中的配置在 train 上拟合；**validation 只用于温度缩放**，不参与特征或超参选择。
3. 训练后按 `|w| < 0.02` 剪枝并导出保留 6 位小数的权重。所有指标都用导出后的
   float64 权重重算，因此 Python 报告、`--check` 与 TypeScript 推理完全一致。
4. test 一次性评估，默认闸门 ≥90%，未达标脚本返回非零退出码。

分类别平衡训练（`class_weight`）在 train CV 上无收益、validation 下降 1–2 个百分点，
因此未启用。详见 `scripts/train-relationship-linear.py` 的方法说明。

## 4. 结果

| 集合 | 正确 | 准确率 | 宏召回 |
| --- | ---: | ---: | ---: |
| validation | 128/136 | 94.12% | 94.17% |
| **test（全部 300 条）** | **272/300** | **90.67%** | **90.67%** |

test 分层（`docs/laya-relationship-linear-report.json` 的 `test.byBatch`）：

| 批次 | 条数 | 正确 | 准确率 | 用途 |
| --- | ---: | ---: | ---: | --- |
| 早期批次（原始 + r4 + r6） | 200 | 181 | 90.50% | 历史对照 |
| r7 | 50 | 46 | 92.00% | 开发测试，曾用于发现含蓄表达缺口 |
| **r8** | **50** | **45** | **90.00%** | **最终盲测，从未参与选型** |

分类别（test 300 条）：

| 类别 | 正确 | 召回 |
| --- | ---: | ---: |
| rejecting | 48/60 | 80.0% |
| distant | 55/60 | 91.7% |
| neutral | 59/60 | 98.3% |
| warm | 57/60 | 95.0% |
| romantic | 53/60 | 88.3% |

主要混淆：rejecting → distant 7 条、romantic → warm 4 条。都是**不含强关键词的
含蓄表达**（如间接回绝、间接表白），仍是词面模型的固有弱点。

概率校准：validation NLL 0.145，Brier 0.070。

## 5. 语料与迭代过程

`tests/fixtures/laya-affinity-corpus.jsonl`，931 条，全部手写合成，不含用户聊天：
train 495 / validation 136 / test 300，test 每类严格 60 条。
详见 `docs/laya-scoring-calibration.md` §2。

本轮（2026-11-07）三次迭代：

| 步骤 | 动作 | 结果 |
| --- | --- | --- |
| 1 | 换 LR + 边界 1–4 gram，语料 551 → 731（新增 train 60 / val 20 / 独立 test 100） | test 200 条 91.0%，validation 97.4% |
| 2 | 新增 r7 盲测 50 条 | **r7 只有 80%**，整体 88.8%，闸门失败；定位到含蓄 romantic/rejecting 误判 |
| 3 | 按 train/validation 侧补 80 条 train + 20 条 validation 弱关键词语料；新增 r8 盲测 50 条 | test 300 条 90.67%，r8 盲测 90%，闸门通过 |

独立性校验：

- r8 与其余 881 条最高 `SequenceMatcher` 相似度 **0.700**，平均最近邻 **0.541**，无 ≥0.75 近似对。
- r7 与其余语料最高 0.696、平均 0.509。
- 全库字符二元组 Jaccard 最高 **0.538**，测试闸门为 < 0.65。
- 文本全局唯一；`tests/laya-affinity-corpus.test.ts` 固定每类计数、唯一性、无 split 泄漏和近重复闸门。

## 6. 生产接入

```
classifyMessage(targetText)
  → relationshipTextProbabilities(text)   # electron/laya/relationship-linear.ts
  → relationship distribution
  → messageScore → affinity-v2
```

- 普通 OTHER 消息、`messageLabelsOnly` 细标签、批量分析统一使用该分布。
- `electron/analysis.ts` 的 `ANALYSIS_VERSION` 纳入 `RELATIONSHIP_LINEAR_VERSION`，
  模型版本变化后旧缓存自动失效。
- `scripts/stage-real-client.py` 的 `LAYA` 白名单已换成
  `relationship-linear.ts` 与 `relationship-linear-model.json`，发布包不会漏文件。
- 概率经温度 0.35 缩放，直接进入 `relationshipConfidence` 与好感度聚合。

## 7. 复现

```bash
# 重训（需要 numpy；系统 Python 与 .venv 均已安装）
npm run train:relationship-linear

# 只验证已发布模型与 90% 闸门
python scripts/train-relationship-linear.py --check

# 模型契约、语料均衡与近重复
node --import tsx --test tests/relationship-linear.test.ts tests/laya-affinity-corpus.test.ts
```

## 8. 验证记录（2026-11-07）

| 检查 | 结果 |
| --- | --- |
| `npm run typecheck` | 通过 |
| 定向 Node 测试（模型 + 语料） | 4/4 |
| 模型 90% 闸门 `--check` | 272/300 = 90.67%，通过 |
| 发布清单 `test-build-portable-clean.py` | 10/10 |
| 脚本入口 `bridge/test_script_entrypoints.py` | 3/3 |
| 真实 Laya 中文推理冒烟 | 通过，`affinity: 69` |
| 完整 Node 套件 | 763 项中 759 通过；4 个失败文件均含未解决的 `<<<<<<< HEAD` 合并冲突，与本模型无关 |
| 完整 Python 套件 | 被 `scripts/start-real-client.py` 的同一合并冲突中断，与本模型无关 |

> 仓库当前有 22 个未解决的合并冲突文件。合并冲突修复后需重跑完整套件。

## 9. 局限与下一步

1. 90.67% 是**合成语料 test** 的成绩，不等于真实微信聊天准确率。
2. 字符 n-gram 对措辞依赖强；含蓄拒绝/表白召回明显偏低（rejecting 80%）。
3. 继续调权重收益有限。下一步应建立**用户明确授权的真实聊天标注集**，
   保持 train/validation/test 隔离，再跑同一脚本和 90% 闸门。
4. 若真实语料仍卡在含蓄表达，可考虑一个小型语义编码器（bge-m3 / MiniLM）
   冻结 embedding + 线性头，或用它给本模型做特征增强；这仍不是微调 Laya ONNX。
