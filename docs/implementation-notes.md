# 实现笔记与踩坑

这里记录开发中踩过的「细碎但致命」的技术问题与设计取舍。每一条都给**使用场景**——什么时候你会撞上它，方便对号入座。

## 一、事件流与迭代器

### call_stream 是「逐轮事件流」，不是 LLM streaming

**场景**：刚接手项目时把 `call_stream` 当成 LLM 的流式输出接口，直接用返回值；或写消费端时以为每轮迭代对应一个 token。

- `call_stream()` 名字里的 "stream" 指的是**逐轮迭代产出事件流**，不是 LLM 层面的 `stream=True`。它 yield 的是 `StreamMessage` 事件（`ANSWER_DELTA` / `START_TOOL` / `TOOL_RESULT` / `APPROVAL_REQUIRED` / `ANSWER_END`…），每轮迭代 = 一次「LLM 调用 + 可能的工具执行」，而不是一个 token。
- 消费端在 `agent/main.py` 的 `_consume_stream()`：`for event in agent.call_stream(...)` 按 `event.event` 分派渲染，返回 `(final, pause)`。
- 引擎只产事件、不碰 IO；消费端只处理事件、不关心事件怎么产生。两者通过 `StreamMessage` 解耦。

### 主循环是双层生成器嵌套（加上消费端共三层），一层驱动一层

**场景**：看到 `for event in agent.call_stream(...)` 里面又套着一个 `while True: next(deltas)`，不知道谁驱动谁；或想改造其中一层时无从下手。

```
[层①] for event in agent.call_stream(...)   # 消费端循环（_consume_stream）：不是生成器，驱动外层
[层②]     while True:                        # call_stream 生成器本体
[层③]         delta = next(deltas)           # completion_stream 生成器（deltas）：yield 字符串增量
```

- 主循环是**两层生成器叠在一起**：外层 `call_stream` 生成器（元素 `StreamMessage` 事件）内部驱动内层 `deltas`（`completion_stream` 生成器，元素是文本增量）。严格说**生成器对象只有 2 个**；"第三层"指**消费端的 `for` 循环**（它不是迭代器，是驱动外层生成器的消费者）——数迭代器对象 2 个，数嵌套环节 3 层。
- 二者用**同一个迭代器协议**（`__next__` + `StopIteration`），区别只在**层级、元素类型、终止方式**：
  - 外层由消费者 `_consume_stream` 的 `for` 驱动，靠自己的 `return` 结束；
  - 内层由外层代码里的 `next(deltas)` 驱动，靠 `StopIteration.value` 携带最终 `ModelResponse` 结束。
- 一条完整链路 = 消费者 `for` 驱动外层 → 外层 `yield` 暂停交接 → 消费者处理事件 → 回到外层 → 外层 `next(deltas)` 驱动内层 → 内层 `yield` 增量 → 外层转发成 `ANSWER_DELTA` → …直到内层 `StopIteration`、外层拿到响应。
- 后果：**不能"一趟 for 拿到所有东西"**——想拿到最终响应，必须在外层循环内部去 `next` 内层并捕获 `StopIteration`（详见下一条）。改造时先分清你在动哪一层。

### 生成器的 return 值藏在 StopIteration.value 里

**场景**：改/写 LLM provider 的流式接口，或想在引擎内部拿到最终的 `ModelResponse`。

```python
deltas = self.llm.completion_stream(messages, tools, tool_choice)  # 返回的是生成器，不是响应
while True:
    try:
        delta = next(deltas)
    except StopIteration as stop:
        response = stop.value    # ← ModelResponse 只能从这里拿
        break
```

- `completion_stream` 一边 `yield` 文本增量，一边在结束时把 `ModelResponse` 作为生成器的 **return 值**塞进 `StopIteration.value`。
- 所以这段**不能**写成 `for delta in deltas:`——`for` 循环会丢弃 `StopIteration.value`。
- 教训：Python 生成器「既有 yield 又有 return 值」时，想拿 return 值必须手动 `next()` + 捕获 `StopIteration`。

### yield 是暂停交接，return 才是真正结束

**场景**：调试「生成器还没产出终止事件就没了」，或写一个内部有 `yield` 又提前 `return` 的消费链。

- `yield` 把事件交给消费者后**暂停**，消费者 `next()` 回来继续执行；`return`（或抛异常）才是生成器真正结束。
- 主循环里所有「暂停」路径都是 `yield XXX` 后立刻 `return`，所以 `_consume_stream` 的 `for` 循环会随之一并退出——暂停**不是异常**，是靠生成器提前结束来表达的。

## 二、暂停与恢复

