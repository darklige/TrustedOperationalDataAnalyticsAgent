# 开发教学记录

本文件随项目开发持续更新。每阶段记录真实完成的内容；“计划”与“已验证”严格区分。

## 阶段 0：需求与架构基线（2026-09-24）

### 本阶段完成了什么

建立 `docs/requirements.md`，把项目目标、用户场景、功能验收、安全与资源限制、评测指标、P0–P7 里程碑写成可检查的要求。建立 `docs/design.md`，规定运行时模块边界、事件/状态模型、Agent loop、流式工具调用、上下文压缩、Memory、Skills、SQL 执行边界和评测方案。建立根目录 `AGENTS.md`，要求后续开发在每次改动后更新本文件。

此时只是**设计阶段**；本文没有声称 Agent 代码、数据快照或评测已经完成。下一步应先固定公开数据快照和 10 条金标任务，然后用假模型实现最小循环。

### 为什么采用这个设计

Agent 是一个会跨多轮调用模型和工具的系统。若只保存当前对话列表，发生崩溃、压缩或工具错误后就很难解释“它为何给出这个结论”。因此设计将**追加事件日志作为事实来源**，当前状态和模型上下文都从日志派生。这样可以重放执行路径，并在压缩模型输入时保留原始证据。

流式模型可能先给出工具参数片段，随后继续修改参数；所以只有工具调用完成事件出现、JSON 合法且通过 schema、权限和预算检查后才派发。异步 Memory 也只在轮次边界进入上下文，避免运行中的模型请求出现竞态。SQL 能访问文件、网络和扩展，因此即使数据库只读，也需要语法限制、配置限制和进程隔离。

### 核心数据流如何讲给面试官

用户问题进入运行时后，系统生成 `run_id` 并追加 `run_started`。每轮从事件重建状态，构造有 token 预算的上下文视图，流式调用模型并转发事件；当模型输出完整工具调用时，调度器校验并执行受控工具，写入带证据 ID 的结果。模型继续分析或输出最终答案；最终答案的关键数字和结论指回查询、口径和数据版本。完整轨迹用于复盘及评测，压缩只影响下一轮发送给模型的内容。

### 如何检查本阶段产物

阅读 `docs/requirements.md` 中的功能验收与里程碑，再对照 `docs/design.md` 的事件流和模块边界。检查 `AGENTS.md` 是否要求保留原始 Trace、限制 SQL、每次更新本文件。此阶段没有可运行应用测试；在代码交付前不要使用“已实现”描述。

### 下一阶段的学习重点

P0：学习如何固定数据版本、写指标口径和金标答案；P1：学习事件折叠、状态机、模型流归一化及工具调用完成条件。完成每个阶段后，在本文件新增小节，写清关键代码路径、命令、实测输出和失败案例。

## 阶段 P0：固定数据快照与首批金标（2026-09-24）

### 完成内容与实现

`data/source_manifest.json` 固定 NYC TLC 官方 2025 年 1–2 月 Yellow Taxi Parquet 和区域映射 CSV 的 URL、字节数及 SHA-256。`scripts/prepare_data.py` 逐块下载到临时文件，校验哈希后才原子替换；已有文件不匹配时直接报错。脚本按文件月份过滤上车时间，再筛选 1–240 分钟、0.1–100 英里且起终点区域存在的行程，生成物化的 `trips`、`zones`、`dataset_metadata` 三张 DuckDB 表。`data/README.md` 解释来源、口径和使用限制。

`evals/gold_cases.jsonl` 提供 12 条首批题目：9 条有人工核对的参考 SQL 与期望结果，3 条验证数据覆盖、不可得信息和因果限定。`docs/eval_plan.md` 规定判分、基线、重复运行与留出集设计。这里的金标是**起点**，不是 80–120 题的正式评测集，也没有测出 Agent 准确率。

### 为什么这样设计

“可复现”要求题目、SQL、清洗口径和具体源文件同时固定。只记录下载链接不足够，因为发布方可能替换同名文件；SHA-256 能检测变化。原始月文件含相邻月份的少量记录，因此先按上车月份收口，避免月度指标重叠。Agent 只查询物化数据库，运行时不需要接触原始 Parquet 路径。

金标 SQL 按**执行结果**判分，因为同一问题有多种等价 SQL 写法。Q10–Q12 则没有单一数值答案，需要检查 Agent 是否正确说明“没有数据”“没有身份字段”“不能证明因果”。这三类是数据 Agent 可信度的关键。

### 如何运行与验证

安装 DuckDB 后，在项目根目录运行 `python scripts/prepare_data.py`，随后运行 `python scripts/prepare_data.py --verify-only`。如需重新构建，运行 `python scripts/prepare_data.py --force`。本次实测：三个源文件哈希均匹配；生成 `zones` 265 行、`trips` 6,799,740 行，其中 1 月 3,356,067、2 月 3,443,673；数据库约 208 MB。用 DuckDB 只读连接，并设置 `enable_external_access=false` 后重跑 Q01–Q09，实际结果全部等于 JSONL 的期望行。Q10–Q12 是行为题，尚未进行 Agent 运行评测。

### 面试时如何讲

“我先固定了公开数据与语义口径，再写 Agent：官方文件的哈希、清洗版本与数据库行数都可追溯。金标题不比较 SQL 文本，而是执行候选 SQL 比较结果；对缺失月份、独立乘客数、因果归因等无法回答的问题，要求系统明确边界。这使模型升级或 Agent loop 改动后可以量化回归。”

### 遗留问题

当前仅有两个月的公开数据和 12 条题目；正式评测需扩展并保留未见测试集。原始数据质量不能由脚本完全修复，金额与乘客人数也未做填补；涉及这些字段的指标必须在查询中明确额外过滤和限制。

## 阶段 P5（独立模块）：MCP stdio 适配（2026-09-24）

### 完成内容与实现

新增 `src/trust_agent/mcp_server.py`，使用官方 `mcp==2.2.0` SDK 的 `MCPServer` 暴露 `describe_data`、`get_metric`、`run_sql`、`load_skill`。`create_server(registry)` 接收已有 `ToolRegistry`，每个 MCP handler 将参数转成 JSON 后统一调用 `registry.execute`。其中 `run_sql` 因此继续经过 `QueryService` 的 AST 校验、允许表限制、只读 DuckDB 连接与查询子进程；MCP 没有第二条 SQL 执行路径。服务使用 stdio，入口为 `python -m trust_agent.mcp_server`，`--db` 或 `TRUST_AGENT_DB` 指定数据快照。工具只读注解是客户端提示，真正的安全边界仍由本地执行路径强制。

添加项目级 `.codex/config.toml` 指向本机项目虚拟环境与 stdio 入口，并在 `docs/mcp_setup.md` 说明迁移路径和验证命令。官方 Codex 配置格式是项目内 `.codex/config.toml`，Codex 在信任项目后读取；没有修改用户级全局配置。该文件含当前机器的绝对路径，移动项目后必须更新。

