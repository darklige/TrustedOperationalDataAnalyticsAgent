# 可公开核验的评测快照

本目录保存冻结版四组评测的逐 trial 评分报告及 `needs_review` 证据队列：72 道开发题、20 道留出题；Agent 与同模型单轮 Text-to-SQL 基线各重复 3 次，共 552 次。报告从本机已保存的 Trace/原预测离线重评后导出，发布时仅把 Agent 报告中的本机绝对 `trace_store` 路径改成相对路径说明；评分、模型信息、题集及源码指纹、试验 ID、耗时与 token 未修改。可将报告逐项与 [`live_eval_report.md`](../live_eval_report.md) 中的数字核对。

新增的 `agent_dev_v15_report.json` 与 `agent_dev_v15_review_queue.md` 是后续改动源码 `2efce36…` 的一次完整开发集批次，共 72 道题、每题一次；它与上面的旧 552 次冻结批次分开，不覆盖历史报告。新留出集 `frozen_heldout_v2.jsonl` 已在源码提交 `6543890` 后运行，Agent 与有效单轮基线各 60 次。`agent_heldout_v2_report.json`、`baseline_heldout_v2_report.json` 是有效批次；对应的 `*_review_queue.md` 分别有 52 和 49 条待审。`baseline_heldout_v2_transport_failure_report.json` 保存首次基线的网关连接故障批次，**不能用于能力比较**。三份报告彼此独立，不能合并成一次实验。

`agent_regression_v2_report.json` 与同名待审队列保存修复后 3 道**已曝光 v2 题**各三次的结果；`agent_dev_v16_report.json` 与队列保存同版 72 道开发题的单次结果。它们验证回归而非泛化，不能与 v2 正式留出集成绩合并。后续 v3 留出集同样单独保存。

安全拒答优先级再次收紧后的最终源码批次单列为 `agent_regression_v2_v14_report.json` 和 `agent_dev_v17_report.json`，各自有同名待审队列。前一段的 v16/初次回归是不同源码指纹的诊断报告，不覆盖也不与最终批次相加；最终源码开发集仍有一条未完成 Trace。原有八题契约集的 24 次统计见 [`live_eval_report.md`](../live_eval_report.md)，本机原 Trace 可重评。

`agent_heldout_v3_report.json`、`baseline_heldout_v3_report.json` 是提交 `7cffd5e` 后的新冻结批次，各 60 次；对应队列分别有 56、54 条待审。`agent_heldout_v3_failures.md` 另记录 4 次结构失败的 Trace 诊断。v3 与旧 v2 是两份不同题集，不能按失败数直接比较改进幅度；报告中的 0 人工 pass 只表示尚无人审阅，正式任务完成率仍为空。

`agent_dev_post_v3_gateway_failure_report.json` 是最新源码开发批次的**供应商故障记录**：72 次中 66 次为 `403 AccessDenied.Unpurchased`，不得拿它的完成率或 SQL `false` 评价模型。`agent_post_v3_nio_smoke_report.json` 与待审队列仅包含另一网关、另一模型的 Q06/H317/H320 三道**已知题**各一次，3/3 次结构完成，Q06 的 SQL 金标匹配；不得与 v3 正式留出集比较。两份公开报告都只将原报告的本机 `trace_store` 绝对路径换成相对说明，原始 Trace 仍在被忽略的 `runtime/`。v4 只有冻结题集与金标验证，当前尚无模型成绩。

`*_review_queue.md` 包含问题、金标或 rubric、模型回答、所引 SQL/结果和空白审核模板。它们是**待审核材料**，不是人工通过记录。当前所有最终文字和行为题尚未由人签署审查；不能从 SQL 匹配推算任务完成率。

本目录不含原始 SQLite Trace、完整检查点、模型原始预测、API 密钥或 194 MB 的 DuckDB 快照。Trace 与预测在本机被忽略的 `runtime/` 中，用于生成和无模型重评上述报告；公开目录不能单独复跑 `eval rescore`。数据源及重建方法见 [`data/README.md`](../../data/README.md)，三条脱敏示例 Trace 见 [`docs/traces/`](../traces/)。未来重新评测时应生成新批次和新留出题，不覆盖这组冻结结果。
