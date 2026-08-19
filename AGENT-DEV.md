# Agent 开发模板

基于 HolmesGPT 项目结构提炼，适用于构建 LLM Agent 项目。

---

## 一、项目结构

```
project/
├── server.py                    # FastAPI 入口，路由注册，中间件
├── pyproject.toml               # Poetry 依赖管理
├── mkdocs.yml                   # 文档站配置
├── CLAUDE.md                    # Claude Code 项目说明书
├── docs/                        # 文档站源码
│
├── your_app/                    # 主包
│   ├── main.py                  # CLI 入口 (typer)
│   ├── config.py                # 配置加载 + 工厂方法
│   ├── common/
│   │   └── env_vars.py          # 环境变量常量
│   ├── core/
│   │   ├── tool_calling_llm.py  # Agent 核心循环 ⭐
│   │   ├── llm.py               # LLM 抽象层 (LiteLLM 封装)
│   │   ├── tools.py             # Tool/Toolset 基类 ⭐
│   │   ├── models.py            # 请求/响应 Pydantic 模型
│   │   ├── prompt.py            # Prompt 构建（多层组装）
│   │   ├── conversations.py     # 对话消息管理
│   │   ├── tracing.py           # 链路追踪（可插拔后端）
│   │   ├── usage_recorder.py    # 用量统计（fire-and-forget）
│   │   ├── safeguards.py        # 防重复调用等安全保护
│   │   ├── approval_tokens.py   # 审批令牌（JWT 防伪造）
│   │   ├── tools_utils/
│   │   │   ├── tool_executor.py # 工具执行器（懒初始化 + OAuth）
│   │   │   ├── frontend_tools.py
│   │   │   ├── oauth_tool_connector.py
│   │   │   └── filesystem_result_storage.py
│   │   ├── truncation/
│   │   │   ├── input_context_window_limiter.py  # 上下文压缩检查
│   │   │   └── compaction.py                   # LLM 摘要压缩
│   │   ├── conversations_worker/
│   │   │   ├── worker.py       # 异步对话 Worker（后台线程）⭐
│   │   │   ├── event_publisher.py
│   │   │   ├── realtime_manager.py
│   │   │   └── tool_call_worker.py
│   │   └── transformers/        # 工具结果转换器
│   ├── plugins/
│   │   ├── interfaces.py        # SourcePlugin / DestinationPlugin 接口
│   │   ├── toolsets/            # 工具集实现
│   │   │   ├── {name}.yaml      # YAML 工具集
│   │   │   └── {name}/          # Python 工具集
│   │   │       ├── {name}.py    # 工具实现
│   │   │       ├── validation.py # 安全验证（如果涉及 shell/网络）
│   │   │       └── instructions.jinja2 # 工具使用说明
│   │   ├── prompts/             # Jinja2 模板（system prompt + user prompt）
│   │   ├── sources/             # 告警源 (AlertManager, Jira, PagerDuty)
│   │   ├── destinations/        # 结果输出 (Slack, PagerDuty)
│   │   └── skills/              # 技能目录
│   └── utils/
│       ├── stream.py            # SSE 事件定义 ⭐
│       ├── pydantic_utils.py    # ToolsetConfig 基类
│       ├── auth.py              # 鉴权工具
│       └── log.py               # 日志配置
│
└── tests/
    ├── core/                    # 单元测试
    ├── plugins/toolsets/        # 工具集测试（含安全测试）
    └── llm/                     # LLM 评估测试
        └── fixtures/
            └── test_ask_holmes/
                └── {NNN}_{name}/
                    └── test_case.yaml
```

---

## 二、核心抽象层

### 2.1 Tool（工具）

