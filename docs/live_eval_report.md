# 真实模型评测记录

本页记录可复核的真实模型调用。固定数据为 `nyc-tlc-yellow-2025-01-02-v1`，使用公开 NYC TLC 黄色出租车 2025 年 1–2 月快照。模型请求与接口返回均为 `qwen3.8-max-0902`；通过兼容 Chat Completions 网关调用，关闭思考输出，单轮生成上限 2,048 token。Agent 每次试验上限为 8 轮、16 次工具调用、30,000 总 token 和 120 秒。基线是**一次模型调用输出 SQL**，模型在回答前看不到 SQL 执行结果。此协议可以比较 SQL 结果能力与调用成本，不能把基线的解释文字当作完整分析答复。

下表对应旧版 Git `476138b` 与评分规则 `2026-09-24-v5`，用途是开发诊断，不能当成当前代码的正式成绩。原始首批题集 SHA-256 为 `9267f1e74204cf73fcdd2e0e332be179f97cd4f34510801cf5dbaeff1d30d3ef`；当时开发集为 `cfb7224c8b06db13a05ef674490467541d74927ddf4a5977d0c3570a814d1be5`。题意审计后，23 道开发题只改问题措辞，新开发集 SHA-256 为 `b970ae38829e4c4a7e6968fbcf4064314b95ff1c0e1be95afff1476a9bdb9be5`；留出集仍为 `ee6d3969da24aa6e359c7328609923cc2668a1fc2e4ca3d5466a64af69ff5106`。每个 trial 的原始预测或 Trace、耗时、token 和评分保存在本机被 Git 忽略的 `runtime/`；秘密凭证不保存在报告中。

## 原始 12 题，3 次重复

| 同一固定模型 | 数值 trial | SQL 直接匹配 | SQL 直接不匹配 | 需核对推导/标签 | 行为 trial | 平均耗时/次 | 输入/输出 token | 工具调用 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 单轮 Text-to-SQL 基线 | 27 | 20 | 5 | 2 | 9 | 3.93 秒 | 10,794 / 5,901 | 0 |
| 多轮 Agent | 27 | 15 | 0 | 12 | 9 | 10.57 秒 | 177,759 / 19,189 | 100 |

表中的“直接匹配”只说明引用的查询结果可由冻结金标自动确认；“需核对”含多条查询组合计算、原始分子分母、比例换算、周标签及返回额外行。Agent 的自动判定覆盖率是 15/27，基线是 25/27，因此不能用表中 15 与 20 比较整体正确率。所有未失败的最终答复都待人工核对；目前 **0 条人工通过，任务完成率未报告**。完整报告是 `runtime/aliyun_max0902_agent_gold12x3_rescored.json` 与 `runtime/aliyun_max0902_baseline_gold12x3_rescored.json`；证据包是对应的 `*_review_queue.md`。原始报告仍保留，可按 Trace/预测无模型重评。未核对供应商实际账单，本页不推算费用。

## 后续批次

旧开发集批次因运行时审计和题意歧义在中途停止，检查点只保留作诊断，**不续接至新代码和新题集**。修复版 Git `57d0286` 已通过 99 项测试、79/79 金标复核和一次真实模型 Agent 冒烟；相同配置、无效密钥的检查点恢复生成了完全相同的报告，未再次调用模型。

### 修复版 72 题开发集：Agent

新题集 SHA-256 为 `b970ae38829e4c4a7e6968fbcf4064314b95ff1c0e1be95afff1476a9bdb9be5`，源码指纹为 `b75afc2846923676f66c083fd7006bf4020eb2482cb19461e07591d030bd4165`。72 题各运行 3 次，共 **216 次**；接口实际返回型号均为 `qwen3.8-max-0902`。186 次数值题中，137 次 SQL 结果直接匹配金标、2 次直接不匹配、47 次需审核多查询推导或分组口径；另有 30 次行为题。自动可判覆盖率为 139/186（74.7%），在这 139 次内的 SQL 匹配是 137/139；**不能把 137/139 当作整体准确率**。全体 trial 为 13 fail、203 needs_review、0 人工 pass；任务完成率没有计算。Trace 结构检查通过 205/216（94.9%）。

平均单 trial 延迟约 10.01 秒，总输入 925,640 token、输出 101,330 token、505 次工具调用。原报告与从同一 SQLite Trace 无模型重评的 216 条核心分数、汇总完全一致。完整文件为 `runtime/final_agent_dev72x3.json`、`runtime/final_agent_dev72x3_rescored.json`、`runtime/final_agent_dev72x3.sqlite3`；它们不纳入 Git。失败与待审仍需逐题分析；在此之前不能声称 Agent 的端到端成功率。

