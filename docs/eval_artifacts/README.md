# 可公开核验的评测快照

本目录保存冻结版四组评测的逐 trial 评分报告及 `needs_review` 证据队列：72 道开发题、20 道留出题；Agent 与同模型单轮 Text-to-SQL 基线各重复 3 次，共 552 次。报告从本机已保存的 Trace/原预测离线重评后导出，发布时仅把 Agent 报告中的本机绝对 `trace_store` 路径改成相对路径说明；评分、模型信息、题集及源码指纹、试验 ID、耗时与 token 未修改。可将报告逐项与 [`live_eval_report.md`](../live_eval_report.md) 中的数字核对。

`*_review_queue.md` 包含问题、金标或 rubric、模型回答、所引 SQL/结果和空白审核模板。它们是**待审核材料**，不是人工通过记录。当前所有最终文字和行为题尚未由人签署审查；不能从 SQL 匹配推算任务完成率。

本目录不含原始 SQLite Trace、完整检查点、模型原始预测、API 密钥或 194 MB 的 DuckDB 快照。Trace 与预测在本机被忽略的 `runtime/` 中，用于生成和无模型重评上述报告；公开目录不能单独复跑 `eval rescore`。数据源及重建方法见 [`data/README.md`](../../data/README.md)，三条脱敏示例 Trace 见 [`docs/traces/`](../traces/)。未来重新评测时应生成新批次和新留出题，不覆盖这组冻结结果。
