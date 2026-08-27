"""
llm_factory.py — 统一的 LLM 工厂（仅 OpenAI 兼容格式）

核心能力：
  1. 创建 OpenAI 兼容客户端（base_url + api_key + model）
  2. api_key 支持逗号分隔多 key → 自动包装为 RotatingKeyChatOpenAI（报错自动轮换 + 指数退避）
  3. 模型接入配置由 debug_server 的"设置"页管理（.model_config），本模块不读取
"""
import os
import time
from typing import List, Any
from langchain_openai import ChatOpenAI
from openai import RateLimitError, APIConnectionError, APITimeoutError, AuthenticationError

# 兼容 openai<1.0 的旧异常名
try:
    from openai import OpenAIConnectionError  # noqa: F401
except ImportError:
    OpenAIConnectionError = APIConnectionError  # type: ignore[assignment,misc]

# 所有需要触发 key 轮换的异常
_ROTATABLE = (RateLimitError, APIConnectionError, APITimeoutError, AuthenticationError, OpenAIConnectionError)


def _split_keys(api_key: str) -> List[str]:
    """把逗号分隔的多个 key 切成列表。"""
    if not api_key:
        return []
    return [k.strip() for k in api_key.split(',') if k.strip()]


class RotatingKeyChatOpenAI:
    """多 API Key 轮询 + 指数退避包装：任何报错（限流/连接/超时/鉴权）都换下一个 key 重试，
    全部 key 试完一轮后退避（2^n 秒，上限 30s）再从头开始，直到 max_rounds 轮或成功。"""

    def __init__(self, api_keys: List[str], max_rounds: int = 5, **kwargs):
        self.api_keys = api_keys
        self.current_index = 0
        self.attempt_count = 0
        self.max_rounds = max_rounds
        self.kwargs = kwargs
        self._tools = None

    def bind_tools(self, tools):
        new_instance = RotatingKeyChatOpenAI(
            api_keys=self.api_keys, max_rounds=self.max_rounds, **self.kwargs
        )
        new_instance._tools = tools
        return new_instance

    def _create_client(self):
        client = ChatOpenAI(api_key=self.api_keys[self.current_index], **self.kwargs)
        if self._tools:
            client = client.bind_tools(self._tools)
        return client

    def _reset_from_start(self):
        self.current_index = 0
        self.attempt_count += 1
        wait_time = min(2 ** self.attempt_count, 30)
        print(f"[API Key] 所有 {len(self.api_keys)} 个 Key 均已尝试，等待 {wait_time}s 后重试 (第 {self.attempt_count}/{self.max_rounds} 轮)...")
        time.sleep(wait_time)

    def invoke(self, messages, **kwargs):
        last_exception = None
        while self.attempt_count < self.max_rounds:
            for i in range(len(self.api_keys)):
                self.current_index = i
                try:
                    client = self._create_client()
                    return client.invoke(messages, **kwargs)
                except _ROTATABLE as e:
                    last_exception = e
                    key_short = self.api_keys[i][:10] + "..." + self.api_keys[i][-4:]
                    print(f"[API Key] Key {i + 1}/{len(self.api_keys)} ({key_short}) 失败: {type(e).__name__}，换下一个")
            # 一轮全部失败 → 退避重试
            self._reset_from_start()
        raise last_exception or RateLimitError("所有 API Key 均已失败")

    def stream(self, messages, **kwargs):
        return self._create_client().stream(messages, **kwargs)

    def astream(self, messages, **kwargs):
        return self._create_client().astream(messages, **kwargs)

    def __getattr__(self, name):
        raise AttributeError(f"'RotatingKeyChatOpenAI' object has no attribute '{name}'")


def _build_openai_client(**kwargs):
    """统一构造 OpenAI 兼容客户端；api_key 含逗号时自动启用多 key 轮换。
    参数：base_url / api_key / model / temperature / timeout / max_retries ..."""
    base_url = kwargs.pop("base_url")
    api_key = kwargs.pop("api_key")
    api_keys = _split_keys(api_key)
    if not api_keys:
        raise ValueError("缺少 api_key")
    common = {
        "model": kwargs.pop("model"),
        "temperature": kwargs.get("temperature", 0),
        "max_tokens": kwargs.get("max_tokens", None),
        "timeout": kwargs.get("timeout", 30),
        "max_retries": kwargs.get("max_retries", 0),  # 重试交给 RotatingKeyChatOpenAI 统一负责
        "base_url": base_url,
    }
    if len(api_keys) == 1:
        return ChatOpenAI(api_key=api_keys[0], **common)
    return RotatingKeyChatOpenAI(api_keys=api_keys, max_rounds=5, **common)


def create_llm(provider: str = "openai_compatible", **kwargs):
    """创建 LLM 客户端（纯工厂，仅 OpenAI 兼容格式）。

    参数：
      - model      必填，模型名
      - base_url   可选，默认读 OPENAI_BASE_URL 环境变量
      - api_key    可选，默认读 OPENAI_API_KEY 环境变量（逗号分隔多 key 自动轮换）
      - temperature / timeout / max_tokens / max_retries 等透传

    模型配置（厂商目录、凭据、默认模型）由外部配置层（debug_server 设置系统）负责，
    本模块不读取 .model_config，不内置任何厂商凭据。
    """
    # ===== OpenAI 兼容格式 =====
    base_url = kwargs.get("base_url") or os.getenv("OPENAI_BASE_URL")
    api_key = kwargs.get("api_key") or os.getenv("OPENAI_API_KEY")
    model = kwargs.get("model")
    if not model:
        raise ValueError("缺少 model")
    if not base_url:
        raise ValueError("缺少 base_url")
    if not api_key:
        raise ValueError("缺少 api_key")
    _kw = dict(kwargs)
    _kw.update({"base_url": base_url, "api_key": api_key, "model": model})
    return _build_openai_client(**_kw)