```python
# tools.py
class Tool(ABC, BaseModel):
    name: str                              # 工具名，LLM 可见
    description: str                       # 工具描述，LLM 可见
    parameters: Dict[str, ToolParameter]   # JSON Schema 参数
    transformers: Optional[List[Transformer]] = None  # 结果转换器

    def invoke(self, params, context) -> StructuredToolResult:
        """
        统一的工具调用入口。所有工具执行都经过此方法：
        1. 审批检查 → APPROVAL_REQUIRED（如果未通过人类审批）
        2. 参数类型强制转换 (coerce_params)
        3. 调用 _invoke() 执行
        4. 应用 transformers
        5. 返回 StructuredToolResult
        """
        if not context.user_approved:
            approval_check = self._get_approval_requirement(params, context)
            if approval_check and approval_check.needs_approval:
                return StructuredToolResult(status=APPROVAL_REQUIRED, ...)
        params = self._coerce_params(params)
        result = self._invoke(params=params, context=context)

    @abstractmethod
    def _invoke(self, params, context) -> StructuredToolResult:
        """子类实现具体逻辑"""

    def requires_approval(self, params, context) -> Optional[ApprovalRequirement]:
        """工具特定的审批检查。默认返回 None（不需要审批）"""
```

**ToolInvokeContext** — 污点追踪的核心载体：

```python
class ToolInvokeContext(BaseModel):
    user_approved: bool = False           # 核心标志位：污点数据是否已通过人类审批
    llm: LLM                              # LLM 实例（用于工具结果摘要）
    max_token_count: int                  # 单个工具结果的最大 token 数
    tool_call_id: str                     # 工具调用 ID（用于审批令牌绑定）
    tool_name: str                        # 工具名
    session_approved_prefixes: List[str]  # Bash 会话级批准前缀
    request_context: Optional[Dict]       # 请求上下文（user_id, headers 等）
```

**关键设计**：`user_approved` 是污点追踪的核心状态标志：
- `False`：工具调用参数来自 LLM（污点），需要经过完整验证
- `True`：工具调用已被人类审批（净化），可以跳过验证直接执行

**序列化保护**：`model_dump()` 和 `__str__()` 中 `request_context` 被自动 redact，防止敏感头部泄露到日志中。

**StructuredToolResult** 五种状态：

| 状态 | 含义 | 后续行为 |
|---|---|---|
| `SUCCESS` | 成功 | 结果追加到 messages，继续循环 |
| `ERROR` | 失败 | 错误信息追加到 messages，LLM 自纠正 |
| `NO_DATA` | 无数据 | 空结果追加到 messages |
| `APPROVAL_REQUIRED` | 需要审批 | 暂停循环，等待用户决策 |
| `FRONTEND_PAUSE` | 需要前端执行 | 暂停循环，等待前端返回结果 |

### 2.2 Toolset（工具集）

```python
# tools.py
class Toolset(BaseModel):
    name: str
    description: str
    tools: List[Tool]                     # 包含的工具列表
    prerequisites: List[...]              # 前置条件检查（按优先级排序执行）
    config: Optional[Any] = None          # 配置对象 (Pydantic Model)
    approval_required_tools: List[str]    # 需要审批的工具名/模式（支持 fnmatch 通配符）
    tags: List[ToolsetTag]               # CORE / CLUSTER / CLI 等
    type: Optional[ToolsetType]          # YAML / PYTHON / HTTP / MCP
    status: ToolsetStatusEnum            # ENABLED / DISABLED / FAILED
```

**四种工具集类型**：

| 类型 | 实现方式 | 适用场景 |
|---|---|---|
| `YAML` | 纯 YAML 定义，工具是 bash 命令 | 简单的 kubectl/helm 操作 |
| `PYTHON` | Python 类，继承 `Tool` | 复杂 API 调用、需要状态管理 |
| `HTTP` | HTTP 端点，工具是 API 调用 | 外部服务集成 |
| `MCP` | MCP 协议，动态发现工具 | 远程 MCP 服务器 |

**工具集标签过滤**：
- 通过 `toolset_tag_filter` 控制哪些工具集被加载
- 服务端 `/api/chat` 使用 `[CORE, CLUSTER]`（不含 CLI 工具集如 bash）
- CLI 使用 `[CORE, CLI]`（不含 CLUSTER 工具集如 aws）
- 实现了最小权限原则：不同入口点有不同的工具集范围