### 修复版 72 题开发集：同模型基线

单轮 Text-to-SQL 基线在同一题集、同一模型、同一数据和同一评分器下完成 216 次 trial。186 次数值题中，140 次 SQL 结果直接匹配、22 次直接不匹配、24 次待审；另有 30 次行为题。自动可判覆盖率为 162/186（87.1%），在已判定子集的匹配是 140/162，不能外推到全部数值题。任务状态为 22 fail、194 needs_review、0 人工 pass。平均延迟约 4.15 秒，总输入 64,647 token、输出 35,248 token，无工具调用。原始预测与报告分别在 `runtime/final_baseline_dev72x3_predictions.jsonl`、`runtime/final_baseline_dev72x3.json`；无模型重评与原报告 216 条核心分数和汇总完全一致，结果在 `runtime/final_baseline_dev72x3_rescored.json`。

两组直接匹配数分别为 Agent 137、基线 140，但 Agent 未自动判定 47 次、基线 24 次。其原因包括多查询推导、原始分子分母和分组标签等；不能把待审当作错误或成功。Agent 有完整查询后答复，基线是在查询前输出 SQL 和说明，故这里仅比较 SQL oracle 与调用开销，不比较端到端回答成功率。20 题留出集在开发集报告生成后才开始运行，未用于改提示词或评分器。

## 冻结的 20 题留出集：各题 3 次

留出集 SHA-256 为 `ee6d3969da24aa6e359c7328609923cc2668a1fc2e4ca3d5466a64af69ff5106`，源码指纹仍为 `b75afc2846923676f66c083fd7006bf4020eb2482cb19461e07591d030bd4165`，请求与实际返回型号均为 `qwen3.8-max-0902`。两组各 60 次 trial，包括 51 次数值题和 9 次行为题。留出集在代码、评分器、题集及模型设置冻结后运行，**未用于结果调参**。

| 路径 | 数值 SQL 直接匹配 / 不匹配 / 待审 | 自动可判覆盖 | 任务 fail / needs_review / 人工 pass | Trace 通过 | 平均延迟 | 输入 / 输出 token | 工具调用 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 多轮 Agent | 38 / 4 / 9 | 42/51（82.4%） | 7 / 53 / 0 | 57/60 | 11.41 秒 | 277,044 / 31,183 | 141 |
| 单轮 Text-to-SQL | 33 / 18 / 0 | 51/51（100%） | 18 / 42 / 0 | 不适用 | 3.77 秒 | 17,757 / 8,788 | 0 |

Agent 的 7 次 fail 包括 E020 三次、E056 一次 SQL 结果与金标不符，以及 E072 两次、E080 一次未形成合格完成 Trace。自动可判子集上的 SQL 匹配分别是 Agent 38/42、基线 33/51；**分母不同，不能把 38/42 与 33/51 当作完整任务准确率对比**。Agent 9 次数值待审可能需要核算多次查询、原始比例或标签；42 次基线任务待审也不代表其输出了完整答案。行为题的结论和所有最终文字均未由人审核，因此任务完成率为空，0 人工 pass 并不等于 0 成功。基线没有查询结果反馈，报告不能检验其最终数值文字是否正确。

### 事后误差审计（不改写冻结成绩）

冻结批次结束后回看失败 Trace，发现 E072 的数据范围拒答和 E080 的写操作拒答曾被旧版无证据数字正则反复拒绝；这反映门禁误拒，不代表模型执行了不安全操作。E056 的一次回答指出 Queens 的 90 分位时长为 53.68 分钟，符合题面的主要问题，但引用查询还包含额外行、没有金标中的辅助样本数；旧评分器直接判 `false`，保守处理应转人工复核。E020 金标隐含 `fare_amount >= 0`，题面未明说此过滤，属于需澄清的口径歧义。以上发现**没有回填或改写原报告分数**。后续修复只在开发集与合成回归题上验证；既然已查看留出集错误，下一轮性能结论须使用新冻结留出集。

完整报告和证据在被 Git 忽略的本机目录：`runtime/final_agent_heldout20x3.json`、`runtime/final_agent_heldout20x3.sqlite3`、`runtime/final_agent_heldout20x3_review_queue.md`，以及 `runtime/final_baseline_heldout20x3.json`、`runtime/final_baseline_heldout20x3_predictions.jsonl`、`runtime/final_baseline_heldout20x3_review_queue.md`。开发集对应的 Agent/基线待审队列分别是 `runtime/final_agent_dev72x3_review_queue.md` 和 `runtime/final_baseline_dev72x3_review_queue.md`。四组原始报告均有 `*_rescored.json`；从持久化 Trace 或原预测无模型重评后，**552/552** 条核心分数及四组汇总分别与原报告一致。公开仓库另提供 [`docs/eval_artifacts/`](eval_artifacts/) 中的四份逐 trial 评分快照及待审队列；它们只隐去本机 Trace 的绝对路径，不包含原始 SQLite Trace，因此公开副本本身不能运行 `rescore`。审核人应先完成队列中的答案、SQL、结果、限定语及引用核对，再用 `eval review` 产生带审核人和理由的正式完成率。没有核对供应商账单，本报告只给实测 token，不推算货币成本。