### 为什么这样设计

Agent 自己调用工具、MCP 客户端调用工具，都应得到相同结果和限制。如果 MCP server 自行直连 DuckDB，就可能绕开 SQL 策略，形成安全漏洞和测试盲区。这里的 MCP 仅是传输适配层，业务契约仍在 ToolRegistry；启动时还校验 MCP handler 名称与 ToolRegistry 公开的 spec 完全一致，防止新增工具后漏同步。

### 如何运行与验证

在项目根目录运行 `.venv/bin/python -m pytest -q tests/test_mcp.py`。本次结果为 **1 passed**。该测试创建小型临时 DuckDB，启动真正的 MCP stdio 子进程，用官方 SDK 客户端列出四个工具，调用 `describe_data`、`get_metric`、`run_sql`，并确认 `read_text('/etc/passwd')` 被拒绝。`.venv/bin/python -m ruff check src/trust_agent/mcp_server.py tests/test_mcp.py` 结果为 **All checks passed**。测试不需要模型 API 密钥，也不依赖大型 TLC 文件。

### 面试时如何讲

“我把 MCP 做成同一 ToolRegistry 的适配器，而不是重新实现工具。MCP tool 的只读 annotation 仅帮助客户端理解风险，实际执行仍经过 schema 检查和隔离的 QueryService。我用真实 stdio 客户端做 list/call 往返测试，并用文件读取 SQL 证明 MCP 入口没有绕开安全策略。”

### 遗留问题

已验证本地 SDK 2.2.0 的 stdio 客户端互操作；不同外部 MCP 主机仍应分别做兼容性测试。项目级 Codex 配置使用本机绝对路径，复制仓库时需更新。当前目录尚非 Git 仓库，`codex mcp list` 未列出项目级 server；待项目成为受信任仓库后需复查主机加载。MCP 入口目前只服务本地演示数据，没有用户身份鉴权或多租户隔离。

## 阶段 P3（独立模块）：可信 SQL 执行边界（2026-09-24）

### 完成内容与关键入口

`src/trust_agent/sql/service.py` 提供 `QueryService(db_path, allowed_tables)`。`schema()` 只返回白名单中 `main` schema 物理表的列名与类型；`query(sql, row_limit=100, timeout_s=10)` 返回列、行、实际行数、是否截断以及解析后执行的 SQL。策略拒绝多语句、DDL/DML、跨 schema 表、非白名单表、表函数，以及不在分析函数白名单中的函数。`src/trust_agent/sql/__init__.py` 导出服务与三种错误：策略拒绝、执行失败、超时。

一次查询的执行顺序是：主进程检查参数大小 → 启动短生命周期子进程 → 子进程用 SQLGlot 按 DuckDB 方言解析并检查 AST 和表作用域 → 只读打开 DuckDB 并核对允许表确实是物理表 → 给查询外包一层 `LIMIT row_limit + 1` → 序列化有大小限制的结果 → 主进程接收结果或到时终止子进程。额外读取一行用于判断 `truncated`，不会把它返回给调用方。语法解析也放在子进程，因为复杂输入造成的解析卡顿同样应受超时约束。

### 为什么这样设计

DuckDB 官方安全文档明确指出，不可信 SQL 可以读文件、访问网络、安装扩展并消耗大量资源，单靠数据库 `read_only` 不足以把它变成安全沙箱。因此此模块组合了四层措施：SQL AST 与函数白名单；DuckDB `read_only=True`、`enable_external_access=false`、禁止扩展自动安装/加载；子进程的时间、内存、线程和临时空间限制；结果行数、列数和输出体积限制。这些措施保护的是本地演示环境中的**可信数据库文件**，不能代替生产环境的 OS 沙箱与网络隔离。

最值得讲解的细节是 CTE 作用域。例如内层子查询可以定义一个叫 `private_data` 的 CTE，而外层同名来源实际上是数据库物理表。若只收集整棵 AST 的 CTE 名称，外层私有表会被误判为 CTE 并绕过表白名单。实现使用 SQLGlot 的 `traverse_scope`，对每个作用域解析真实来源：CTE/子查询是逻辑 Scope，物理表才需要匹配允许表。`tests/test_sql.py` 专门验证了这个绕过案例。

集成时还发现 SQLGlot 的 `exp.Func` 基类包含 `AND`、`CASE`、`IF` 等纯 SQL 结构。若把所有 `exp.Func` 都当成可能读文件的数据库函数，合法的多条件与条件聚合查询就会被误拒。现在明确放行这些**结构表达式类型**，实际函数调用仍需匹配函数白名单；新增测试同时验证 `CASE` 内的 `read_text` 仍被拒绝。

### 如何运行与实测结果

在项目根目录运行 `.venv/bin/python -m pytest -q tests/test_sql.py`，本次实测 **23 passed in 7.17s**；运行 `.venv/bin/python -m ruff check src/trust_agent/sql tests/test_sql.py`，结果 **All checks passed**。临时 DuckDB 测试覆盖 CTE 加聚合与联表、月份函数、`CASE`/布尔条件、schema、行截断、非法表/视图、跨 schema、多语句、文件与设置函数、内层 CTE 遮蔽，以及超时终止和数据库错误分类。最后一项集成测试需要本地 `data/nyc_taxi.duckdb`；若没有执行过数据准备，会明确跳过该项。测试不需要模型 API 密钥。

使用真实的 `data/nyc_taxi.duckdb` 运行固定金标题 Q01–Q09，9 条都经过 `QueryService` 返回与 `evals/gold_cases.jsonl` 相同的结果（**9/9 PASS**）。这验证策略既拦截危险 SQL，也能运行当前评测集的合法分析 SQL；它不是 Agent 任务完成率指标。

### 面试时如何讲

“我没有把模型生成的 SQL 直接交给 DuckDB。先用 SQLGlot 在独立进程解析单条 SELECT，并做作用域级表来源检查；再用 DuckDB 只读连接和关闭外部访问限制文件、网络及扩展；最后通过子进程超时和结果预算限制资源消耗。我还构造了嵌套 CTE 名称遮蔽的绕过测试，证明仅靠字符串或全局 AST 名称扫描不可靠。”

### 遗留问题与安全边界

子进程仍以当前 macOS 用户身份运行；DuckDB 的内存上限主要限制数据库分配，不能严格限制整个 Python 进程。若面对不受信任的外部用户，需要再加受限容器或 OS 沙箱、网络隔离、独立低权限用户和服务端并发配额。当前策略只做到**表级**白名单，未提供列级授权；允许表不能含不应被 Agent 读取的敏感列。函数白名单刻意较窄，新分析函数应先审查再加入，并用测试证明不会引入外部访问或副作用。

## 阶段 P6（独立模块）：评测与单轮基线（2026-09-24）

### 完成内容与关键入口