### 2.3 ToolExecutor（工具执行器）

```python
# tools_utils/tool_executor.py
class ToolExecutor:
    toolsets: List[Toolset]              # 所有工具集
    enabled_toolsets: List[Toolset]      # 已启用的工具集
    tools_by_name: Dict[str, Tool]       # 工具名 → 工具对象
    _tool_to_toolset: Dict[str, Toolset] # 工具名 → 所属工具集

    def get_tool_by_name(name, user_id) -> Optional[Tool]
    def ensure_toolset_initialized(tool_name) -> Optional[str]  # 懒初始化
    def get_toolset_name(tool_name, user_id) -> Optional[str]
```

**关键设计**：
- 工具名冲突解决：MCP 工具重名时自动加 `{toolset}__{tool}` 前缀
- 懒初始化：工具集首次使用时才执行前置条件检查
- OAuth 支持：动态注册用户级 OAuth 工具
- 实例复用：`reuse_executor=True` 时同一实例被多个请求共享

---

## 三、Agent 核心循环

### 3.1 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                      ToolCallingLLM                               │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐  │
│  │ ToolExecutor │  │     LLM      │  │ Context Limit Manager   │  │
│  │ (工具注册/执行)│  │ (LiteLLM封装) │  │ (压缩/溢写)             │  │
│  └─────────────┘  └──────────────┘  └─────────────────────────┘  │
│                                                                   │
│  call_stream(msgs, tools, ...):                                   │
│    while i < max_steps:                                           │
│      ① 处理审批决策 → _execute_tool_decisions()                    │
│         ├─ verify_token()  ← JWT 验证（防伪造审批）                │
│         └─ 以 user_approved=True 重新执行工具                      │
│      ② 处理前端工具结果 → _process_frontend_tool_results()         │
│      ③ 拒绝孤立的工具调用 → _resolve_orphaned_tool_calls()         │
│      ④ check_compaction_needed()  → 上下文超限? 压缩               │
│      ⑤ llm.completion(messages, tools)  → LLM 调用（stream=False）│
│      ⑥ 无 tool_calls → ANSWER_END → 返回                          │
│      ⑦ 有 tool_calls → ThreadPoolExecutor 并行执行（最多16并发）   │
│      ⑧ 审批检查 → 需要审批 → mint_token() → APPROVAL_REQUIRED 暂停 │
│      ⑨ 结果追加到 messages → i++ → 继续                           │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 关键设计：`call_stream()` 不是 LLM streaming

```python
# call_stream() 名字里的 "stream" 指的是"逐轮迭代产出事件流"
# 而不是 LLM 层面的 token 流
full_response = self.llm.completion(
    messages=messages,
    tools=tools,
    stream=False,  # ← 始终是 False，每轮完整调用 LLM
)
```

**两种"流"的对比**：

| | LLM 层面的 stream=True | Holmes 层面的 call_stream() |
|---|---|---|
| 机制 | LLM 逐个 token 返回 | 每轮迭代 yield 一个事件 |
| 优势 | 用户看到文字逐字出现 | 工具调用结果逐步展示 |
| 问题 | tool_calls 只在最后一次性返回 | 每轮需要完整 LLM 调用 |

### 3.3 审批流

```
LLM 调用 → 返回 tool_calls → 检查是否需要审批
    │
    ├─ 不需要审批 → 直接执行 → 结果追加到 messages
    │
    └─ 需要审批 → mint_token() 签发 JWT
                  → yield APPROVAL_REQUIRED
                  → 暂停循环，等待客户端
                  → 客户端发回 tool_decisions
                  → verify_token() 验证 JWT
                  → user_approved=True 重新执行
```

**审批令牌设计**（JWT HS256）：
```python
# 令牌绑定到 {tool_call_id, tool_name, args_hash}
# 防止审批伪造：攻击者不能用一个工具的审批去执行另一个工具
def mint_token(tool_call_id, tool_name, args_json):
    return jwt.encode({
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "args_hash": sha256(canonical_json(args_json)),  # sort_keys=True
        "iat": now,
        "exp": now + 30_days,
    }, SIGNING_KEY, algorithm="HS256")
```

