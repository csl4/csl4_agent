"""Runtime API（002-enterprise-cli-upgrade US4 T033，FR-011，R-09）。

FastAPI 应用：线程 / 回合 / SSE + 持久化后台任务 worker。
serve 与 CLI 一样只是 `StreamMessage` 事件流消费者（宪法 II），
不接触 Tool/Toolset 内部细节。

端点（contracts/runtime.md）：
  POST /threads                创建线程 {scope?} → {thread_id}
  POST /threads/{id}/messages  发送回合 {content, hitl_mode?}
  GET  /threads/{id}/events    SSE 事件流（转发 StreamMessage.to_sse()）
  POST /tasks                  投递后台任务 {scope, payload} → {task_id}
  GET  /tasks                  查询任务 {scope?, state?}
  GET  /tasks/{id}             任务详情（状态/租约/scope）
  POST /tasks/{id}/cancel      取消（canceled 优先，迟到结果不覆盖，FR-010）

设计要点：
- 线程状态保存在内存（回合历史 + 事件缓冲），任务持久化于 DurableTaskManager
  （stdlib sqlite3，R-08）——SC-006 持久化/崩溃恢复针对的是任务队列而非线程。
- 回合在后台线程执行 agent.call_stream（同步生成器），事件经线程安全缓冲
  推给 /events 的 SSE 生成器（轮询消费，跨 TestClient/uvicorn 一致）。
- 后台 worker 线程按已知 scope 轮询 claim 任务，执行 payload.prompt 回合；
  complete/fail 前检查 is_canceled（FR-010 迟到结果不覆盖）；
  worker 崩溃后租约过期由 requeue_expired 重排（SC-006）。
- serve 默认 hitl_mode=never：服务无人类审批界面 → 危险操作拒绝
  （approver=none，FR-004）；--hitl auto 需显式指定。
"""

import logging
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Deque, Dict, List, Optional

import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from GSagent.config import Config
from GSagent.core.agents.graph_agent import GraphAgent, PauseRequest
from GSagent.core.prompts import build_chat_messages
from GSagent.core.runtime.tasks import DurableTaskManager, queue_db_path
from GSagent.utils.stream import StreamEvents

logger = logging.getLogger(__name__)

DEFAULT_SCOPE = "(default)"
DEFAULT_LEASE_SECS = 60


class ThreadCreate(BaseModel):
    scope: Optional[str] = None


class MessageCreate(BaseModel):
    content: str
    hitl_mode: Optional[str] = None


class TaskCreate(BaseModel):
    scope: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)


class _EventBuffer:
    """线程安全的事件缓冲（回合线程写，SSE 生成器读）。"""

    def __init__(self) -> None:
        self._items: Deque[str] = deque()
        self._eof = False
        self._cond = threading.Condition()

    def push(self, item: str) -> None:
        with self._cond:
            self._items.append(item)
            self._cond.notify_all()

    def set_eof(self) -> None:
        with self._cond:
            self._eof = True
            self._cond.notify_all()

    def pop(self) -> Optional[str]:
        with self._cond:
            return self._items.popleft() if self._items else None

    def done(self) -> bool:
        with self._cond:
            return self._eof and not self._items


