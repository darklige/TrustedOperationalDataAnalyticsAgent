# 开发时使用的 Skill 与 MCP

本项目在 2026-09-24 搜索并配置了以下辅助能力。这些是**开发辅助**，不参与 Agent 的生产推理路径；运行时只使用项目自有的 `ToolRegistry` 和 MCP adapter。

| 能力 | 来源与用途 | 本机验证 |
| --- | --- | --- |
| `security-best-practices` Skill | `openai/skills` 官方仓库；审查 Python/FastAPI/SQL 边界 | 已安装于 Codex 用户 skills 目录；SQL 模块按相关原则设计，重新启动会话后可直接调用 |
| `openaiDeveloperDocs` MCP | [OpenAI 官方 Docs MCP](https://developers.openai.com/learn/docs-mcp)，只读查 OpenAI API/SDK 文档 | `codex mcp list` 显示 enabled；JSON-RPC initialize 返回 HTTP 200 |
| `modelContextProtocolDocs` MCP | [MCP 官方文档端点](https://modelcontextprotocol.io/mcp)，只读查协议 | `codex mcp list` 显示 enabled；JSON-RPC initialize 返回 HTTP 200 |
| `trustworthy_data_agent` MCP | 本项目 `src/trust_agent/mcp_server.py`；向 MCP 客户端提供四个受控只读工具 | SDK stdio 客户端 list/call 测试通过；项目受信任后 `codex mcp list` 显示 enabled |

官方文档 MCP 配置在本机 Codex 用户级配置中；仓库提供 `.codex/config.example.toml`，复制为被 Git 忽略的 `.codex/config.toml` 后填写本机路径。项目目录已初始化为 Git 仓库并在开发机器设为 trusted。移至其他机器时，用户应自行安装依赖、信任仓库并创建本机 MCP 配置。`docs/mcp_setup.md` 有详细步骤。

没有安装通用 filesystem MCP：本地文件操作已有 CLI/工作区工具，而额外的广泛文件读写能力会扩大 Agent 工具面。没有把外部文档 MCP 接进数据 Agent 的运行时；它们只帮助开发者核对最新规范。
