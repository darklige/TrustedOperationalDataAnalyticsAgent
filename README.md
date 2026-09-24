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

HTTP 接口：`POST /runs` 传入 `{"question":"..."}`，得到 `run_id`；随后 `GET /runs/{run_id}/events` 订阅 SSE。事件 ID 是持久化序列号；断线后可用 `?after=<最后收到的ID>` 重放遗漏事件。`GET /runs/{run_id}` 查看快照；`POST /runs/{run_id}/cancel` 取消当前进程中的任务。API 目前是本机开发接口，未加用户认证，请仅绑定 `127.0.0.1`。

## 实现路径

```text
用户请求
  → RunState + append-only EventStore
  → ContextBuilder（必要时压缩模型视图）
  → Responses API 流式适配器
  → 完整工具调用校验 → ToolRegistry
  → SQL 策略 + 独立查询进程 → 固定 DuckDB 快照
  → 带 query_id / 数据版本 / 结果哈希的事件
  → 下一轮模型输入或有证据的最终答复
```

`src/trust_agent/loop.py` 是唯一 Agent loop；`provider.py` 隔离模型供应商；`store.py` 保存事件和快照并支持事件重放；`context.py` 压缩模型视图而不删除原始 Trace；`sql/service.py` 是 SQL 执行边界；`mcp_server.py` 复用同一个 ToolRegistry。Skills 在 `skills/` 中按需读取，Memory 目前只异步提取用户明确要求记住的内容。

## 评测与复现

评测集共 **92 题**：原始 `evals/gold_cases.jsonl` 12 题，加上 `evals/expanded_cases.jsonl` 80 题；`evals/dev_cases.jsonl` 与 `evals/heldout_cases.jsonl` 分别含 72 和 20 题。其中 79 条数值金标已通过生产查询服务复核；13 条行为题需要人工按 rubric 评阅。评测设计见 [docs/eval_plan.md](docs/eval_plan.md)。评测脚本比较查询执行结果、检查 Trace 中的工具/证据行为，并记录重复 trial、延迟和 token 使用量。**尚未运行真实模型，因此仓库不宣称 Agent 准确率或相对基线的提升数字。**

```bash
.venv/bin/python -m trust_agent.eval score --cases evals/gold_cases.jsonl \
  --db data/nyc_taxi.duckdb --predictions path/to/predictions.jsonl
.venv/bin/python -m trust_agent.eval baseline --model "$TRUST_AGENT_MODEL" \
  --cases evals/gold_cases.jsonl --db data/nyc_taxi.duckdb --repeats 3
.venv/bin/python -m trust_agent.eval agent --model "$TRUST_AGENT_MODEL" \
  --cases evals/gold_cases.jsonl --db data/nyc_taxi.duckdb --repeats 3
.venv/bin/python scripts/verify_eval_suite.py
```

模型冒烟验证先使用 `gold_cases.jsonl`，调试使用 `dev_cases.jsonl`；最终报告单独使用 `heldout_cases.jsonl`。留出集不用于提示词或工具调参。

MCP 的真实 stdio 测试和项目级 Codex 配置见 [docs/mcp_setup.md](docs/mcp_setup.md)。官方只读文档 MCP 可用于开发查询规范；项目 MCP 只暴露本地受控工具。

## 设计与局限

- [项目需求](docs/requirements.md)、[设计规划](docs/design.md)、[逐阶段教学记录](docs/teach_me.md)、[开发工具与 MCP](docs/development_tools.md)。
- 工具参数的流式增量会展示，但只有完整调用通过校验才执行。多个独立只读工具可以并发执行。
- SQL 采用 AST 白名单、只读 DuckDB、禁外部访问、独立进程、资源及结果上限。独立进程仍以当前 OS 用户运行；此版本是本地作品演示，面向多用户部署还需容器/系统级隔离。
- SSE 断线可从持久事件重放；服务进程崩溃后**正在运行的任务不会自动恢复执行**，只可重放其已保存轨迹。
- 尚未提供生产身份验证、多租户权限、费用计价、因果推断或正式上线 SLA。真实模型端到端效果须在配置 API 后实测。

## 参考资料

- [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAI Streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses)
- [Anthropic Agent 评测](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents/)
- [DuckDB 安全指南](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview)
- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