### 3.4 LLM 抽象层

```python
# llm.py
class LLM(ABC):
    @abstractmethod
    def completion(self, messages, tools, tool_choice, temperature,
                   stream, response_format, drop_params) -> ModelResponse:
        """LLM 调用抽象，子类实现 LiteLLM/Boto3 等"""

    @abstractmethod
    def count_tokens(self, messages, tools) -> ContextWindowUsage:
        """Token 计数"""

    @abstractmethod
    def get_context_window_size(self) -> int:
        """上下文窗口大小"""

    @abstractmethod
    def get_maximum_output_token(self) -> int:
        """最大输出 token 数"""
```

---

## 四、FastAPI 入口设计

### 4.1 同步 vs 异步端点

**关键模式**：阻塞操作使用 `def`（不是 `async def`），由 FastAPI 自动放入线程池：

```python
# ✅ 正确：LLM 调用 + 工具执行是阻塞操作，用 def
@app.post("/api/chat")
def chat(chat_request: ChatRequest, http_request: Request):
    # 全是同步阻塞调用
    request_ai.call(messages=messages, ...)       # 阻塞 LLM 调用
    tool.invoke(params, context=invoke_context)   # 阻塞工具执行
    execute_bash_command(cmd=command_str, ...)    # 阻塞子进程

# ✅ 正确：中间件只有轻量操作，用 async def
@app.middleware("http")
async def api_key_auth(request: Request, call_next):
    key = extract_api_key(request)   # 轻量检查
    return await call_next(request)
```

| 声明方式 | FastAPI 如何处理 | 事件循环影响 |
|---------|-----------------|------------|
| `def` | 扔进 `ThreadPoolExecutor` 的线程中 | 事件循环不受影响 |
| `async def` | 直接在事件循环中执行 | 阻塞操作会卡死事件循环 |

**原则**：如果调用链中有 `subprocess.run()`、`requests.get()`、`litellm.completion()` 等阻塞操作，用 `def`；如果只有轻量 I/O（读 header、查内存缓存），用 `async def`。

### 4.2 流式路径

```python
# 生成器模式：同步生成器 + StreamingResponse
def chat(...):
    recorded_stream = stream_with_usage_recording(
        request_ai.call_stream(...),  # 同步生成器（yield 事件）
        recorder_state,
    )
    return StreamingResponse(
        stream_chat_formatter(recorded_stream, ...),
        media_type="text/event-stream",
    )
```

**注意**：生成器在 `ThreadPoolExecutor` 的线程中运行，`StreamingResponse` 在底层异步发送——两者通过 Starlette 的队列机制解耦。

### 4.3 请求上下文构建

```python
# 构建 request_context，携带以下信息：
# 1. passthrough_headers（去除敏感头部后）
# 2. user_id、conversation_id、cluster_name
# 3. 这些信息对 LLM 不可见，但工具集可以通过 context 访问
request_context = {
    "headers": extract_passthrough_headers(request),
    "user_id": chat_request.user_id,
    "conversation_id": chat_request.conversation_id,
    "cluster_name": config.cluster_name,
}
```

---

## 五、追踪基础设施

### 5.1 可插拔追踪后端

```
TracingFactory.create_tracer()
    │
    ├─ HOLMES_TRACE_BACKEND="braintrust" → BraintrustTracer
    ├─ OTEL_EXPORTER_OTLP_ENDPOINT 已设置  → OpenTelemetryTracer
    └─ 都没有设置                          → DummyTracer (no-op)
```

### 5.2 空对象模式（Null Object Pattern）

```python
class DummySpan:
    """所有方法都是空操作，零开销"""
    def start_span(self, name=None, **kwargs):
        return DummySpan()

    def log(self, *args, **kwargs):
        pass

    def end(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
```

**优势**：代码中不需要写 `if tracer:` 判断——所有代码正常调用 `trace_span.log()`，如果没有配置追踪后端，这些调用就是空操作。

### 5.3 Span 树结构

