以下是基于官方 API 参考（当前最新版 **langgraph 1.2.11**，2026-08 发布）整理的全量功能函数总结，先给包架构图，再按子模块逐一列出函数签名与输入参数。

## 一、langgraph.graph — 核心建图模块

| 函数 / 类                          | 输入参数                                                     | 功能                                                         |
| ---------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `StateGraph`                       | `(state_schema, context_schema=None, input_schema=None, output_schema=None)` | 共享状态图构建器（各节点读写同一状态）                       |
| `StateGraph.add_node`              | `(node, action=None, *, defer=False, metadata=None, input_schema=None, retry_policy=None, cache_policy=None, destinations=None)` | 添加节点（node 可为函数或名称字符串）                        |
| `StateGraph.add_edge`              | `(start_key: str|list[str], end_key: str)`                   | 添加边；多条起点需全部完成后才执行终点                       |
| `StateGraph.add_conditional_edges` | `(source, path, path_map=None)`                              | 条件路由；path 返回节点名或 END，path_map 做映射             |
| `StateGraph.add_sequence`          | `(nodes)`                                                    | 按顺序批量添加一串节点                                       |
| `StateGraph.compile`               | `(checkpointer=None, *, cache=None, store=None, interrupt_before=None, interrupt_after=None, debug=False, name=None)` | 编译为可执行 `CompiledStateGraph`                            |
| `MessageGraph`                     | `()`                                                         | 以消息列表为状态的简化图（老版本入口）                       |
| `START` / `END`                    | 常量                                                         | 图的入口 / 出口哨兵节点                                      |
| `add_messages`                     | `(left, right, *, format=None)`                              | 消息合并 reducer：按 ID 覆盖更新、否则追加（`format='langchain-openai'` 可转 OpenAI 格式） |

**CompiledStateGraph（编译产物，实现了 Runnable 接口）**

| 方法                                       | 输入参数                                                     | 功能                                                         |
| ------------------------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `invoke`                                   | `(input, config=None, *, context=None, stream_mode='values', print_mode=(), output_keys=None, interrupt_before=None, interrupt_after=None, durability=None, **kwargs)` | 单次同步运行                                                 |
| `ainvoke`                                  | 同 invoke                                                    | 单次异步运行                                                 |
| `stream`                                   | `(input, config=None, *, context=None, stream_mode=None, print_mode=(), output_keys=None, interrupt_before=None, interrupt_after=None, durability=None, subgraphs=False, debug=None)` | 流式运行（`stream_mode`：values/updates/custom/messages/checkpoints/tasks） |
| `astream`                                  | 同 stream                                                    | 异步流式运行                                                 |
| `get_state` / `aget_state`                 | `(config, *, subgraphs=False)`                               | 获取当前状态快照 `StateSnapshot`                             |
| `get_state_history` / `aget_state_history` | `(config, *, filter=None, before=None, limit=None)`          | 获取状态历史（回放 / 时间旅行）                              |
| `update_state` / `aupdate_state`           | `(config, values, as_node=None, task_id=None)`               | 以某节点身份注入状态更新（HITL 用）                          |
| `bulk_update_state` / `abulk_update_state` | `(config, supersteps)`                                       | 批量状态更新（需 checkpointer）                              |
| `get_graph` / `aget_graph`                 | `(config=None, *, xray=False)`                               | 返回可绘制的图结构（可视化用）                               |
| `get_subgraphs` / `aget_subgraphs`         | `(*, namespace=None, recurse=False)`                         | 枚举子图                                                     |
| `with_config`                              | `(config=None, **kwargs)`                                    | 复制对象并更新配置                                           |

## 二、langgraph.prebuilt — 预置组件