def create_app(
    config: Optional[Config] = None,
    *,
    agent: Optional[GraphAgent] = None,
    task_manager: Optional[DurableTaskManager] = None,
    hitl_mode: str = "never",
    worker_interval: float = 0.1,
    start_worker: bool = True,
    worker_id: str = "worker-1",
) -> FastAPI:
    """构建 Runtime API 应用。

    config：配置（组装默认 agent 与任务队列路径）；agent/task_manager 均注入时可为 None。
    agent：可注入的 GraphAgent（测试用 FakeChatLLM 打桩）；None 时按 config 装配。
    task_manager：可注入；None 时按 runtime.queue_db 新建。
    hitl_mode：serve 审批模式，默认 never（无审批界面 → 危险操作拒绝，FR-004）。
    """
    if agent is None:
        config = config or Config()
        # serve 默认 never：服务无人类审批界面（FR-004）。
        config.data.setdefault("policy", {})["hitl_mode"] = hitl_mode
        agent = config.create_single_graph_agent(
            checkpointer=config.create_saver(), store=config.create_store()
        )

    if task_manager is None:
        config = config or Config()
        task_mgr = DurableTaskManager(path=queue_db_path(config))
    else:
        task_mgr = task_manager

    stop_event = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop_event.clear()
        worker = None
        if start_worker:
            worker = threading.Thread(
                target=_worker_loop, args=(app,), daemon=True, name="task-worker"
            )
            worker.start()
        yield
        stop_event.set()
        if worker is not None:
            worker.join(timeout=2.0)

    app = FastAPI(title="agent Runtime API", version="0.1.0", lifespan=lifespan)

    # 应用状态（线程 + 事件缓冲 + 任务队列 + worker）
    app.state.threads: Dict[str, List[Dict[str, Any]]] = {}
    app.state.event_buffers: Dict[str, _EventBuffer] = {}
    app.state.task_manager = task_mgr
    app.state.agent = agent
    app.state.toolsets = agent.tools_registry.get_all_tools()
    app.state.known_scopes: set = {DEFAULT_SCOPE}
    app.state.scopes_lock = threading.Lock()
    app.state.stop_event = stop_event
    app.state.worker_interval = worker_interval
    app.state.worker_id = worker_id

    # ---------------- 线程 / 回合 / SSE ----------------

    @app.post("/threads")
    async def create_thread(body: Optional[ThreadCreate] = None) -> Dict[str, str]:
        # body 可选（contracts/runtime.md：body {scope?}——scope 本就可省）
        thread_id = f"thread-{uuid.uuid4().hex[:8]}"
        app.state.threads[thread_id] = []
        app.state.event_buffers[thread_id] = _EventBuffer()
        return {"thread_id": thread_id}

    @app.post("/threads/{thread_id}/messages")
    async def send_message(thread_id: str, body: MessageCreate) -> Dict[str, str]:
        if thread_id not in app.state.threads:
            raise HTTPException(status_code=404, detail=f"线程不存在: {thread_id}")
        history = app.state.threads[thread_id]
        history.append({"role": "user", "content": body.content})
        buffer = app.state.event_buffers[thread_id]

        messages = build_chat_messages(
            ask=body.content,
            session_history=history[:-1],
            toolsets=app.state.toolsets,
        )

        def run_turn() -> None:
            final_msgs = list(messages)
            resume: Optional[Dict[str, Dict[str, Any]]] = None
            try:
                while True:
                    saw_pause = False
                    for item in app.state.agent.stream(
                        messages=final_msgs,
                        session_id=thread_id,
                        resume=resume,
                    ):
                        if isinstance(item, PauseRequest):
                            # serve 无人类审批界面 → 自动拒绝该审批（FR-004）
                            resume = {item.id: {"approved": False}}
                            saw_pause = True
                            continue
                        buffer.push(item.to_sse())
                        if item.event == StreamEvents.ANSWER_END:
                            final_msgs = item.data.get("messages", final_msgs)
                    if not saw_pause:
                        break
            except Exception as exc:  # noqa: BLE001 - 回合失败也关闭事件流
                logger.exception("线程回合失败: %s", exc)
                buffer.push(
                    f"event: {StreamEvents.ERROR.value}\n"
                    f'data: {{"error": "回合失败: {exc}"}}\n\n'
                )
            finally:
                app.state.threads[thread_id] = final_msgs
                buffer.set_eof()

        threading.Thread(target=run_turn, daemon=True).start()
        return {"thread_id": thread_id, "status": "accepted"}

    @app.get("/threads/{thread_id}/events")
    async def thread_events(thread_id: str):
        if thread_id not in app.state.event_buffers:
            raise HTTPException(status_code=404, detail=f"线程不存在: {thread_id}")
        buffer = app.state.event_buffers[thread_id]

        async def gen():
            while True:
                item = buffer.pop()
                if item is not None:
                    yield item
                    continue
                if buffer.done():
                    return
                await asyncio.sleep(0.01)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ---------------- 后台任务（持久化队列） ----------------

    @app.post("/tasks")
    def create_task(body: TaskCreate) -> Dict[str, str]:
        scope = body.scope or DEFAULT_SCOPE
        task_id = app.state.task_manager.create(scope=scope, payload=body.payload)
        with app.state.scopes_lock:
            app.state.known_scopes.add(scope)
        return {"task_id": task_id}

    @app.get("/tasks")
    def list_tasks(
        scope: Optional[str] = None, state: Optional[str] = None
    ) -> Dict[str, Any]:
        return {
            "tasks": app.state.task_manager.list(scope=scope, state=state)
        }

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> Dict[str, Any]:
        rec = app.state.task_manager.get(task_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        return rec

    @app.post("/tasks/{task_id}/cancel")
    def cancel_task(task_id: str) -> Dict[str, str]:
        if not app.state.task_manager.cancel(task_id):
            raise HTTPException(
                status_code=409, detail=f"任务不可取消（可能已终止）: {task_id}"
            )
        return {"task_id": task_id, "state": "canceled"}

    return app


# ---------------- worker 循环 ----------------

def _worker_loop(app: FastAPI) -> None:
    """后台 worker：轮询各 scope 领取任务并执行（崩溃由租约恢复，SC-006）。"""
    state = app.state
    while not state.stop_event.is_set():
        try:
            with state.scopes_lock:
                scopes = list(state.known_scopes)
            for scope in scopes:
                _run_one_task(app, scope)
        except Exception as exc:  # noqa: BLE001 - worker 不因单次异常退出
            logger.exception("任务 worker 异常: %s", exc)
        time.sleep(state.worker_interval)


def _run_one_task(app: FastAPI, scope: str) -> None:
    """领取并执行一个后台任务（payload.prompt 回合）。"""
    state = app.state
    claimed = state.task_manager.claim_next(
        scope, worker_id=state.worker_id, lease_secs=DEFAULT_LEASE_SECS
    )
    if claimed is None:
        return
    tid = claimed["id"]
    payload = claimed.get("payload") or {}
    prompt = str(payload.get("prompt", ""))

    # 领取后先检查取消（FR-010：canceled 优先）
    if state.task_manager.is_canceled(tid):
        return
    try:
        messages = build_chat_messages(
            ask=prompt, session_history=None, toolsets=state.toolsets
        )
        resume: Optional[Dict[str, Dict[str, Any]]] = None
        while True:
            saw_pause = False
            for item in state.agent.stream(
                messages=messages, session_id=tid, resume=resume
            ):
                if isinstance(item, PauseRequest):
                    # 后台任务无审批界面 → 自动拒绝
                    resume = {item.id: {"approved": False}}
                    saw_pause = True
                    continue
                if item.event == StreamEvents.ANSWER_END:
                    break
            if not saw_pause:
                break
        if state.task_manager.is_canceled(tid):
            return  # 迟到结果不覆盖 canceled
        state.task_manager.complete(tid, state.worker_id)
    except Exception as exc:  # noqa: BLE001 - 单任务失败落 failed
        logger.warning("任务 %s 执行失败: %s", tid, exc)
        if not state.task_manager.is_canceled(tid):
            state.task_manager.fail(tid, state.worker_id)


__all__ = ["create_app"]
