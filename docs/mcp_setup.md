# MCP 本地配置与验证

项目使用官方 Python MCP SDK `mcp==2.2.0` 的 stdio server。四个 MCP tools 调用现有 `ToolRegistry.execute`；`run_sql` 继续通过 `QueryService` 校验并在查询进程执行。MCP 工具上的只读标注只是给客户端的提示，真正的限制由 ToolRegistry 和 QueryService 强制执行。

在项目根目录安装 `pip install -e '.[mcp,dev]'` 后，运行 `python -m trust_agent.mcp_server --db data/nyc_taxi.duckdb` 即可启动 stdio server。该进程会等待 MCP 客户端发送 JSON-RPC；不要直接在终端输入自然语言。可用 `TRUST_AGENT_DB` 指定别的数据快照。

Codex 的项目级配置位于 `.codex/config.toml`，已指向本机的 `.venv/bin/python` 与此项目。**Codex 仅在信任项目后读取项目配置**；换机器或移动目录后需要更新 `command` 与 `cwd`。此配置不会改动 `~/.codex/config.toml`。在 Codex 中可用 `/mcp` 查看连接。其他 MCP 客户端可配置相同命令、参数、工作目录和 `TRUST_AGENT_DB` 环境变量。

项目现已初始化为 Git 仓库，并在本机 Codex 用户配置中将该项目目录标记为 trusted。重新运行 `codex mcp list` 已显示 `trustworthy_data_agent` 为 enabled；`openaiDeveloperDocs` 和 `modelContextProtocolDocs` 也均为 enabled。其他机器仍需单独信任此仓库并更新 `.codex/config.toml` 中的绝对路径。

验证命令：`pytest -q tests/test_mcp.py`。测试会启动真实 stdio 子进程，用 SDK 客户端列出四个工具，并调用 `describe_data`、`get_metric`、`run_sql`，同时确认文件读取 SQL 被拒绝。测试数据库在临时目录创建，不依赖大型 TLC 快照，也不需要模型 API 密钥。

参考：[官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[Codex MCP 配置文档](https://learn.chatgpt.com/docs/extend/mcp)。