`src/trust_agent/eval/tasks.py` 加载并验证冻结的 JSONL 金标题，拒绝重复 ID、缺失问题和混合两种金标的记录。`scoring.py` 通过生产 `QueryService` 运行候选 SQL，将**结果行**与金标比较；允许行顺序变化、无关附加列和两位小数误差，但不接受少行、多行或被截断结果。对 Agent Trace，它还检查事件序号、调用参数完成事件先于工具启动、工具结果有对应启动、最终答案引用真实 `query_id`。评分器从被引用的查询中取 SQL，避免拿没有进入最终答案的探索性 SQL 冒充正确结果。

`runner.py` 提供 `run_agent_trials` 重复运行真实 AgentRunner，以及只调用一次模型的 `run_single_turn_baseline`/`run_baseline_trials`。单轮基线只看固定 schema 和口径提示，生成 SQL 后使用同一受控查询服务执行，模型没有第二次修正机会。`python -m trust_agent.eval score` 可无 API key 对 JSONL 预测离线评分；`baseline` 和 `agent` 命令需要模型 API key。`agent` 支持模型、重复次数、题目数量、报告与事件数据库路径，每次 trial 使用独立 run ID。报告包括每次状态、结果正确性、Trace 检查、耗时、token、工具次数和按类别汇总；行为题及 Q06 的文字限定始终标记 `needs_review`，不能自动算通过。

### 为什么这样设计

SQL 文本相异不代表答案不同，所以数值题按执行结果判分。Trace 的作用是证明“正确数字来自哪个已完成工具调用”，而不是仅对最终文字做关键词匹配。多次 trial 用于观察模型随机性；报告把自动评分与待人工核对分开，避免把拒答题因缺少数值金标而错误计为成功。

### 验证与当前限制

运行 `.venv/bin/pytest -q tests/test_eval.py`，本阶段实测 **11 passed**；运行 `.venv/bin/ruff check src/trust_agent/eval tests/test_eval.py scripts/prepare_data.py`，结果 **All checks passed**；`python -m trust_agent.eval agent --help` 已验证命令参数可用。集成时曾发现核心 SQL 白名单误拦截 `AND`/`CASE`，已由 SQL 模块修复。修复后使用真实 TLC 数据库、九条金标 SQL 和 `python -m trust_agent.eval score` 重跑：**9/9 数值结果匹配，8 条标记 pass，Q06 因现金小费限制仍需人工核对说明而标记 needs_review，0 条 fail**。这验证的是评分器与查询边界，不是 Agent 实际准确率；尚未使用模型运行 `agent` 或 `baseline` 命令。

### 面试时如何讲

“我把评测分为任务金标、结果评分和执行轨迹三层。候选 SQL 必须经过线上同一 SQL 策略执行，然后按结果而非字符串比较；Trace 进一步证明工具调用完整、查询确实完成且答案引用了该查询。模型每题运行多次，数值题自动评分，因果和数据不可得的文字判断保留人工审核。”

### 后续工作

当时只有 12 题首批开发题；后续已按下方 P6 扩展阶段增加 80 题并冻结留出集。报告仍未实现自动文字蕴含判断和成本金额估算；这两项需要人工 rubric/定价快照，不能用词语匹配或猜测价格替代。

## 阶段 P1：自行实现 Agent loop 与事件重放（2026-09-24）

### 完成内容与数据流

`src/trust_agent/loop.py` 中的 `AgentRunner.run` 建立或延续 `RunState`，追加用户请求，再进入 `_loop`。每轮依次记录 `loop_started`、收取已完成的异步记忆、调用 `ContextBuilder`、流式消费标准化的 `ProviderEvent`。工具参数增量只产生 `tool_call_delta` 事件；仅在 `tool_ready`（供应商报告单个调用参数已完整）后登记调用 ID、检查工具预算并启动任务。多个工具执行可重叠，结果按模型给出的调用顺序写回下一轮上下文。若模型给出最终文字，运行时要求有已完成 SQL 查询的 `query_id` 引用，或明确承认无法核实/请求澄清。

`src/trust_agent/store.py` 用 SQLite 追加事件，并保存可丢弃的状态快照。`replay(run_id)` 从 `run_started`、`model_completed`、`tool_finished`、压缩事件和终止事件重建会话；即使工具先于模型流结束，仍按调用 ID 配对。`run_id` 用于定位会话，`seq` 是全局单调事件号。查询结果附 `query_id`、结果 SHA-256 与固定数据快照版本，方便从答案返回原始事件。

### 设计原因与验证

这是“模型负责选择动作，程序负责边界”的结构。`response.function_call_arguments.delta` 只能展示进度，不能解析执行；只有完整事件才有可验证的 JSON。`tests/test_loop.py` 用假模型测试两次工具调用后回答、部分 JSON 不执行、完整调用在模型流尚未结束时开始执行、工具预算阻断、会话续问和事件重放。`tests/test_context.py` 检查上下文压缩不删原始历史。离线演示命令为 `.venv/bin/trust-agent demo`，本机返回 2025 年 1 月 **3,356,067** 条、2 月 **3,443,673** 条合格行程，最终答案含真实 `query_id`。

### 面试时如何讲

“我没有依赖图框架实现循环。供应商适配器只产出规范事件，运行时独立处理轮次、工具预算、并发、完成条件和 Trace。工具调用完整时可以开始执行，但半截 JSON 绝不会触发工具。每个关键转换都追加事件，所以答案可回溯到具体查询。”

### 局限

状态快照便于快速继续，事件可重建已完成历史；**进程崩溃后正在执行的模型或工具请求尚不会自动重试/恢复**。当前答案校验确认至少一个有效查询引用，并没有逐句核验所有自然语言数值断言；评测中的文字限定仍需人工审核。

## 阶段 P2：真实供应商适配器、CLI 与 SSE 接口（2026-09-24）

### 完成内容与实现

`src/trust_agent/provider.py` 使用 OpenAI Responses API 流，把 `response.output_text.delta`、工具参数增量、单个完整工具调用、`response.completed` 标准化为四类事件。完整模型输出保留在 Trace 中，下一轮继续传回，避免丢失模型的工具调用或推理项。CLI 在终端显示文本和工具阶段；`src/trust_agent/api.py` 提供 `POST /runs`、续问、查询状态、取消与 `GET /runs/{id}/events`。HTTP 启动任务后由后台协程继续运行，SSE 客户端断开仍可用 `after=seq` 读取已持久化事件。

### 验证与面试讲法

`tests/test_provider.py` 使用假 Responses 流检查增量、完整调用和 usage 的归一化，不消耗 API。`tests/test_api.py` 检查 SSE 首次读取、断线游标重放和最终状态。面试可讲：“传输层只订阅事件存储；运行任务不依赖当前 SSE 连接，因此前端掉线不会使已产生的 Trace 消失。模型接口在一个适配器内，主循环不认识 SDK 对象。”

### 未完成的真实环境验证

