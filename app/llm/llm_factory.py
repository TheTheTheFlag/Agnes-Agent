"""
llm_factory.py — 统一的 LLM 工厂（仅 OpenAI 兼容格式）

核心能力：
  1. 创建 OpenAI 兼容客户端（base_url + api_key + model）
  2. api_key 支持逗号分隔多 key → 自动包装为 RotatingKeyChatOpenAI（报错自动轮换 + 指数退避）
  3. 模型接入配置由 debug_server 的"设置"页管理（.model_config），本模块不读取
  4. thinking 模型兼容（deepseek-v4-pro 等）：提取 reasoning_content 到 additional_kwargs，
     并在多轮回传——不传网关会 400。
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

# 所有需要触发 key 轮换/退避的异常（业界共识：401/429/5xx/网络 = 可重试；400 = 不可重试）
# AuthenticationError(401)、RateLimitError(429)、InternalServerError(5xx)、APIConnectionError、APITimeoutError
# BadRequestError(400) 显式排除：参数错/prompt 超长/tool_call 配对错——重试无意义，立刻抛给上层
try:
    from openai import BadRequestError as _BadRequestError
except ImportError:
    _BadRequestError = None  # type: ignore[assignment]

try:
    from openai import InternalServerError as _InternalServerError
except ImportError:
    _InternalServerError = None  # type: ignore[assignment]

_RETRYABLE = (RateLimitError, APIConnectionError, APITimeoutError, AuthenticationError, OpenAIConnectionError)
if _InternalServerError is not None:
    _RETRYABLE = _RETRYABLE + (_InternalServerError,)


# ============================================================
# thinking 模型兼容补丁（langchain-openai 不处理 reasoning_content）
# 适用：deepseek-v4-pro、llm.chatops.fun 上的 thinking 模型等
# 行为：
#   - 响应侧：把 choices[0].message.reasoning_content 写入 AIMessage.additional_kwargs
#   - 请求侧：把 AIMessage.additional_kwargs["reasoning_content"] 透传到 message dict
#   - 流式 chunk：把 delta.reasoning_content 写入 AIMessageChunk.additional_kwargs
# 仅在首次导入时 monkey-patch 一次（避免重复包装）
# ============================================================
def _patch_chatopenai_for_thinking():
    if getattr(ChatOpenAI, "_thinking_patched", False):
        return
    ChatOpenAI._thinking_patched = True

    import langchain_openai.chat_models.base as _chat_base
    _orig_create = _chat_base.ChatOpenAI._create_chat_result
    _orig_convert = _chat_base._convert_message_to_dict
    _orig_chunk = _chat_base.ChatOpenAI._convert_chunk_to_generation_chunk
    _orig_dict_to_msg = _chat_base._convert_dict_to_message

    def _create_chat_result(self, response, generation_info=None):
        rtn = _orig_create(self, response, generation_info)
        try:
            import openai as _openai
            choices = getattr(response, "choices", None) if isinstance(response, _openai.BaseModel) else None
            if choices and hasattr(choices[0].message, "reasoning_content"):
                rc = choices[0].message.reasoning_content
                if rc and rtn.generations:
                    rtn.generations[0].message.additional_kwargs.setdefault("reasoning_content", rc)
        except Exception:
            pass
        return rtn

    def _convert_message_to_dict(message, api="chat/completions"):
        d = _orig_convert(message, api=api)
        # AIMessage 节点：多轮必须回传 reasoning_content（thinking 模型网关强制）
        if getattr(message, "type", None) == "ai":
            rc = (getattr(message, "additional_kwargs", None) or {}).get("reasoning_content")
            if rc:
                d["reasoning_content"] = rc
        return d

    def _convert_dict_to_message(_dict):
        # 响应侧：把 dict 响应里的 reasoning_content 也提取到 additional_kwargs
        msg = _orig_dict_to_msg(_dict)
        rc = _dict.get("reasoning_content") if isinstance(_dict, dict) else None
        if rc and getattr(msg, "type", None) == "ai":
            msg.additional_kwargs.setdefault("reasoning_content", rc)
        return msg

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        gen = _orig_chunk(self, chunk, default_chunk_class, base_generation_info)
        try:
            choices = chunk.get("choices") if isinstance(chunk, dict) else None
            if choices and gen is not None and hasattr(gen, "message"):
                delta = choices[0].get("delta", {}) or {}
                rc = delta.get("reasoning_content")
                if rc is not None and hasattr(gen.message, "additional_kwargs"):
                    gen.message.additional_kwargs.setdefault("reasoning_content", rc)
        except Exception:
            pass
        return gen

    _chat_base.ChatOpenAI._create_chat_result = _create_chat_result
    _chat_base._convert_message_to_dict = _convert_message_to_dict
    _chat_base._convert_dict_to_message = _convert_dict_to_message
    _chat_base.ChatOpenAI._convert_chunk_to_generation_chunk = _convert_chunk_to_generation_chunk


_patch_chatopenai_for_thinking()


def _split_keys(api_key: str) -> List[str]:
    """把逗号分隔的多个 key 切成列表。"""
    if not api_key:
        return []
    return [k.strip() for k in api_key.split(',') if k.strip()]


class RotatingKeyChatOpenAI:
    """多 API Key 轮询 + 指数退避 + jitter 包装（业界做法）。

    触发条件：可重试异常（401/429/5xx/网络/超时）→ 换下一个 key；全部 key 试完一轮后退避（2^n + jitter，上限 30s）
    再从头开始，直到 max_rounds 轮或成功。
    400(BadRequest)立即抛出，不重试（参数错/prompt 超长/tool_call 配对错——重试无意义）。
    """

    def __init__(self, api_keys: List[str], max_rounds: int = 5, base_backoff: float = 1.0, max_backoff: float = 30.0, **kwargs):
        self.api_keys = api_keys
        self.current_index = 0
        self.attempt_count = 0
        self.max_rounds = max_rounds
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.kwargs = kwargs
        self._tools = None

    def bind_tools(self, tools):
        new_instance = RotatingKeyChatOpenAI(
            api_keys=self.api_keys, max_rounds=self.max_rounds,
            base_backoff=self.base_backoff, max_backoff=self.max_backoff,
            **self.kwargs,
        )
        new_instance._tools = tools
        return new_instance

    def _create_client(self):
        client = ChatOpenAI(api_key=self.api_keys[self.current_index], **self.kwargs)
        if self._tools:
            client = client.bind_tools(self._tools)
        return client

    def _backoff_seconds(self) -> float:
        """指数退避 + jitter：2^n + random(0, 1)，上限 max_backoff。
        jitter 防止多 worker 同时重试撞网关（业界标准做法，AWS/Google 重试指南）。"""
        import random as _r
        wait = min((2 ** self.attempt_count) + _r.random(), self.max_backoff)
        return wait

    def _reset_from_start(self):
        self.current_index = 0
        self.attempt_count += 1
        wait_time = self._backoff_seconds()
        print(f"[API Key] 所有 {len(self.api_keys)} 个 Key 均已尝试，等待 {wait_time:.1f}s 后重试 (第 {self.attempt_count}/{self.max_rounds} 轮)...")
        time.sleep(wait_time)

    def _is_non_retryable(self, e: Exception) -> bool:
        """400 BadRequest 立即抛——重试无意义。"""
        if _BadRequestError is not None and isinstance(e, _BadRequestError):
            return True
        return False

    def invoke(self, messages, **kwargs):
        last_exception = None
        while self.attempt_count < self.max_rounds:
            for i in range(len(self.api_keys)):
                self.current_index = i
                try:
                    client = self._create_client()
                    return client.invoke(messages, **kwargs)
                except _RETRYABLE as e:
                    if self._is_non_retryable(e):
                        raise  # 400 立即抛，不重试
                    last_exception = e
                    key_short = self.api_keys[i][:10] + "..." + self.api_keys[i][-4:]
                    print(f"[API Key] Key {i + 1}/{len(self.api_keys)} ({key_short}) 失败: {type(e).__name__}: {str(e)[:120]}，换下一个")
                except Exception as e:
                    # 其他异常（400/编程错误等）立即抛
                    if self._is_non_retryable(e):
                        raise
                    raise
            # 一轮全部失败 → 退避重试
            self._reset_from_start()
        raise last_exception or RateLimitError("所有 API Key 均已失败")

    def _stream_with_retry(self, stream_method_name: str, messages, **kwargs):
        """流式调用：启动失败时换 key 重试一次（不重试 token 中途，避免重复推）。

        业界做法：流式重试只重"连接建立阶段"，因为 token 已 yield 后重试会把同一段
        内容推给前端两次。OpenAI Cookbook / Anthropic best practice 均为此模式。
        """
        last_exception = None
        for i in range(len(self.api_keys)):
            self.current_index = i
            client = self._create_client()
            stream_method = getattr(client, stream_method_name)
            try:
                # 先建立流（__enter__/首次 iter）；失败 → 换 key 重试一次
                stream_iter = stream_method(messages, **kwargs)
                # 包一层迭代器：先触发底层 __iter__，如果到首个 token 前出错则抛
                def _wrapped():
                    try:
                        for chunk in stream_iter:
                            yield chunk
                    except _RETRYABLE as e:
                        if self._is_non_retryable(e):
                            raise
                        last_exception = e
                        key_short = self.api_keys[i][:10] + "..." + self.api_keys[i][-4:]
                        print(f"[API Key] Stream Key {i + 1}/{len(self.api_keys)} ({key_short}) 中途失败: {type(e).__name__}，不重试（避免 token 重复）")
                        raise
                return _wrapped()
            except _RETRYABLE as e:
                if self._is_non_retryable(e):
                    raise
                last_exception = e
                key_short = self.api_keys[i][:10] + "..." + self.api_keys[i][-4:]
                print(f"[API Key] Stream Key {i + 1}/{len(self.api_keys)} ({key_short}) 启动失败: {type(e).__name__}: {str(e)[:120]}，换下一个")
                continue
        raise last_exception or RateLimitError("所有 API Key 均已失败（stream 启动）")

    def stream(self, messages, **kwargs):
        return self._stream_with_retry("stream", messages, **kwargs)

    def astream(self, messages, **kwargs):
        return self._stream_with_retry("astream", messages, **kwargs)

    def __getattr__(self, name):
        raise AttributeError(f"'RotatingKeyChatOpenAI' object has no attribute '{name}'")


def _build_openai_client(**kwargs):
    """统一构造 OpenAI 兼容客户端；任何 key 数都走 RotatingKeyChatOpenAI（业界做法）。

    关键改进：单 key 也走轮换骨架——单 key 走 ChatOpenAI 直连时 401 不会触发退避重试，
    是"切会话丢消息"那个 401 链路的根因。现在单 key 也享受指数退避 + jitter 保护。
    多 key 时增加换 key 维度的额外重试。
    """
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
    # 任何 key 数（含单 key）都走轮换骨架——单 key 也能享受退避重试保护
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