### 暂停 = 提前 return（不抛异常）；恢复 = 重新调用 call_stream

**场景**：集成审批/前端暂停；前端「暂停后恢复」时发现对话错乱或编号从 0 重来。

- 遇到 `APPROVAL_REQUIRED` / `FRONTEND_PAUSE`，`call_stream` 提前 `return`，消费者拿到 `pause` 信息（kind、tool_call_id、messages…）停止消费。
- 恢复 = **重新调用** `call_stream`，带上 `tool_decisions`（tool_call_id → bool）或 `frontend_tool_results`，并传 `tool_number_offset` / `iteration_offset` 接着上次的计数。
- 恢复后第一轮迭代进入 `if tool_decisions:` 分支，由 `_execute_tool_decisions` 把决策落地。

### max_steps 不跨恢复累计（已知坑）

**场景**：交互式 CLI 下模型持续请求需审批的工具、用户持续批准，担心步数失控（`CODE_REVIEW.md` 已记录）。

- `_run_turn` 每轮审批暂停→恢复都**重新调用** `call_stream`，而 `call_stream` 每次从 `iteration_offset=0` 开始计数——`max_steps` 只约束单次调用内的迭代次数，**不累计**跨恢复的 LLM 调用。
- 交互式 CLI 下用户可 Ctrl+C 兜底；接入 server 模式时需要跨恢复传递步数累计。

### tool_decisions 处理完必须清空，否则工具被重复执行

**场景**：恢复后同一批已批准的工具被**执行两遍**（副作用命令跑两次）、产生重复 tool 消息被 provider 拒绝。

```python
approved_count = self._execute_tool_decisions(...)
tool_number += approved_count
tool_decisions = {}   # ← 防重复执行的关键
```

- 主循环是 `while`，不清空则下一轮迭代 `if tool_decisions:` 仍为真，同一批决策被再次重放。
- `_execute_tool_decisions` 内部还用 `_answered_tool_call_ids` 过滤已应答调用，双保险。
- `frontend_tool_results` 同理，处理完立即清空。

### frontend_pause 只保留第一个

**场景**：同一批多个工具同时触发前端暂停（边界情况）。

- 收集循环里 `if frontend_pause is None:` 只保留第一个 `FRONTEND_PAUSE`；第二个及之后会走 `continue` 被**静默丢弃**（不落历史、不保留），其 `tool_call_id` 将沦为孤儿，靠 `_resolve_orphaned_tool_calls` 按已取消兜底。
- 设计假设是「同一批只有一个前端暂停」，需要多暂停时按 `approval_pauses` 的列表模式扩展。

## 三、供应商协议约束

### 每个 tool_call_id 必须有 role="tool" 响应——服务端结构校验，不是模型问题

**场景**：LLM 调用直接报 `tool_call_ids did not have response messages` / `An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'`。

- 这是 OpenAI/DeepSeek/Anthropic **API 服务端的结构校验**（推理时 400 错误），发生在消息发给模型之前，**与具体模型无关**——换任何模型、消息结构不合法照样报错。
- 它**不是模型训练问题**；训练只影响「跳过工具结果时模型输出质量下降」这个层面（模型被训练成 call→result 成对出现，条件分布预期结果在下一段输入）。
- 因此对话历史上「assistant 消息带 tool_calls → 必须有对应 tool 消息」是硬不变量，所有代码路径都在维护它。

### 孤儿调用兜底：_resolve_orphaned_tool_calls 倒序插入

**场景**：一批调用因审批暂停、兄弟调用未应答；或某个待处理的审批被整体放弃；或前端暂停被丢弃。不处理则下一次 LLM 调用直接失败。

- 每次 LLM 调用前（主循环第 203 行）扫描整个历史，为所有「无响应的 tool_call_id」注入取消型拒绝结果。
- **从后往前遍历**：插入新消息会改列表长度，倒着走索引才不受影响。
- `insert_offset` 保证同一 assistant 消息里的多个孤儿调用，其结果按顺序插在该消息后面、保持配对顺序。
- 注入后 `answered_ids.add()` 防止同 id 在更早消息里被二次注入（同 id 两条 tool 消息同样违规）。

### 审批决策落地：_execute_tool_decisions 批准重放 / 拒绝给回应

**场景**：恢复暂停批次，想知道已批准的工具怎么补执行、已拒绝的怎么收尾。

- 已批准：以 `user_approved=True` 重放执行，成功才计入 `approved_count`（`tool_number += approved_count` 推进编号）。
- 已拒绝：追加一条拒绝型 tool 消息（`error="User denied approval..."`），让该 `tool_call_id` 得到回应。
- 已应答的调用**绝不重放**（`_answered_tool_call_ids` 过滤），避免重复 `tool_call_id` 响应。