本机当前没有 `OPENAI_API_KEY`，因此尚未进行真实模型请求、供应商故障注入或延迟/费用测量。接口代码和假流测试通过，**不能据此声称线上模型效果**。服务进程退出会中断后台任务；目前只支持从持久事件重放历史，尚未实现跨进程任务恢复。

## 阶段 P4：上下文、显式 Memory 与按需 Skill（2026-09-24）

`src/trust_agent/context.py` 只在模型输入超过字符预算时摘要旧项，保留最近交互；旧查询 ID 由程序确定性追加到摘要。原始 `RunState.history` 与事件不删除，压缩动作另存 `context_compacted`。当前字符预算是近似上限，尚未实现精确 tokenizer 预算。`src/trust_agent/memory.py` 异步提取用户明确写出的“请记住/以后请”内容，落在 SQLite；提取结果只在下一轮边界读取。此版刻意不从任意工具输出中自动生成长期记忆。`load_skill` 从受控名称表读取本项目诊断流程，并返回版本和内容哈希；外部文本在系统指令中明确按低信任数据处理。

验证：`tests/test_context.py` 确认压缩后完整历史仍在、查询 ID 未丢；`tests/test_loop.py` 验证短运行结束后显式记忆也持久化。面试可讲：“Memory 是带来源的可选上下文，不能在模型响应进行中突然注入；Skill 是渐进加载的任务方法，不拥有工具权限。”

## 阶段 P7：离线演示轨迹与开发工具配置（2026-09-24）

`scripts/export_demo_traces.py` 经真实数据查询服务导出三条**脚本化假模型**轨迹：正常分析、文件读取 SQL 被拒、无证据数值回答被拒后改为无法核实。分别位于 `docs/traces/`，可逐条查看 `seq`、工具调用、错误与终止条件。`docs/development_tools.md` 记录已安装的安全 Skill、两个只读官方文档 MCP 与本项目 MCP。项目初始化为 Git 仓库并设为本机 trusted 后，`codex mcp list` 已显示三个 MCP 为 enabled；此前 P5 中“尚未列出项目级 server”的记录是配置完成前的历史状态。

验证命令：`.venv/bin/python scripts/export_demo_traces.py`。三条轨迹均以 `run_completed` 结束，其中危险 SQL 产生 `tool_failed`，缺证据轨迹产生 `answer_rejected`。这些轨迹证明协议与控制流，不代表真实模型具有相同表现。正式简历指标仍须在冻结留出集上运行真实模型后填写。

## 阶段 P6 扩展：92 题冻结评测集（2026-09-24）

### 完成内容

保留原始 `evals/gold_cases.jsonl` 的 12 题不变，新增 `evals/expanded_cases.jsonl` 的 80 题：70 条带参考 SQL 和冻结结果的数值题、10 条带行为 rubric 的题。合并后 `evals/dev_cases.jsonl` 为 72 题，`evals/heldout_cases.jsonl` 为 20 题，占总题数 21.7%。新增题按时间、地理关联、机场路线、支付、拥堵费、数据质量、分布、贡献分析和行为边界九类组织。70 条数值题使用 70 条不同 SQL；题目要求在粒度、联结方向、分母、分位数、异常过滤、按日归一化等方面变化，而非只替换日期或区域 ID。

`scripts/verify_eval_suite.py` 检查数据库快照与源清单哈希、初始题未改、开发/留出拆分、问题文本唯一性，并把全部 79 条数值金标通过**生产 QueryService** 重新执行。任何源文件、清洗口径、SQL 策略或金标改动造成结果不符，都应先审题再修改冻结资产。

### 如何实现及为什么

先根据官方数据字典和项目指标口径手工设计题族，再用固定 DuckDB 快照生成数值预期；随后通过与 Agent 相同的 SQL 白名单、只读查询进程和结果评分器做独立验证。开发集可用于修复提示、工具和运行时；留出集只在阶段冻结后运行并完整报告，避免针对已知答案调参。行为题包含三月无数据时不能把空结果解释为零、没有身份字段不能算去重人数、不能读任意本地文件或执行 DROP，以及观察性数据不能证明天气/拥堵费因果。

### 本次验证结果

在项目根目录运行 `.venv/bin/python scripts/verify_eval_suite.py`，实测输出为“12 original unchanged; 80 new cases; 72 dev / 20 heldout；production QueryService: **79/79 numeric gold SQL results match**”。`13` 条行为题只验证了题目格式，后续需要人工按 rubric 审核真实回答。新增金标通过率是**题集和评分器自检结果**，不是模型或 Agent 的准确率；本阶段没有对 92 题调用模型。

### 面试时如何讲与限制

“我让评测数据有明确版本和拆分，数值题的标准答案来自真实 SQL 执行，且在生产查询边界上可重复验证；对空结果、歧义、越权和因果题使用人工 rubric，避免把‘会写 SQL’误当成可信答案。留出集是在开发前冻结的，后续只用来报告最终效果。”当前题集仍只覆盖两个月纽约黄色出租车记录；它能检验这个数据域内的 Agent 行为，不能直接代表其他企业数据库上的性能。正式简历成绩须真实运行 Agent 与同模型单轮基线、重复 trial、人工审核行为题后才能填写。

## 阶段 P7 验收修正：上下文预算与交付状态（2026-09-24）

### 本次改了什么、为什么

`ContextBuilder.build` 原来只计算未压缩的 `active` 历史长度，已有摘要可能让发给模型的输入超出设定字符预算；此外固定保留最近十项，遇到连续较大的工具结果时仍可能超过预算。现在先计算**摘要加当前历史的完整模型视图**；若超过预算，保留摘要空间并逐步缩小最近窗口，同时避免从一个没有匹配调用的 `function_call_output` 开始。原始 `RunState.history` 和 EventStore 没有被裁剪。字符数仍只是 token 用量的近似指标；极低预算且单条最新消息本身超长时，视图可能仍超过限制，需要供应商级 tokenizer 或单项截断策略。

新增 `test_compaction_counts_existing_summary_and_reduces_recent_window` 验证旧摘要会计入预算、最近窗口会收缩、最新问题和完整历史都被保留。更新 README 的评测集数量与 `docs/design.md` 的真实代码映射和状态表，避免把已完成的 92 题资产写成未来计划，也避免把没有 API key 的离线测试写成真实模型成绩。

### 如何验证、怎样给面试官解释

在项目根目录运行 `.venv/bin/python -m pytest -q`、`.venv/bin/ruff check src tests scripts`、`.venv/bin/python scripts/prepare_data.py --verify-only`、`.venv/bin/python scripts/verify_eval_suite.py`。本次全量测试 **48 passed**，Ruff **All checks passed**，三个原始数据文件哈希均匹配，数值金标复核 **79/79**。这验证固定题目的 SQL 与结果一致，**不是 Agent 模型准确率**。此外 `.venv/bin/trust-agent demo` 用真实查询完成两次工具调用；`scripts/export_demo_traces.py` 重新导出正常、危险 SQL、缺少证据三条离线轨迹。

