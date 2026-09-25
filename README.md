# 可信运营数据分析 Agent

这是一个面向公开运营数据的、可追溯的分析 Agent 项目。核心循环由 Python 实现：流式接收模型事件，在完整工具调用到达后执行受控工具，保留原始 Trace，并要求数值结论引用真实查询结果。开发与运行不依赖 LangChain/LangGraph。

目前使用 NYC TLC 官方 2025 年 1–2 月黄色出租车数据及区域表。项目适合作为 Agent 工程、数据分析工具治理和评测方法的学习样例。公开数据只能支持观察性分析，不能证明因果关系，也不能计算独立乘客数。

## 快速开始

需要 macOS/Linux、Python 3.12+；本机已在 Apple Silicon Mac 上用 Python 3.12 验证。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,mcp]' -r requirements.lock
.venv/bin/python scripts/prepare_data.py
.venv/bin/python -m pytest -q
.venv/bin/trust-agent demo
```

`demo` 是**确定性假模型**驱动的离线端到端演示：仍会经过真实工具注册、隔离查询、事件日志与证据校验；不代表真实模型的任务完成率。数据准备会下载约 120 MB 官方文件，校验 SHA-256，再生成本地 DuckDB 快照。数据细节见 [data/README.md](data/README.md)。

使用真实模型时，设置自己的 API 密钥和模型名后运行：

```bash
export OPENAI_API_KEY='...'
export TRUST_AGENT_MODEL='你的 Responses API 模型名'
.venv/bin/trust-agent ask '2025 年 2 月机场行程的时长是否比 1 月高？'
.venv/bin/trust-agent serve --host 127.0.0.1 --port 8000
```

如果服务只提供兼容 OpenAI 的 `POST /v1/chat/completions`，先设置
`TRUST_AGENT_PROVIDER=chat_completions`、`OPENAI_BASE_URL` 为该服务的 `/v1` **基础地址**，
再设置 `TRUST_AGENT_MODEL` 与 `OPENAI_API_KEY`。密钥只放本机环境变量；不要提交到仓库。
Chat Completions 的工具参数是流式片段，此适配器会等整轮流完成并验证 JSON 后派发工具；
Responses 适配器可在单个输出项完成时更早派发。某些兼容网关即使返回工具调用，
`finish_reason` 也可能是 `stop`，因此以完整调用和正常流结束共同判断。
`baseline`、`agent` 评测与 CLI/API 共用同一供应商选择；报告会同时记录请求型号与接口实际返回型号。

HTTP 接口：`POST /runs` 传入 `{"question":"..."}`，得到 `run_id`；随后 `GET /runs/{run_id}/events` 订阅 SSE。事件 ID 是持久化序列号；断线后可用 `?after=<最后收到的ID>` 重放遗漏事件。`GET /runs/{run_id}` 查看快照；`POST /runs/{run_id}/cancel` 取消当前进程中的任务。API 目前是本机开发接口，未加用户认证，请仅绑定 `127.0.0.1`。

启动服务后打开 `http://127.0.0.1:8000/ui` 可使用浏览器界面。界面按尝试 ID 缓存暂定文本，只有收到 `text_committed` 才显示；失败、取消或 `text_discarded` 会清空草稿。页面 URL 带 `run` ID，重载时可从事件序号重放已提交答案。

SSE 的 `text_delta` 是带 `attempt_id` 的暂定草稿。客户端应按 `attempt_id` 缓冲，收到 `text_committed` 才显示最终答复；`text_discarded` 应撤销相应草稿。终端 CLI 已按这一契约实现。`context_layered` 表示旧 SQL 结果在下一轮模型视图中缩成保留证据字段的摘要，原始 Trace 不变；如果仍超出预算，才触发旧轮次归纳。

可通过 `TRUST_AGENT_MODEL_CONTEXT_TOKENS`、`TRUST_AGENT_OUTPUT_RESERVE_TOKENS`、`TRUST_AGENT_MAX_OUTPUT_TOKENS` 设置上下文和输出上限；`TRUST_AGENT_STREAM_IDLE_SECONDS` 与 `TRUST_AGENT_MAX_STREAM_RETRIES` 控制单次模型流的空闲超时和有限重试。`context_built.estimated_input_tokens` 是保守估算，实际用量以模型返回的 `usage` 为准。