| 函数 / 类            | 输入参数                                                     | 功能                                                         |
| -------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `create_react_agent` | `(model, tools, *, state_schema=None, prompt=None, response_format=None, checkpointer=None, store=None, interrupt_before=None, interrupt_after=None, debug=False)` | 创建循环调用工具直至停止条件的 ReAct 智能体图。⚠️ **v1 已弃用**，官方建议迁移至 `langchain.agents.create_agent`（签名基本一致，增加中间件） |
| `ToolNode`           | `(tools, name='tools', tags=None, handle_tool_errors=True, messages_key='messages')` | 执行最后一条 AIMessage 中全部 tool_calls 的节点，并行运行并返回 ToolMessage |
| `ValidationNode`     | `(schemas, format_error=None, name='validation', tags=None)` | 用 Pydantic schema 校验工具调用（不执行工具，用于结构化输出） |
| `tools_condition`    | `(state, messages_key='messages')` → `'tools'|'__end__'`     | 条件路由：最后消息含 tool_calls 则去 "tools"，否则结束       |
| `InjectedState`      | `(field=None)`                                               | 工具参数注解：自动注入图状态（对模型隐藏该参数）             |
| `InjectedStore`      | `()`                                                         | 工具参数注解：自动注入 store（需 langchain-core ≥0.3.8）     |
| `msg_content_output` | 工具输出 → `ToolMessage` 内容                                | 工具输出格式化辅助函数                                       |

## 三、langgraph.types — 类型与原语

| 函数 / 类                             | 输入参数                                                     | 功能                                                         |
| ------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `interrupt`                           | `(value)` → Any                                              | 节点内中断图执行（human-in-the-loop），必须启用 checkpointer；恢复时返回 resume 值 |
| `Command`                             | dataclass `(graph=None, update=None, resume=None, goto=())`  | 一次调用内更新状态、跳转节点、恢复中断；`graph=Command.PARENT` 可控制父图 |
| `Send`                                | `(node, arg)`                                                | 条件边中动态并行分发：以自定义状态调用目标节点（map-reduce） |
| `RetryPolicy`                         | `(initial_interval=0.5, backoff_factor=2.0, max_interval=128.0, max_attempts=3, jitter=True, retry_on=...)` | 节点失败重试策略                                             |
| `CachePolicy`                         | NamedTuple                                                   | 节点结果缓存策略                                             |
| `StateSnapshot`                       | `(values, next, config, metadata, created_at, parent_config, tasks)` | 某步开始时的状态快照                                         |
| `PregelTask` / `PregelExecutableTask` | NamedTuple                                                   | 任务结构（含 name/input/config/retry_policy 等）             |
| `StreamMode`                          | `Literal['values','updates','debug','messages','custom']`    | 流式输出模式                                                 |
| `StreamWriter` / `All`                | 类型别名                                                     | 自定义流写入回调 / 全节点中断标记 `'*'`                      |

## 四、langgraph.func — 函数式 API（实验性）

| 函数         | 输入参数                                                 | 功能                                                         |
| ------------ | -------------------------------------------------------- | ------------------------------------------------------------ |
| `entrypoint` | `(*, checkpointer=None, store=None, config_schema=None)` | 装饰器：把普通函数转成 Pregel 工作流；函数可接收 `writer`、`config`、`previous`（checkpointer 下的上次返回值） |
| `task`       | `(func_or_none=None, *, retry=None)`                     | 装饰器：把函数定义为任务，调用返回 Future（`.result()` 取值），天然可并行；需在 entrypoint/StateGraph 内调用 |

## 五、langgraph.checkpoint — 检查点持久化

| 函数 / 类                                    | 输入参数                                                     | 功能                                                         |
| -------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `MemorySaver`（即 `InMemorySaver`）          | `()`                                                         | 内存检查点，仅调试/测试用                                    |
| `SqliteSaver` / `AsyncSqliteSaver`           | `(conn)`                                                     | SQLite 检查点（同步/异步）                                   |
| `PostgresSaver` / `AsyncPostgresSaver`       | `(conn, serde=None)`，另有 `from_conn_string(...)` + `setup()` | PostgreSQL 检查点，生产环境全量历史                          |
| `BaseCheckpointSaver`                        | 抽象基类                                                     | 需实现 `get_tuple / list / put / put_writes / delete_thread`（及 async 变体 `aget_tuple / alist / aput / aput_writes / adelete_thread`） |
| `JsonPlusSerializer` / `EncryptedSerializer` | 序列化协议                                                   | 默认序列化器（ormsgpack+JSON 回退）/ AES 加密序列化器（`from_pycryptodome_aes(key)`） |
| `create_checkpoint`                          | `(previous_checkpoint, channels, ...)`                       | 由旧检查点 + 实时通道状态构建新 Checkpoint                   |

## 六、langgraph.store — 长期记忆存储