面试时可解释：“我把上下文压缩当成视图变换：Trace 是事实来源，模型输入才做裁剪和摘要。预算要把已有摘要一起算，工具调用和结果的配对也不能随意拆散。我加了低预算回归测试。真实模型评测尚需 API 凭证，我不会把金标校验率说成 Agent 准确率。”

## 阶段 P2 回归修正：按真实 SDK 事件派发工具（2026-09-24）

### 发现与修复

复核本机 `openai` 2.x 的生成类型时发现，`ResponseFunctionCallArgumentsDoneEvent` 只有 `arguments`、`item_id`、`name` 等字段，**没有**先前假测试所用的 `item.call_id`。原实现会在真实流上抛出属性错误。`provider.py` 现在继续把参数 delta 当展示事件，但在 `response.output_item.done` 收到完整 `function_call` 输出项后，才读取 `call_id/name/arguments` 并发出 `tool_ready`；如 SDK/网络流省略该事件，还可从 `response.completed.output` 恢复一次完整调用。通过 `ready_ids` 去重，避免两个事件重复执行同一工具。状态为 `incomplete` 的输出项不会派发。

### 验证与面试讲法

`tests/test_provider.py` 现在直接用安装版 SDK 的 `ResponseFunctionCallArgumentsDoneEvent` 和 `ResponseOutputItemDoneEvent` 类型构造测试流，覆盖正常派发和完成响应兜底；本次单项测试 **2 passed**，全量回归 **49 passed**，Ruff **All checks passed**，源文件哈希与 **79/79** 数值金标复核仍通过。`codex mcp list` 显示本项目及两个官方文档 MCP 为 enabled。这只证明本机 SDK 事件结构与适配代码一致，仍需真实 API key 才能做端到端模型验证。面试可讲：“流式参数完成不等于完整工具输出项；我用 SDK 生成类型检查事件契约，并在输出项完成时派发，避免半截参数或缺少 call_id 导致误执行。”

## 阶段 P2/P6：接入兼容 Chat Completions 网关并做真实小样本（2026-09-24）

### 完成了什么

用户提供的服务使用 `/v1/chat/completions`，不是 Responses API。新增 `src/trust_agent/chat_provider.py` 实现同一 `ModelProvider` 契约；`config.make_provider` 通过 `TRUST_AGENT_PROVIDER` 选择适配器，`OPENAI_BASE_URL` 指向 `/v1` 基础地址。CLI/API 和 `eval baseline|agent` 现在共用供应商工厂，避免开发演示已接入但评测仍请求错误 API。密钥只通过进程环境变量传入，未写入仓库、报告或 Trace。

Chat 工具定义需把项目的平铺 `{type,name,description,parameters}` 转为嵌套的 `{type,function:{...}}`。旧历史以 `function_call`/`function_call_output` 保存；发送给 Chat 模型时，要把同轮多个调用合并进一条 `assistant.tool_calls`，随后按调用 ID 追加 `role=tool` 结果。`ContextBuilder` 现在只在调用与所有结果配齐的位置切分，防止压缩后出现没有配对调用的工具结果。原始事件依然不裁剪。

流式响应按 `delta.tool_calls[*].index` 拼接参数片段，并把片段继续作为 `tool_delta` 显示。与 Responses API 不同，Chat 流没有每个调用单独的完整事件；因此待整轮正常结束后，检查调用 ID、函数名和 JSON 对象，再发 `tool_ready`。`length`、过滤或缺少完成原因均不执行工具。本次真实网关探针有一个重要兼容细节：虽然返回了工具调用，`finish_reason` 却是 `stop`；适配器接受这种**流已完整结束且调用参数合法**的组合。用量的 `prompt_tokens/completion_tokens` 归一化为本项目的 `input_tokens/output_tokens`，报告保留请求型号和实际返回型号。

### 如何验证，以及失败给我们什么信息

`tests/test_chat_provider.py` 使用本机 OpenAI SDK 的 `ChatCompletionChunk` 构造多工具片段、用量末块、截断和消息转换测试；`tests/test_context.py` 检查多工具批次不会被压缩拆开；`tests/test_config.py` 检查供应商选择。项目全量测试在接入后为 **57 passed**，Ruff **All checks passed**。不需要密钥即可复跑这些测试。

真实网关的普通请求、流式工具调用和 `assistant.tool_calls → role=tool` 续轮均验证成功。请求名 `Qwen3.5-Turbo` 的响应 `model` 字段却是 `Qwen3.5-0.8B`，因此任何效果报告都必须标出**实际型号**。用这一实际型号对开发题做了极小样本试验：Q01 单轮基线一次输出非单一 JSON 而失败；Q01 Agent 一次曾成功调用查询，但加了用户未要求的支付方式过滤，且没有完成证据引用；Q10 Agent 一次也未在预算内完成。这些结果说明传输协议与工具闭环已打通，同时暴露模型能力/路由问题。三个 trial 不足以给出总体准确率，更不能写简历提升百分比。真实试验文件留在被忽略的 `runtime/`，没有提交临时密钥或未经审查的服务输出。

### 面试时如何讲、下一步

“同一个 Agent loop 接两个模型协议，核心状态机不认识供应商对象。Chat Completions 的工具参数要按 index 聚合，且必须把一次多工具调用还原为一个 assistant 消息和多个 tool 结果。通过真实网关探针我发现非标准停止原因和模型别名路由，修了兼容层，并让评测报告记录实际型号。首轮失败被完整记录，没有把接口成功当成任务成功。”下一步需确认可用于评测的实际模型，在开发集做有限 trial，再冻结配置运行留出集并人工审核行为题；不能把小样本失败转写成完整评测结论。

## 阶段 P6 迭代：真实模型筛选、评分器校准和人工复核入口（2026-09-24）

### 本阶段做了什么

在第二个兼容 Chat Completions 的网关上，分别以 `qwen3.8-max`、`qwen3.8-flash` 运行了 Q01/Q02/Q04/Q05/Q10 五道**开发题**的单轮基线和 Agent；每题每配置只运行一次。Max Agent 原始报告看起来有两条失败，但沿 `run_id` 回看实际 SQL、查询结果、最终回答和金标后，发现评分器本身造成假阴性。Q04 返回列的次序不同，工作日机场时长和样本数仍可与金标对应；Q05 返回信用卡计数与信用卡+现金分母，回答再计算百分比。前者可以在确定性结果比较中允许列排列和展示精度误差；后者需要人核对回答是否正确使用了分子、分母，所以不能自动 pass。Q02 的查询多返回了排行其他行，第一名虽然正确，也需要审查最终回答是否只提取了用户所需结论；Q10 的超时间范围答复本来就是行为 rubric 题。