```
holmesgpt.investigation  (根 Span)
├── gen_ai.chat           (子 Span, LLM 调用)
│   ├── tool: kubectl_get (子 Span, 工具调用)
│   └── tool: prometheus_query
├── gen_ai.chat           (下一轮 LLM 调用)
│   └── ...
└── ...                   (直到调查结束)
```

### 5.4 典型用法

```python
# 创建根 Span
trace_span = server_tracer.start_trace("holmesgpt.investigation")
trace_span.log(input=ask, metadata={...})

# 在 LLM 调用处创建子 Span
with trace_span.start_span(name="gen_ai.chat") as llm_span:
    full_response = self.llm.completion(...)
    llm_span.log(metadata={
        "prompt_tokens": ...,
        "completion_tokens": ...,
        "total_cost": ...,
    })

# 结束时
trace_span.end()
```

---

## 六、对话存储模式

### 6.1 两种存储路径

| | 实时 `/api/chat` | 异步 Worker |
|---|---|---|
| 存储位置 | 客户端（前端 DB） | 服务端（数据库） |
| 数据格式 | OpenAI messages 数组 | 事件流（event + data + ts） |
| 服务端状态 | **无状态** | 有状态（Worker 持有对话） |
| 适用场景 | 前端 UI 交互 | Slack、定时任务、告警自动调查 |

### 6.2 实时路径（客户端驱动）

```
客户端 → ChatRequest {ask, conversation_history}
         │
    server.py  ← 只做加工，不存储
         │
客户端 ← ChatResponse {analysis, conversation_history}

客户端负责持久化 conversation_history
```

**设计思想**：服务端无状态，可以水平扩展。任意实例都能处理任意请求。

### 6.3 异步路径（Worker 驱动）

```
┌──────────────────────────────────────────────────────┐
│  数据库 (Conversations 表 + ConversationEvents 表)     │
│                                                      │
│  Conversations: {id, status, assignee, ...}          │
│  ConversationEvents: {conversation_id, seq, event,   │
│                        data, compacted, ts}          │
└──────────────────────────────────────────────────────┘
         │                              ▲
         ▼                              │
┌─────────────────────┐    ┌─────────────────────────┐
│ 客户端写 pending 行  │    │ ConversationWorker       │
│ (Slack/定时任务触发)  │    │ 轮询 → 认领 → 处理 → 写回 │
└─────────────────────┘    └─────────────────────────┘
```

**Worker 工作流程**：
1. `claim_n_pending_conversations()` → 认领 pending 对话
2. `get_conversation_events()` → 从 DB 读历史事件，还原成 messages
3. `build_chat_messages()` + `ai.call_stream()` → 和实时 API 完全一样的处理
4. `post_conversation_events()` → 批量写回事件
5. `update_conversation_status()` → 标记 completed/failed

### 6.4 用量记录

两种路径都通过 `usage_recorder.py` 记录用量（fire-and-forget，不阻塞响应）：

```python
# 记录到 HolmesUsageEvents 表（计费和分析用，不存 messages 内容）
dal.record_usage_event({
    "account_id": ..., "conversation_id": ..., "model": ...,
    "prompt_tokens": ..., "completion_tokens": ..., "total_cost": ...,
    "tool_call_count": ..., "duration_ms": ..., "iterations": ...,
})
```

---

## 七、Prompt 组装管道

### 7.1 多层组装流程

```
用户输入: "为什么 checkout 服务挂了？"
    │
    ▼
build_prompts() ──────────────────────────────────────────────
    │
    ├─ build_system_prompt()
    │   ├─ intro          → "你是 HolmesGPT，一个 AI 运维代理"
    │   ├─ cluster_name   → "你当前连接的是 prod-us-east 集群"
    │   ├─ skills         → 技能目录使用说明
    │   ├─ todowrite      → 任务管理规则
    │   ├─ general_instructions → 调查方法论（五个为什么等）
    │   ├─ toolset_instructions → 每个工具的用法说明（动态生成）
    │   ├─ permission_errors → 权限错误处理指南
    │   ├─ style_guide    → 回复风格要求
    │   └─ system_prompt_additions → 用户自定义额外指令
    │
    └─ build_user_prompt()
        ├─ 原始问题
        ├─ skills 上下文（时间、自定义指令）
        ├─ 附件文件内容
        └─ 图片 (vision)
    │
    ▼
build_chat_messages()
    ├─ 合并历史对话
    ├─ 更新 system prompt（如果历史中有则替换）
    └─ 追加新的 user message
```