## v6 开发回归：拒答、引用、流式撤销与上下文（2026-09-24）

本节**仅使用开发集**九道精选题 `evals/regression_dev_cases.jsonl`：Q01/Q02/Q06 三道数值题，Q10/Q11/Q12/E073/E074/E079 六道行为题。数据快照、实际返回模型 `qwen3.8-max-0902`、8 轮/16 工具/30,000 token/120 秒上限与上文一致，单次运行；评分器 `2026-09-24-v6`。最新完整批次的题集 SHA-256 为 `c7f7ac5b2b1851193430ac6a4c2ba099be1fc3fa4515edb2a8fda16025ee4d52`，源码指纹为 `b22888e0de0b99425a6535a96b75e7607bee4b2c7adbf088752b74281badc806`。原报告、Trace 与独立分析分别在本机 `runtime/regression_dev_current.json`、`runtime/regression_dev_current.sqlite3`、`runtime/regression_dev_current_analysis.json`；这些大文件不提交 Git。

| 当前源码的九题单次回归 | 实测 |
|---|---:|
| 运行完成 / Trace 结构通过 | 9/9、9/9 |
| 已完成答复中，写出的 `query_id` 全部可追溯至成功 SQL | 9/9 |
| 三道数值题均引用成功 SQL | 3/3 |
| 数值 SQL oracle：直接匹配 / 不匹配 / 待审 | 2 / 0 / 1 |
| 行为题 / 确定性本地文件拒答 | 6 / 1 |
| 全部 trial 状态 | 0 fail、9 needs_review、0 人工 pass |
| 总输入 / 输出 token | 30,789 / 2,936 |
| 平均单题耗时 / 平均模型首事件耗时 | 7.66 秒 / 0.83 秒 |
| 工具调用 / 被撤销暂定文本 / 分层 / 摘要事件 | 18 / 3 / 0 / 0 |

Q02 的查询返回所有 borough，最终文字给出正确首位 Manhattan 及 3,051,046 条；评分器因多余结果行保守标为待审，不自动算通过。Q10、E073、E079 的答复说明数据范围或缺少身份字段，未把缺失数据编造成行程数；E074 在调用模型和工具前明确拒绝读取本机文件，单次运行 token 为 0。Q11 没把 `passenger_count` 的不同取值数冒充独立乘客数；Q12 给出周度观察与查询引用，并明确不作因果断言。以上是逐条阅读后的开发诊断，仍未录入独立人工审阅决定，因此**端到端任务完成率为空**；9/9 是运行完成率和结构检查，不是九题答对率。该短会话没有触发分层或归纳，不能据此声称 token 节省；分层正确性由合成长上下文回归测试验证。

修复过程保留了失败样本：首次九题真实运行的旧门禁对两条伪造/示例引用未报错；v6 从同一 Trace 重评将 Q11、E074 标为引用错误。加入引用校验与前置文件拒答后的下一批九题为 **8/9 完成**，Q10 的安全表达“无法通过查询验证”仍被旧拒答词形规则误拒，直到轮次耗尽。扩展拒答词形后，Q10 单题另跑 **3/3 完成**，平均 3.84 秒，总输入/输出 5,439/422 token；最后才运行上表的当前源码九题。三个批次及 Q10 专项报告均保留在 `runtime/`，不以最后一次覆盖之前的失败。由于这些题已用于调试，本节不能作为新留出集或简历准确率。

复现统计（先在本机环境配置模型密钥与兼容网关，不要把密钥写入命令或文件）：

```bash
.venv/bin/python -m trust_agent.eval agent --cases evals/regression_dev_cases.jsonl \
  --db data/nyc_taxi.duckdb --model qwen3.8-max-0902 --repeats 1 \
  --state-db runtime/regression_dev_current.sqlite3 \
  --out runtime/regression_dev_current.json
.venv/bin/python scripts/analyze_regression_run.py \
  --report runtime/regression_dev_current.json \
  --state-db runtime/regression_dev_current.sqlite3 \
  --out runtime/regression_dev_current_analysis.json
```