| 函数 / 类             | 输入参数                                                     | 功能                                                         |
| --------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `BaseStore`           | 抽象基类                                                     | `get / search / put / delete / list_namespaces / batch` + 全部 async 变体 |
| `InMemoryStore`       | `(index=None, ttl=None)`                                     | 内存 KV 存储；配 `index={"dims":…, "embed":…, "fields":…}` 可启用语义检索 |
| `Item` / `SearchItem` | `(value, key, namespace, created_at, updated_at[, score])`   | 存储条目 / 搜索结果条目                                      |
| `GetOp`               | `(namespace, key, refresh_ttl=True)`                         | 批量取操作                                                   |
| `SearchOp`            | `(namespace_prefix, filter=None, limit=10, offset=0, query=None, refresh_ttl=True)` | 搜索操作（filter 支持 `$eq/$ne/$gt/$gte/$lt/$lte`）          |
| `PutOp`               | `(namespace, key, value, index=None, ttl=None)`              | 写入 / 更新 / 删除操作                                       |
| `ListNamespacesOp`    | `(match_conditions=None, max_depth=None, limit=100, offset=0)` | 列举命名空间                                                 |
| `MatchCondition`      | `(match_type, path)`                                         | 命名空间匹配规则（prefix/suffix，支持 `*` 通配）             |
| `IndexConfig`         | `(dims, embed, fields=None)`                                 | 语义检索索引配置                                             |
| `TTLConfig`           | `(refresh_on_read=True, default_ttl=None, sweep_interval_minutes=None)` | 条目过期配置                                                 |

## 七、langgraph.channels / managed / runtime / errors

| 模块                 | 成员                                                         | 输入参数                                                     | 功能                                                         |
| -------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `langgraph.channels` | `LastValue`                                                  | `()`                                                         | 只存最近一个值（默认通道）                                   |
|                      | `EphemeralValue`                                             | `()`                                                         | 存上一步的值，随后清除                                       |
|                      | `AnyValue`                                                   | `()`                                                         | 存最近值，允许一步多次写入                                   |
|                      | `BinaryOperatorAggregate`                                    | `(operator)`                                                 | 用二元算子聚合每次新值                                       |
|                      | `Topic`                                                      | `(typ, key=None, deduplicate_policy=None, accumulate=False)` | 可配置的 PubSub 主题通道                                     |
|                      | `BaseChannel`                                                | 抽象基类                                                     | 自定义通道需实现 update/consume 等                           |
| `langgraph.managed`  | `IsLastStep`                                                 | 状态注解                                                     | 托管值：当前是否为最后一步                                   |
|                      | `RemainingSteps`                                             | 状态注解                                                     | 托管值：剩余可执行步数                                       |
|                      | `InjectedValue`                                              | 状态注解                                                     | 向节点注入值                                                 |
| `langgraph.runtime`  | `Runtime`                                                    | 运行时上下文                                                 | 0.6+ 新增；向节点注入 `context`（只读）、`store`、`writer`、`step`、`checkpointer` 等 |
| `langgraph.errors`   | `GraphRecursionError`、`GraphInterrupt`、`GraphValueError`、`InvalidUpdateError`、`EmptyChannelError`、`NullChannelError`、`InvalidNodeError`、`MissingEdgeError`、`InvalidCheckpointError` 等 | —                                                            | 各类运行时异常类                                             |

## 快速上手（导入路径）

```
from langgraph.graph import StateGraph, START, END, add_messages        # 建图
from langgraph.prebuilt import ToolNode, tools_condition                # 预置组件
from langgraph.checkpoint.memory import MemorySaver                     # 检查点
from langgraph.store.memory import InMemoryStore                        # 长期记忆
from langgraph.types import interrupt, Command, Send, RetryPolicy       # 原语
from langgraph.func import entrypoint, task                             # 函数式 API
from langgraph.managed import IsLastStep, RemainingSteps                # 托管值
```

**几点版本提醒**：① 以上签名对应当前稳定版 v1.x（最新 1.2.11，Python ≥3.10）；② v1 已弃用 `create_react_agent`，新项目建议直接用 `langchain.agents.create_agent`（运行在 LangGraph 之上）；③ 0.6.0 起新增 `context_schema` / `Runtime` 只读上下文机制，替代旧的 `config_schema`。