## 实现路径

```text
用户请求
  → RunState + append-only EventStore
  → ContextBuilder（必要时压缩模型视图）
  → Responses / Chat Completions 流式适配器
  → 完整工具调用校验 → ToolRegistry
  → SQL 策略 + 独立查询进程 → 固定 DuckDB 快照
  → 带 query_id / 数据版本 / 结果哈希的事件
  → 下一轮模型输入或有证据的最终答复
```

`src/trust_agent/loop.py` 是唯一 Agent loop；`provider.py` 和 `chat_provider.py` 隔离两种模型协议；`store.py` 保存事件和快照并支持事件重放；`context.py` 压缩模型视图而不删除原始 Trace；`sql/service.py` 是 SQL 执行边界；`mcp_server.py` 复用同一个 ToolRegistry。Skills 在 `skills/` 中按需读取，Memory 目前只异步提取用户明确要求记住的内容。

## 评测与复现

评测集共 **92 题**：原始 `evals/gold_cases.jsonl` 12 题，加上 `evals/expanded_cases.jsonl` 80 题；`evals/dev_cases.jsonl` 与 `evals/heldout_cases.jsonl` 分别含 72 和 20 题。其中 79 条数值金标已通过生产查询服务复核；13 条行为题需要人工按 rubric 评阅。评测设计见 [docs/eval_plan.md](docs/eval_plan.md)，[真实模型评测记录](docs/live_eval_report.md)保留冻结配置、分母和调用成本；[公开评测快照](docs/eval_artifacts/README.md)提供逐 trial 报告与待审材料。评测脚本比较查询执行结果、检查 Trace 中的工具/证据行为，并记录重复 trial、延迟和 token 使用量。**首批 12 题的基线/Agent 三次重复是上一版运行时的开发诊断结果。修复版 72 题开发集和冻结的 20 题留出集，Agent 与同模型单轮基线各运行 3 次，共 552 次 trial，并已从持久化 Trace/原预测无模型重评。最终文字及行为题仍需人工审核，不能宣称整体任务完成率。**

```bash
.venv/bin/python -m trust_agent.eval score --cases evals/gold_cases.jsonl \
  --db data/nyc_taxi.duckdb --predictions path/to/predictions.jsonl
.venv/bin/python -m trust_agent.eval baseline --model "$TRUST_AGENT_MODEL" \
  --cases evals/gold_cases.jsonl --db data/nyc_taxi.duckdb --repeats 3
.venv/bin/python -m trust_agent.eval agent --model "$TRUST_AGENT_MODEL" \
  --cases evals/gold_cases.jsonl --db data/nyc_taxi.duckdb --repeats 3 \
  --checkpoint runtime/agent_eval.jsonl --state-db runtime/agent_eval.sqlite3
.venv/bin/python -m trust_agent.eval rescore --cases evals/dev_cases.jsonl \
  --db data/nyc_taxi.duckdb --report runtime/old_report.json \
  --state-db runtime/eval_agent.sqlite3 --out runtime/rescored_report.json
.venv/bin/python scripts/verify_eval_suite.py
.venv/bin/python -m trust_agent.eval agent --model "$TRUST_AGENT_MODEL" \
  --cases evals/regression_dev_cases.jsonl --repeats 1 \
  --state-db runtime/regression_dev.sqlite3 --out runtime/regression_dev.json
.venv/bin/python scripts/analyze_regression_run.py \
  --report runtime/regression_dev.json --state-db runtime/regression_dev.sqlite3
```

长批次中断后，用原命令加 `--resume` 续跑；`--checkpoint` 逐 trial 持久化，
会校验题集、模型/生成设置、数据文件、预算和 Trace 数据库是否仍匹配。
基线也支持相同的 `--checkpoint`/`--resume`。具体流程见 [docs/eval_plan.md](docs/eval_plan.md)。