Q05 基线初始报告中的 fail 还暴露了另一种评测器问题：查询结果中的 `Decimal` 类型在序列化/结果比较时产生假阴性。评分器现接受可验证的 Decimal 数值字符串，并须对原始预测重评，不能把这个 fail 直接算作模型缺陷。另一个 Q08 基线样本周度度量值匹配但分组标签不同；评分器转为 `needs_review`，由人核对周标签与时间桶口径，避免只比较数值或直接误判失败。每次修评分器都保留原报告、复评分数和修正原因，避免在开发集上无记录地改口径。

单轮基线当前在数据库执行前生成文字说明，不能看到最终查询行。五题原始输出中，多个基线说明只解释查询方法，没有最终数值。因此基线与 Agent 现在只能公平比较 SQL 结果正确性及调用开销；若要比较“完整回答”，必须为基线预先定义统一的查询结果呈现步骤并单独评估，不应直接拿两条路径的任务完成率相减。

`eval/scoring.py` 在有界搜索内比较列排列、忽略结果行顺序，金标结果是候选结果前缀或候选提供可核验的原始分子分母时返回 `needs_review`。即使数值查询完全匹配，也只记录 `sql_correct=true`，最终任务状态等待人工核对答案文字。这个状态表示“自动评分信息不足”，不是成功。`TrialScore` 增加 `human_review`，汇总同时显示 `passed/failed/needs_review`、`auto_scored/human_reviewed`，并让 `task_completion_rate_on_decided` 在任何 trial 待审时保持空值，避免选择性分母。`model_failed` 的 token 用量也被记录并计入汇总，模型流中途截断不会使消耗凭空消失。

`eval/review.py` 新增两条不调用模型的路径。`rescore_report` 读取原报告中的 `run_id`，从 SQLite EventStore 回放 Trace，再以当前评分器重算，保留实测延迟和模型元数据；因此评分规则变更后无需耗费 token 重跑模型。`apply_reviews` 只允许把 Trace 未失败的 `needs_review` trial 改为 pass/fail，要求精确的 case ID、trial、run ID、审核人和非空理由，并拒绝重复审核。CLI 分别提供 `rescore` 和 `review`。人工决定属于审核人的判断，程序只校验其记录是否完整；当前试测没有填写人工结论。

### 如何复现与读取结果

先固定 `TRUST_AGENT_PROVIDER=chat_completions`、模型名和 `/v1` 基础地址，并通过环境变量提供自己的密钥。`TRUST_AGENT_MAX_OUTPUT_TOKENS` 控制单次 Chat 输出上限；`TRUST_AGENT_CHAT_EXTRA_BODY` 可传兼容网关需要的 JSON 参数。调试先用开发题子集，报告和 SQLite Trace 都留在被忽略的 `runtime/`。`python -m trust_agent.eval rescore --cases evals/dev_cases.jsonl --db data/nyc_taxi.duckdb --report runtime/agent_report.json --state-db runtime/eval_agent.sqlite3 --out runtime/rescored.json` 可在已存 Trace 上重评。人工审核 JSONL 的结构及 `review` 命令见 `docs/eval_plan.md`。切换评分器后要记录版本，保持原始报告，不能只留下修正后的分数。

旧版评分器对五题 Max Agent 的重评是 **Q01/Q04 SQL 匹配并标为 pass，Q02/Q05/Q10 needs_review，0 fail，5/5 Trace 检查通过**。这两个自动 pass 不能证明最终文字无误；新版评分器把 SQL 匹配的任务状态也保留为 `needs_review`，另用 `sql_correct=true` 记录数值查询结果。五题都没有人工任务完成判定，旧报告的 `2/2` 已判定完成率不代表五题完成率。Max 基线原报告为 3 pass、1 fail、1 needs_review；Flash 基线初始为 1 pass、3 fail、1 needs_review，Flash Agent 初始为 0 pass、3 fail、2 needs_review。Flash 报告还没按新评分器重评，不能用这些数字证明 Max 优于 Flash，更不能推出 92 题准确率、成本优势或简历提升比例。`qwen3.8-max-0902` 的 Q01 单轮基线探针已通过，实际模型名与请求一致；留出集尚未触碰。下一步需冻结评分器/预算/代码，再做重复试验和人工 rubric。

### 面试时如何讲

“真实试测除了验证模型接入，还验证了评测器。我发现 SQL 结果在列顺序和展示精度上可以等价，而原始分子分母证据不能被机器直接当成最终百分比正确。于是把确定性判分和待人工复核分开，并做了可回放重评：评分规则修正时复用已保存 Trace，不重算模型，也保留原始报告和审查理由。当前只有开发集五题筛选，我会如实给出分母和待审数量。”

## 阶段 P6 迭代：可续跑评测日志（2026-09-24）

### 完成内容与实现

正式开发集有 72 题，每题重复试验时，一个进程中断不能让所有已完成 trial 的成果丢失。`eval/runner.py` 的 `TrialJournal` 把评测当作可追加的 JSONL 日志：首行固定实验元数据，每个完成的 trial 独立写一行并 `flush`/`fsync`；基线同时保存模型预测，Agent 保留可定位 SQLite Trace 的 `run_id`。`eval/__main__.py` 的 `baseline`、`agent` 新增 `--checkpoint` 和 `--resume`。恢复时按 `case_id+trial` 跳过已完成项，继续生成最终完整报告，而不是把多个局部报告手工拼接。

日志头用于拒绝错配：题集 SHA-256、题目与重复序号、请求模型、供应商类型与配置哈希、数据文件路径/大小/修改时间，以及 Agent 的轮次、工具、token、时间预算与 Trace 数据库路径必须一致。密钥不在头部或 trial 行中。恢复还拒绝损坏 JSON、未完成行、重复键和 Agent Trace 丢失。若模型刚执行完但日志尚未落盘就崩溃，可能留下孤立 Trace；恢复会重新运行这一 trial，这比把没有评分的动作当作成功更可靠。

使用示例：`python -m trust_agent.eval agent --cases evals/dev_cases.jsonl --db data/nyc_taxi.duckdb --model "$TRUST_AGENT_MODEL" --repeats 3 --state-db runtime/dev_agent.sqlite3 --checkpoint runtime/dev_agent.jsonl --out runtime/dev_agent_report.json`；中断后原命令添加 `--resume`。基线同理，并设置 `--predictions-out runtime/dev_baseline_predictions.jsonl`，便于之后运行 `eval rescore --predictions ...`，无模型调用地修正旧评分。这样才可把原始预测、Trace、重评分和人工审查连成可追溯链条。检查点不是任务跨进程自动续执行：它只恢复**已完成 trial 后的下一题**，不接管中断时正在跑的单个模型请求。

### 首批 12 题的真实基线记录

