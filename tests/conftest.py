"""pytest 共享配置（宪法 IV：tests/ 镜像 GSagent/）。

环境修复（001-langgraph-otel-refactor 实现期发现）：litellm 的
``default_encoding`` 默认把 tiktoken tokenizer 缓存强制写到包内只读目录
（base_llm site-packages），首次调用触发 ``PermissionError``，导致整个
``GSagent`` 包无法 import。litellm 官方提供 ``CUSTOM_TIKTOKEN_CACHE_DIR``
覆盖开关，这里统一重定向到系统临时目录下的可写路径。
"""

import os
import tempfile

_CACHE = os.path.join(tempfile.gettempdir(), "gsagent_tiktoken_cache")
os.makedirs(_CACHE, exist_ok=True)
os.environ.setdefault("CUSTOM_TIKTOKEN_CACHE_DIR", _CACHE)
os.environ.setdefault("TIKTOKEN_CACHE_DIR", _CACHE)