### 7.2 条件组件

每个 prompt 组件都可以通过 `PromptComponent` 枚举 + `behavior_controls` 动态开关：

```python
class PromptComponent(str, Enum):
    INTRO = "intro"
    TODOWRITE_INSTRUCTIONS = "todowrite_instructions"
    TOOLSET_INSTRUCTIONS = "toolset_instructions"
    GENERAL_INSTRUCTIONS = "general_instructions"
    STYLE_GUIDE = "style_guide"
    # ... 每个组件可独立开关
```

### 7.3 工具说明动态生成

工具列表是运行时动态的——每个用户的配置不同，工具集不同。`toolset_instructions` 组件遍历 `tool_executor.toolsets`，为每个启用的工具集生成使用说明。

---

## 八、安全架构（污点追踪）

### 8.1 威胁模型

**核心前提**：LLM 的工具调用输出是**污点数据（tainted data）**，原因有三：
1. **间接提示注入**：攻击者通过被观测系统的数据（日志、告警、Confluence 页面等）注入恶意指令
2. **LLM 幻觉**：LLM 可能生成危险但看似合理的命令
3. **非确定性**：LLM 输出本质上是概率性的

### 8.2 纵深防御体系

```
LLM 输出 (污点)
    │
    ▼
[验证层 1] prevent_overly_repeated_tool_call()  ← 防重复调用
    │
    ▼
[验证层 2] Tool.invoke() → _get_approval_requirement()  ← 审批检查
    │
    ▼
[验证层 3] Bash: validate_command()  ← 6 层递进式验证
    │         ├─ 前缀真实性检查（suggested_prefixes 必须在命令中实际出现）
    │         ├─ bashlex AST 解析（无法解析 → 安全失败，需要审批）
    │         ├─ 逐段白名单/黑名单验证
    │         ├─ 参数级危险检测（find -exec, sort -o, uniq 输出文件）
    │         ├─ 输出重定向检测（真实文件 vs /dev/null）
    │         └─ 动态展开检测（$(), $VAR, <()）
    │
    ▼
[验证层 4] 审批令牌 JWT 验证  ← 防伪造审批
    │
    ▼
[验证层 5] Internet: SSRF 防护
    │         ├─ Scheme 过滤（只允许 http/https）
    │         ├─ IP 范围检查（阻止 loopback, link-local, private, multicast）
    │         ├─ DNS 重绑定防御（IP 钉扎）
    │         └─ 跨主机重定向头部剥离
    │
    ▼
[验证层 6] HTTP: 端点白名单（host/port/path/method 匹配）
    │
    ▼
[验证层 7] kubectl-run: shell 元字符拒绝 + 镜像白名单 + shell=False 执行
    │
    ▼
[执行 Sink] subprocess.run() / requests.get() / execute_bash_command()
```

### 8.3 关键安全原则

- **安全失败**：无法确定安全性的操作默认被阻止或需要审批
- **硬拒绝优先于软审批**：一个段的 `sudo` 硬拒绝不能被另一个段的 `$()` 展开审批覆盖
- **LLM 元数据不可信**：`suggested_prefixes` 必须在命令中实际出现，保存的前缀必须存在于 `suggested_prefixes` 中
- **审批令牌绑定**：JWT 绑定到 `{tool_call_id, tool_name, args_hash}`，防止审批伪造
- **最小权限**：工具集通过 `toolset_tag_filter` 按入口点限制范围

### 8.4 Bash 安全验证详解