固定版本 `qwen3.8-max-0902` 的 Q01 基线探针确认了请求和响应型号一致。随后在原始 Q01–Q12 上做每题 3 次单轮基线，并保存原始预测；修正 Decimal 数值字符串、周标签待审和最终文字待审规则后，**用原预测重评，没有重跑模型来改写试验**。36 次里，20 次数值 SQL 匹配、5 次不匹配、2 次周标签需核对，另 9 次是行为题。`20/25` 是已确定数值 SQL 的命中率；任务状态为 5 fail、31 needs_review、0 pass，尚未人工评阅，不能叫作 80% 任务完成率。平均延迟约 3.93 秒，合计 10,794 输入 token 和 5,901 输出 token。Agent 同批次以三次重复和每 trial 8 轮、16 工具、30,000 总 token、120 秒预算运行中；此处尚无 Agent 汇总。这个阶段能展示如何在真实试验后追查评分假阴性，同时坚持保留预测和各版评分结果。留出集未参与。

## 阶段 P6 复核：保守评分、证据包与首批 Agent 结果（2026-09-24）

### 本阶段的代码与设计

真实试验暴露了“SQL 查到正确数据”和“最终回答正确”之间的差距。`eval/scoring.py` 的 `sql_correct` 只说明候选 SQL 结果能否被金标直接确认；即使为 `true`，任务 `status` 仍是 `needs_review`，直到审核最终文字。周度数值相同而标签写法不同、返回多余排序行、引用多个查询计算百分比、返回原始比例后在答案换算，都先标为待复核，绝不自动通过。新版 `numeric_case` 让汇总显式展示数值题的 true/false/undecided 与行为题数量，`numeric_oracle_coverage` 说明自动结论覆盖多少数值 trial；有任何待审 trial 时任务完成率为 `null`。当前评分规则为 `2026-09-24-v5`。

新增 `scripts/export_review_queue.py`：从报告、冻结题集和 Trace（或基线预测）导出 `needs_review` 的逐题证据与空白审核模板。它核对题集 SHA、trial 身份和 Trace 存在性，展示问题、金标或行为 rubric、答案、所引 SQL 和查询结果。审核人自己判断后填写 `decision/reviewer/reason`，再用 `eval review` 合并；脚本不会替审核人下结论。基线预测未保存 SQL 执行结果，导出时明确标记该限制。

### 实测与限制

固定型号 `qwen3.8-max-0902` 对原始 12 题每题运行 3 次。基线原预测在 v5 下无模型重评：36 次中 27 次数值题有 20 次 SQL true、5 次 false、2 次 undecided，另 9 次行为题；31 个任务待审、5 个失败、0 个已审通过。Agent 原 Trace 在 v5 下无模型重评：36 次中 27 次数值题有 15 次 SQL true、0 次 false、12 次 undecided，另 9 次行为题；36 个任务待审、0 个已审通过。Agent 的 12 个 undecided 包含多条查询推导出的结果；**两者自动判定覆盖率不同，不能据此说 Agent 更准确**。基线平均每 trial 约 3.93 秒、总输入/输出 10,794/5,901 token；Agent 约 10.57 秒、177,759/19,189 token 与 100 次工具调用。这是首批开发题的真实调用开销，不代表 92 题成绩或任务完成率。两条路径都已导出待复核 Markdown；人工评阅数目前为 0。

在项目根目录可复现证据包：

```bash
.venv/bin/python scripts/export_review_queue.py \
  --cases evals/gold_cases.jsonl \
  --report runtime/aliyun_max0902_agent_gold12x3_rescored.json \
  --state-db runtime/aliyun_max0902_agent_gold12x3.sqlite3 \
  --out runtime/aliyun_max0902_agent_gold12x3_review_queue.md \
  --format markdown
```

本阶段全量单元测试 **80 passed**、Ruff 通过；数据源 SHA 与 79/79 数值金标仍通过核验。面试可讲：“我保存每次模型运行的原始 Trace，评测规则改版时重放原结果而不重抽样；SQL oracle 命中与最终答案通过分开统计，并显示未判定的分母。长批次用配置校验和逐条落盘续跑，审核人能从答案一路追到 query_id、SQL、结果和数据版本。”下一步在 v5 规则下完成 72 道开发题与冻结的 20 道留出题，人工复核后才可报告端到端任务完成率。

## 阶段 P7 回归：运行时恢复、证据门禁与题集审计（2026-09-24）

### 发现与实现

对旧版开发集扩大试验时，代码审查发现比模型正确率更重要的边界问题，因此中止旧批次并保留检查点作诊断。`loop.py` 原先把“无法核实”视为无条件放行，模型可以写“无法核实，但有 300 万单”而没有查询证据。现在引用真实 `query_id` 才能支撑数值结论；无证据拒答只允许不含数值结果的说明，日期范围可用于解释数据覆盖。正则仍只是保守门禁，中文语义与因果限定还要人工 rubric 核对。

原 `EventStore.memories()` 会把任意 run 的显式“请记住”内容注入之后所有 run。当前 API 没有身份体系，不能把不同独立请求默认视为同一人。`store.py` 新增 `scoped_memories`，用 `(source_run_id, content)` 去重并迁移旧记录；Agent 只读取当前 run 的记忆。这样同一 run 的续问仍可使用该记忆，另一个 run 看不到它。未来要提供跨会话记忆，需要先有身份、授权、撤销及租户隔离。

崩溃恢复以前只拿最后快照；如果 `model_completed` 已写入事件但快照还没保存，会丢失刚完成的模型输出，可能重复调用工具。现在 `run()` 先用 `EventStore.replay` 重建，再在确实中断的 episode 中补齐缺失的只读工具结果；已持久化的 `tool_finished` 不再重复执行。续聊的第二个 `run_started` 会清空旧答案并恢复 `running`，启动事件回调出错也由 `finally` 清理 `active`。恢复延续已消耗的轮次、工具和 token 预算。若只读工具实际执行完毕、进程恰好在写入 `tool_finished` 前崩溃，日志无法证明其完成，恢复可能重执行一次；工具保持只读是必要前提。

`chat_provider.py` 现在向流式 Chat Completions 请求 `stream_options.include_usage`，并在网关仍未返回用量时失败，避免把未知 token 当 0 绕过预算。真实固定型号探针返回 296 输入、120 输出 token，说明当前网关支持。`eval/__main__.py` 的检查点再加入源代码 SHA-256：新增、删除、改名或修改 `src/trust_agent/**/*.py` 都会使 `--resume` 拒绝接续，避免把不同版本的 trial 混在一个报告。

题集审计也找到开发题题意与金标不一致：E003 的“8–9 点”容易被理解为一小时，而金标覆盖至 09:59；E025 的“两机场分别”容易被理解为各机场单独统计，而金标是两机场合计。另有 21 道题的金标含题面未明确要求的数量/样本量。只修订 `expanded_cases.jsonl` 和 `dev_cases.jsonl` 的 23 道开发题提问，不改 SQL 或预期结果；原始 12 题与 20 道留出题没有修改。新开发集 SHA-256 为 `b970ae38829e4c4a7e6968fbcf4064314b95ff1c0e1be95afff1476a9bdb9be5`。旧检查点因题集和代码指纹不匹配，必须保留作诊断，不能强行恢复为新版正式试验。