### 所有「未执行却要回应」的路径统一走 _denial_result

**场景**：新增一条需要回应 tool_call_id 但实际不执行工具的代码路径（拒绝、取消、孤儿…）。

- `_denial_result` 工厂统一产出 `ERROR` 状态、形态完全一致的 `ToolCallResult`，经 `to_llm_message()` 成为对供应商安全的 tool 消息。
- 不要自己手拼 tool 消息：形态不一致迟早触发供应商校验或前端解析错乱。

### assistant_msg 条件赋值，避免畸形消息

**场景**：模型返回空 content、或同时带 content + tool_calls 导致 API 拒绝。

```python
assistant_msg = {"role": "assistant"}
if response.content:    assistant_msg["content"] = response.content
if response.tool_calls: assistant_msg["tool_calls"] = response.tool_calls
```

- 正常回答只有 `content`；工具请求只有 `tool_calls`。按实际内容只填存在的键，避免 `content=""` 之类的空值。
- 无脑全量 `model_dump()` 会带出空值触发服务端格式校验。

### is_last_step 强制收尾 + 模型无视 tool_choice="none" 的兜底

**场景**：`max_steps` 耗尽，担心模型永远在调工具不回答。

- `is_last_step = i >= self.max_steps` 时传 `tools=None` + `tool_choice="none"` 强制收尾。
- 但**有些模型无视 `tool_choice="none"`**，最后一步仍可能带 `tool_calls`——所以兜底逻辑不假设最后一条 assistant 消息干净。

### max_steps 耗尽时绝不静默返回

**场景**：循环耗尽却没有无工具的答案，调用方会永远等不到终止事件。

- 从历史**从后往前**捞最后一条 assistant 消息：有 content → 发 `ANSWER_END`（`max_steps_reached=True`）；没有 → 发 `ERROR`（提示增大步数上限或简化任务）。
- 终止事件二选一必须给一个，这是对调用方的契约。

## 四、并发执行

### Future ≠ 线程；futures[future]=tc 是冗余映射

**场景**：调试并行工具执行，以为 `futures` 字典装的是线程。

- `executor.submit()` 返回 **Future（结果占位符）**，不是线程；线程在 `ThreadPoolExecutor` 内部的工作线程池里（最多 `max_workers=16`），代码里摸不到。
- `futures[future] = tc` 是 **Future → 原始 tool_call** 映射：key 供 `as_completed(futures)` 迭代，value 目前**没被消费**（收集循环只用 `future.result()`），属留档/调试用。
- 想按 future 反查是哪个工具调用时用这个映射。

### 先收整批再暂停（drain all futures）

**场景**：一批工具调用部分需审批、部分正常。若收到第一个审批就立即暂停，兄弟调用的结果还没落历史，其 `tool_call_id` 沦为孤儿，下次 LLM 调用报 "tool_call_ids did not have response messages"。

- `as_completed` 收完**所有** future 后才发暂停事件——普通结果先落历史 + `yield TOOL_RESULT`，审批/前端暂停只「记账」。
- `with ThreadPoolExecutor` 块退出时 `shutdown(wait=True)` 也会等全部线程结束，双保险。
- 暂停时历史必须是合法的，恢复时只需补暂停那几位的决策。

### as_completed 乱序收结果，TOOL_RESULT 随完成顺序流出

**场景**：期望 `TOOL_RESULT` 事件按提交顺序到达。

- `as_completed` 谁先完成先返回谁，`future.result()` 阻塞直到该线程跑完——**不要**用 `for future in futures` 按提交顺序收，那会让前面慢的工具卡住后面快的结果的展示。
- 暂停事件的判定放在整批收完之后，所以乱序只影响 `TOOL_RESULT` 展示顺序，不影响正确性。

## 五、日志系统

### Windows 中文环境下文件 handler 必须显式 encoding="utf-8"

**场景**：给 agent 加文件日志后，中文/emoji 消息在日志文件里乱码或抛 `UnicodeEncodeError`。

- `logging.FileHandler`（含 `RotatingFileHandler`）默认 `encoding=None`，走系统 locale；Windows 中文系统是 cp936（GBK），UTF-8 消息必然出问题。
- 解决办法：构造 handler 时显式 `encoding="utf-8"`。所有 str 都能编码为 UTF-8，因此只设 `encoding` 即可，无需再配 `errors=`。
- 见 `agent/utils/log.py` 的 `RotatingFileHandler` 装配。