```python
def validate_command(command, suggested_prefixes, allow_list, deny_list):
    # 第 1 层：前缀真实性检查
    for prefix in suggested_prefixes:
        if prefix not in command:
            return DENIED  # LLM 声称命令是 kubectl get 但实际是 kubectl delete

    # 第 2 层：bashlex AST 解析
    try:
        extractor = _build_extractor(command)
    except ParsingError:
        return APPROVAL_REQUIRED  # 无法解析 → 安全失败

    # 第 3 层：逐段白名单/黑名单验证
    for segment in segments:
        if matches_hardcoded_block(segment):
            return DENIED  # 硬拒绝（sudo, chmod, rm, ...）
        if matches_deny_list(segment):
            return DENIED
        if not matches_allow_list(segment):
            unapproved_segments.append(segment)

    # 第 4 层：参数级危险检测
    # find -exec → DENIED, sort -o → DENIED, uniq output-file → DENIED
    dangerous = check_dangerous_argv(extractor)

    # 第 5 层：复合命令检查
    if contains_compound_command:
        return APPROVAL_REQUIRED  # for/while/if → 需要审批

    # 第 6 层：动态展开检测
    if has_dynamic_expansion and is_checked_command:
        return APPROVAL_REQUIRED  # $() / $VAR / <() → 需要审批
```

---

## 九、工具开发模式

### 9.1 Python 工具集（推荐用于复杂场景）

参考实现：`servicenow_tables/servicenow_tables.py`

```python
# ① 配置类：继承 ToolsetConfig
class MyToolConfig(ToolsetConfig):
    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {
        "old_field": "new_field",    # 改名
        "removed_field": None,       # 废弃
    }
    api_url: str = Field(...)
    api_key: Optional[str] = Field(default=None)
    timeout_seconds: int = Field(default=30)

# ② 工具类：继承 Tool，实现 _invoke
class MyTool(Tool):
    def _invoke(self, params, context) -> StructuredToolResult:
        config = context.toolset.config  # 获取配置
        try:
            response = requests.get(
                f"{config.api_url}/api/endpoint",
                headers={"Authorization": f"Bearer {config.api_key}"},
                params=params,
                timeout=config.timeout_seconds,
            )
            response.raise_for_status()
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response.json(),
            )
        except RequestException as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"API call failed: {e}",
                params=params,
            )

# ③ 工具集工厂函数
def create_my_toolset(install_config: Optional[Dict[str, Any]] = None):
    config = MyToolConfig(**install_config) if install_config else MyToolConfig()
    return Toolset(
        name="my_toolset",
        description="My toolset description",
        type=ToolsetType.PYTHON,
        config=config,
        prerequisites=[
            CallablePrerequisite(
                name="connectivity_check",
                callable=lambda cfg: requests.get(f"{cfg.api_url}/health").ok
            )
        ],
        tools=[
            MyTool(
                name="my_tool",
                description="What this tool does",
                parameters={
                    "query": ToolParameter(
                        type="string",
                        description="Search query",
                        required=True,
                    ),
                },
            ),
        ],
    )
```

### 9.2 YAML 工具集（适合简单操作）

```yaml
# plugins/toolsets/my_toolset.yaml
name: my_toolset
description: My toolset
type: YAML
tools:
  - name: my_command
    description: Run a command
    parameters:
      command:
        type: string
        description: The command to run
        required: true
    command: "{{ command }}"  # Jinja2 模板，参数通过 shlex.quote 净化
```

**安全注意**：YAML 工具集的参数通过 `shlex.quote()` 净化后插入 Jinja2 模板。`shlex.quote()` 将值包裹在单引号中——这**只在非引号上下文中是安全的**。绝不能将 `{{ param }}` 放在 shell 引号内部。

### 9.3 工具开发关键原则

| 原则 | 说明 |
|---|---|
| **Thin API Wrapper** | 用 `requests` 库，不要用专用客户端库 |
| **详细错误信息** | 返回完整 API 错误 + 请求参数 + 时间范围 |
| **服务端过滤** | 绝不返回无界数据，始终加 filter 参数 |
| **配置向后兼容** | 用 `ToolsetConfig._deprecated_mappings` 映射旧字段名 |
| **类层次放置** | 新增字段放在最通用的层级，不只放在特定子类 |
| **重试用 `tenacity`** | 不要手写重试循环 |
| **安全验证独立** | 将验证逻辑放在单独的 `validation.py` 文件中 |

