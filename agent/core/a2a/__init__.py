"""A2A 协议与进程内通信客户端（对接开源 a2a-sdk，不自造协议轮子）。"""

from agent.core.a2a import protocol
from agent.core.a2a.client import A2AClient, A2AClientError, InProcessA2AClient

__all__ = [
    "A2AClient",
    "A2AClientError",
    "InProcessA2AClient",
    "protocol",
]