### 验证与面试讲法

全量测试 **99 passed**、Ruff 通过，79/79 数值金标经生产查询服务重新执行全部匹配。新增恢复测试覆盖事件已写但快照滞后、已完成工具不重执行、续聊清空旧答案、回调失败释放 active、不同 run 的记忆隔离；证据门禁测试覆盖无依据数字与纯拒答。修复后的真实网关 Q01 Agent 冒烟查询成功、Trace 合格、token 非零；用无效密钥 `--resume` 得到与原报告完全相同的 JSON，证明该 trial 未再请求模型。

面试可讲：“我把事件日志视为事实来源，快照只是缓存；恢复时区分已入日志的结果和未完成的只读工具。一次扩大评测暴露题目歧义和运行时边界，我没有把不可靠成绩写进简历，而是停下批次、保留诊断证据、修订题集和代码后重新冻结。检查点同时锁定数据、题集、模型设置与源码指纹，防止跨版本混跑。”

## 阶段 P6 实测：修复版开发集 Agent 全量运行（2026-09-24）

修复版 Git `57d0286` 与题集 SHA `b970ae38829e4c4a7e6968fbcf4064314b95ff1c0e1be95afff1476a9bdb9be5` 固定后，72 道开发题各运行 3 次。`eval agent` 为每个 trial 创建独立 run ID，把原始模型与工具事件写入 SQLite，并将评分追加到检查点。216 个 trial 全部完成；随后执行 `eval rescore`，从原 Trace 无模型重算，216 条核心分数与汇总均和原报告一致。这验证了评分器的可重复性，而不是证明最终答案无误。

186 个数值 trial 中 137 个 SQL 结果可直接匹配金标、2 个直接不匹配、47 个不能自动判定；30 个行为 trial 只按 rubric 待审。总状态为 13 fail、203 needs_review、0 人工 pass；Trace 结构通过 205/216。平均每 trial 约 10.01 秒，总输入 925,640 token、输出 101,330 token、505 次工具调用。`137/139` 只是**已自动判定的 SQL 子集**，覆盖 139/186（74.7%）；不得叫作开发集准确率，更不能叫作端到端任务完成率。失败和待审队列要保留完整分母。当前同型号单轮基线顺序运行中，留出集尚未触碰。

面试可讲：“我在调试后才冻结代码和题集，失败 trial 也保留；报告同时给出 SQL true/false/undecided 与行为题数，说明自动评分覆盖率。运行后的 Trace 可无模型重评并逐项核对，人工判分单独留痕。”

## 阶段 P6 实测：同口径开发集基线（2026-09-24）

在 Agent 完成后，保持源码指纹、题集 SHA、模型 `qwen3.8-max-0902`、数据和评分器不变，运行单轮 Text-to-SQL 基线 72 题 × 3 次。它先让模型输出 SQL 与说明，再由同一个只读查询服务执行 SQL；模型在生成说明时看不到执行结果。原始预测保存在 JSONL，216 次 trial 的报告可从预测无模型重评；重评分与原报告逐项一致。

186 次数值 trial 中，基线 SQL true/false/undecided 为 140/22/24，另有 30 次行为题；22 fail、194 needs_review、0 人工 pass。平均延迟约 4.15 秒，总输入/输出 64,647/35,248 token。Agent 为 137/2/47，调用 505 次工具，耗时与 token 明显更高。两组的自动判定覆盖率分别为 139/186 和 162/186，因此直接比较 137 与 140 会混淆未判定样本。基线的文本生成时尚未见 SQL 行，也不能与 Agent 的最终答案做公平的端到端比较。

用 `scripts/export_review_queue.py` 分别导出了开发集 Agent 203 条、基线 194 条待审试验的证据包。审核人须核对 SQL 行、分组/分母、最终文字、证据引用及行为题限定，再填写审核决定；程序没有假造人工通过率。面试可讲：“我在同一固定模型下比较 Agent 和单轮 SQL，保留全部失败与未决样本，并把 SQL oracle、完整任务以及调用成本拆开报告。基线更便宜并不自动说明它能回答完整问题；Agent 的待审比例更高也不能直接判它更差。”

## 阶段 P8：冻结留出集与可审核发布（2026-09-24）

### 如何做留出集

开发集结束后保持源码 SHA `b75afc2846923676f66c083fd7006bf4020eb2482cb19461e07591d030bd4165`、评分规则 `2026-09-24-v5`、模型 `qwen3.8-max-0902`、数据快照和生成设置不变。留出集 20 题各运行 3 次；先运行 Agent，再顺序运行同模型单轮 Text-to-SQL 基线，避免同时请求造成网关超时。留出集 SHA 为 `ee6d3969da24aa6e359c7328609923cc2668a1fc2e4ca3d5466a64af69ff5106`，未根据其结果修改工具、提示词或题目。每组都保留逐 trial 检查点、报告，以及 Agent 的 SQLite Trace 或基线的原预测。

`eval rescore` 从 Trace/预测无模型复算，Agent 和基线的各 60 条核心分数及汇总均与原报告一致。四组开发集、留出集总计 **552/552** 条重评一致。复算证明报告可重现，不能证明金标或模型文字全都正确。

### 如何读结果

留出集的 51 次数值 trial，Agent SQL true/false/undecided 为 **38/4/9**，基线为 **33/18/0**；9 次行为 trial 需要 rubric。Agent 任务状态为 7 fail、53 needs_review、0 人工 pass，Trace 57/60 通过；基线为 18 fail、42 needs_review、0 人工 pass。Agent 的 7 次失败含 4 次 SQL 结果不符、3 次没有合格完成 Trace。Agent 平均约 11.41 秒，输入/输出 277,044/31,183 token，141 次工具调用；基线约 3.77 秒，17,757/8,788 token。两者自动可判的数值题分母不同：42/51 对 51/51。Agent 的 9 次 undecided 应核对计算与语义，不能算对或算错；任何 SQL true 也还要核对最终答复。

已导出留出集 Agent 53 条、基线 42 条待审证据包。审核时先看问题与冻结金标，再对 `run_id`、引用的查询、行值和回答中的时间范围、分母、限定语逐项判断。基线生成文字时没见 SQL 执行结果，审核时须明确这个产品能力限制。填入真实审核人、理由和 pass/fail 后，才能调用 `eval review` 得到完整任务完成率。这里没有填写虚构审核决定，也没有根据 token 估算未经核对的账单金额。

面试可讲：“我把 72/20 题拆分在调试前冻结，完整保留 552 次试验及失败。留出集上，Agent 多轮查询带来较多直接匹配的 SQL，但耗时和 token 显著更高，且仍有 9 次数值题需人工判断。我能回放 Trace、复算评分、定位每次查询与结果；在人工核对最终文字前，不把 SQL oracle 命中写成任务成功率。”