正式评测前可在开发集做小规模冒烟，例如 `agent --limit 1 --repeats 1 --max-turns 4
--max-total-tokens 6000`。2026-09-24 的首个兼容网关探针中，请求 `Qwen3.5-Turbo`
实际返回 `Qwen3.5-0.8B`；Q01 的单次基线和 Agent、Q10 的单次 Agent 试验均失败。
随后在另一兼容网关用五道开发题 Q01/Q02/Q04/Q05/Q10、每题一次筛选
`qwen3.8-max` 与 `qwen3.8-flash`。旧版重评显示 Max Agent 的 Q01/Q04 查询结果与金标匹配，
Q02/Q05/Q10 需要核对额外行、计算或文字；五题 Trace 均通过。数值查询匹配仍须人工核对
最终文字，目前五题都没有人工任务完成判定。初筛后选择优先验证 Max 的固定版本；
Flash 尚未按相同评分器重评，不能据此比较两模型整体效果。`qwen3.8-max-0902` 的 Q01 单轮基线
探针已通过，响应中的实际模型名与请求一致；这一题还不能代表固定版本的整体效果。
这些样本和版本间的评分器差异都不能推出整体性能。重评入口及审核格式见
[docs/eval_plan.md](docs/eval_plan.md)。

模型冒烟验证先使用 `gold_cases.jsonl`，调试使用 `dev_cases.jsonl`。旧 `heldout_cases.jsonl` 已用于失败审计，只保留历史报告；本轮改动以新增的 `frozen_heldout_v2.jsonl` 作未见留出集，题集和数据快照可用 `.venv/bin/python scripts/verify_new_holdout.py` 核验。E020 历史金标歧义记录在 `evals/known_ambiguities_v1.json`，评分时显式传入 `--ambiguities` 后，匹配替代口径只会进入人工复核。

新留出集在源码提交 `6543890` 后完成：20 题各 3 次，Agent 52/60 次形成合格完成 Trace；36 次数值题 SQL 为 17 直接匹配、0 不匹配、19 待审。单轮基线有效重跑的对应结果为 20/11/5；首次基线因网关连接错误作废并保留故障报告。Agent 的 8 次未完成集中在三道行为题，提示安全拒答仍可能反复被门禁拒绝至轮次耗尽。两组最终文字均未人工审核，**不能报告任务完成率或整体准确率**。分母、token、延迟、故障处理及逐 trial 证据见 [真实模型评测记录](docs/live_eval_report.md) 与 [公开评测快照](docs/eval_artifacts/README.md)。

`evals/regression_contract_cases.jsonl` 收集已知的引用、范围、安全和业务口径回归题，其中 E072/E080 是已审计的旧留出题；它只用于开发回归。运行命令把上例 `--cases` 改为该文件并保持 `--repeats 1`，结果不得称为新留出集成绩。

MCP 的真实 stdio 测试和项目级 Codex 配置见 [docs/mcp_setup.md](docs/mcp_setup.md)。官方只读文档 MCP 可用于开发查询规范；项目 MCP 只暴露本地受控工具。

## 设计与局限

- [项目需求](docs/requirements.md)、[设计规划](docs/design.md)、[逐阶段教学记录](docs/teach_me.md)、[开发工具与 MCP](docs/development_tools.md)。
- 工具参数的流式增量会展示，但只有完整调用通过校验才执行。多个独立只读工具可以并发执行。
- SQL 采用 AST 白名单、只读 DuckDB、禁外部访问、独立进程、资源及结果上限。独立进程仍以当前 OS 用户运行；此版本是本地作品演示，面向多用户部署还需容器/系统级隔离。
- SSE 断线可从持久事件重放；服务进程崩溃后**正在运行的任务不会自动恢复执行**，只可重放其已保存轨迹。
- 尚未提供生产身份验证、多租户权限、费用计价、因果推断或正式上线 SLA。真实模型已完成冻结开发集与留出集重复试验；完整端到端效果仍须人工审核最终答复。

## 参考资料

- [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAI Streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses)
- [Anthropic Agent 评测](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents/)
- [DuckDB 安全指南](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview)
- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