### 9.4 安全验证文件组织

对于涉及 shell 执行或网络请求的工具集，将安全验证逻辑放在独立的 `validation.py` 中：

```
your_toolset/
├── your_toolset.py      # 工具实现
├── validation.py         # 安全验证（独立文件，便于测试）
└── instructions.jinja2   # 工具使用说明
```

---

## 十、SSE 流式事件

```python
# utils/stream.py
class StreamEvents(str, Enum):
    ANSWER_END = "ai_answer_end"                         # 最终答案
    START_TOOL = "start_tool_calling"                    # 开始调用工具
    TOOL_RESULT = "tool_calling_result"                  # 工具结果
    ERROR = "error"                                      # 错误
    AI_MESSAGE = "ai_message"                            # AI 中间消息
    APPROVAL_REQUIRED = "approval_required"              # 需要审批
    TOKEN_COUNT = "token_count"                          # Token 统计
    COMPACTION_START = "conversation_history_compaction_start"
    COMPACTED = "conversation_history_compacted"
    FRONTEND_PAUSE = "frontend_pause"                    # 需要前端执行

class StreamMessage(BaseModel):
    event: StreamEvents
    data: dict = {}

# SSE 格式: event: {event_type}\ndata: {json}\n\n
```

---

## 十一、插件系统

```python
# plugins/interfaces.py
class SourcePlugin:
    """告警/问题来源"""
    def fetch_issues(self) -> List[Issue]: ...
    def fetch_issue(self, id: str) -> Issue: ...
    def stream_issues(self) -> Iterable[Issue]: ...       # 可选
    def write_back_result(self, issue_id, result) -> None: ...  # 可选

class DestinationPlugin:
    """结果输出"""
    def send_issue(self, issue: Issue, result: LLMResult): ...
```

---

## 十二、配置模式

### 12.1 ToolsetConfig 基类

```python
# utils/pydantic_utils.py
class ToolsetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")  # 允许额外字段，向后兼容
    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {}
    # 自动处理旧字段名映射 + 废弃警告
```

### 12.2 配置加载 + 工厂方法

```python
# config.py
class Config:
    # 从 ~/.your_app/config.yaml 加载
    # 工厂方法:
    def create_toolcalling_llm(self, dal, toolset_tag_filter, ...) -> ToolCallingLLM:
        """创建 Agent 实例：LLM 客户端 + 工具执行器 + 追踪器"""
        llm = self._get_llm(model_key=model, tracer=tracer)
        tool_executor = self.create_tool_executor(
            dal=dal,
            toolset_tag_filter=toolset_tag_filter,  # 按标签过滤工具集
            ...
        )
        return ToolCallingLLM(tool_executor, self.max_steps, llm, ...)

    def create_tool_executor(self, ...) -> ToolExecutor:
        """创建工具执行器：加载工具集 + 检查前置条件"""
```

---

## 十三、快速开发 Checklist

开发一个新 Agent 功能时：

1. **定义工具** → 继承 `Tool`，实现 `_invoke()` 和 `requires_approval()`
2. **定义配置** → 继承 `ToolsetConfig`，声明字段 + 废弃映射
3. **定义安全验证**（如涉及 shell/网络）→ 独立的 `validation.py`
4. **组装工具集** → 创建 `Toolset`，配置 `prerequisites` + `tools` + `tags`
5. **注册到执行器** → 通过 `ToolExecutor` 的 `toolsets` 列表
6. **构建 Prompt** → 在 `prompts/` 下添加 Jinja2 模板
7. **配置追踪** → 在关键节点添加 `trace_span.start_span()` + `log()`
8. **编写测试** → 单元测试 + 安全测试 + LLM 评估测试
9. **更新文档** → README + docs/ + .nav.yml