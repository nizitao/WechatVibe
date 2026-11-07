# 关系分类训练：relationship-nb

> 2026-11-01 起，本地分析的 relationship 信号由这个训练模型提供，不再依赖 Laya 的关系问题。
> 这是独立的小模型，**没有微调 Laya ONNX 权重**。

## 1. 模型

| 项 | 值 |
| --- | --- |
| 算法 | Multinomial Naive Bayes |
| 特征 | Unicode 字符 1–3 gram，去掉空白 |
| 训练集 | `split=train`，355 条 |
| 验证集 | `split=validation`，96 条 |
| 测试集 | `split=test`，100 条（20/类） |
| 平滑 alpha | 1.0 |
| 概率温度 | 1.5（仅验证集拟合，不改变 argmax） |
| 模型版本 | `relationship-nb-v1-f2ff39cc` |
| 产物 | `electron/laya/relationship-nb-model.json` |
| 推理 | `electron/laya/relationship-nb.ts` |
| 训练 | `scripts/train-relationship-nb.py` |
| 报告 | `docs/laya-relationship-nb-report.json` |

## 2. 训练方法

1. 在 train 上做 5 折交叉验证，候选 alpha `0.5–2.0`；alpha 1.0 的 CV 准确率最高（87.0%）。
2. 用 alpha 1.0 在 train 上拟合；validation 始终不参与参数选择。
3. 在 validation 上拟合温度缩放（1.5），降低 NB 概率过度自信；温度不改变分类。
4. test 只评估一次，准确率未达 90% 时脚本返回非零退出码。

这里的 CV 低于 holdout 不是矛盾：每折只有约 284 条训练样本，而最终模型使用全部 355 条；
不同折的类别分布也不同。最终发布看 validation + test。

## 3. 结果

| 集合 | 正确 | 准确率 | 宏召回 |
| --- | ---: | ---: | ---: |
| validation | 91/96 | **94.8%** | 94.6% |
| test | 95/100 | **95.0%** | **95.0%** |

test 分类别：

| 类别 | 正确 | 召回 |
| --- | ---: | ---: |
| rejecting | 20/20 | 100% |
| distant | 18/20 | 90% |
| neutral | 19/20 | 95% |
| warm | 18/20 | 90% |
| romantic | 20/20 | 100% |

混淆：

- distant → rejecting：2
- neutral → romantic：1
- warm → romantic：2

概率校准：validation NLL 0.163，Brier 0.082。

## 4. 语料

- 总量 551 条，全部手写合成，不含用户聊天。
- 训练 355 / 验证 96 / 测试 100。
- test 每类严格 20 条，旧 20 条保留，新增 80 条。
- 新增 test 与其余语料最高相似度 0.727，平均最近邻 0.52，无 ≥0.75 近似对。
- 根据验证集错误补过 11 条 train，只改 train，不碰 test。

## 5. 生产接入

本地分析链：

```
classifyMessage(targetText)
  → relationshipTextProbabilities(text)
  → relationship distribution
  → messageScore → affinity-v2
```

覆盖：

- 普通 OTHER 消息；
- `messageLabelsOnly` 细标签；
- 批量分析。

API 画像仍使用用户配置的 API 模型，不被本地 NB 覆盖。

缓存：

- `ANALYSIS_VERSION` 已包含模型版本 `relationship-nb-v1-f2ff39cc`；
- 模型或语料变化后，训练脚本会生成新版本并自动失效旧缓存；
- `tests/relationship-nb.test.ts` 校验模型内的语料 SHA-256 与当前 JSONL 一致。

发布白名单：

- `scripts/stage-real-client.py` 的 `LAYA` 已加入 `relationship-nb.ts` 与模型 JSON。

## 6. 复现

```bash
# 训练 + 验证 + test 闸门
npm run train:relationship-nb

# 只复核已发布模型
python scripts/train-relationship-nb.py --check

# 模型/语料一致性、TS 推理与 90% 闸门
node --import tsx --test tests/relationship-nb.test.ts tests/laya-affinity-corpus.test.ts

npm run typecheck
npm run test:node
```

## 7. 边界

- 95% 是**合成语料 test** 的结果，不等于真实微信聊天准确率。
- 字符 n-gram 依赖措辞；遇到反讽、上下文指代、长消息可能退化。
- 当前 test 只有 100 条，单条价值 1 个百分点；扩大到数百条真实人工标注前，不应把 95% 当成最终泛化指标。
- 下一阶段应采集用户明确授权的真实消息，保持与训练集隔离，重新跑同一训练脚本和 90% 闸门。
