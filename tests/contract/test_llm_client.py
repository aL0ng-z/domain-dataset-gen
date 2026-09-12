"""LLM client 适配器契约测试。

外部 LLM 服务（OpenAI-compatible）通过 libs/llm/llm/client.py 封装。测试使用
FakeOpenAI 替换底层 AsyncOpenAI 客户端，验证：
- LLMClient 向正确的 model/messages 发起调用；
- 从响应中正确提取 content 与 usage（input/output tokens）；
- 网络错误触发重试并最终抛错。
契约测试不发出任何真实网络请求，也不真实等待退避（monkeypatch asyncio.sleep）。
"""

import httpx
import pytest
from openai import RateLimitError

from llm.client import LLMClient


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://llm.test/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError("rate limited", response=response, body=None)


class FakeMessageContent:
    def __init__(self, content: str):
        self.content = content


class FakeChoice:
    def __init__(self, content: str):
        self.message = FakeMessageContent(content)


class FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeChatCompletion:
    def __init__(self, content: str, prompt_tokens: int = 10, completion_tokens: int = 5):
        self.choices = [FakeChoice(content)]
        self.usage = FakeUsage(prompt_tokens, completion_tokens)


class FakeCompletions:
    """记录调用参数；fail_times>0 时前 fail_times 次调用抛 RateLimitError。"""

    def __init__(self, calls: list, *, fail_times: int = 0):
        self.calls = calls
        self.fail_times = fail_times

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise _rate_limit_error()
        return FakeChatCompletion(content="你好，世界", prompt_tokens=10, completion_tokens=5)


class FakeOpenAI:
    def __init__(self, base_url: str, api_key: str, completions: FakeCompletions):
        self.base_url = base_url
        self.api_key = api_key
        self.chat = type("FakeChat", (), {"completions": completions})()


def _build_client(monkeypatch, calls: list, *, fail_times: int = 0) -> LLMClient:
    def _factory(base_url, api_key, max_retries):
        assert max_retries == 0  # Task is the sole retry owner by default
        return FakeOpenAI(base_url, api_key, FakeCompletions(calls, fail_times=fail_times))

    monkeypatch.setattr("llm.client.AsyncOpenAI", _factory)
    return LLMClient(base_url="https://llm.test/v1", api_key="secret", model_name="deepseek-chat")


@pytest.fixture
def no_backoff(monkeypatch):
    """消除重试退避等待，加速测试。"""
    import asyncio

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


async def test_chat_completion_records_call_arguments(monkeypatch):
    calls: list = []
    client = _build_client(monkeypatch, calls)
    messages = [{"role": "user", "content": "hello"}]

    response = await client.chat_completion(messages)

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["model"] == "deepseek-chat"
    assert kwargs["messages"] == messages
    assert "temperature" not in kwargs  # 未配置时不应携带
    assert response.content == "你好，世界"
    assert response.input_tokens == 10
    assert response.output_tokens == 5
    assert response.latency_ms >= 0


async def test_chat_completion_sends_response_format(monkeypatch):
    calls: list = []
    client = _build_client(monkeypatch, calls)
    await client.chat_completion(
        [{"role": "user", "content": "x"}], response_format={"type": "json_object"}
    )
    assert calls[0]["response_format"] == {"type": "json_object"}


async def test_chat_completion_retries_on_rate_limit(monkeypatch, no_backoff):
    calls: list = []
    client = _build_client(monkeypatch, calls, fail_times=1)

    response = await client.chat_completion([{"role": "user", "content": "x"}], max_retries=1)

    assert len(calls) == 2  # 一次失败 + 一次成功
    assert response.content == "你好，世界"


async def test_chat_completion_raises_after_all_retries(monkeypatch, no_backoff):
    calls: list = []
    client = _build_client(monkeypatch, calls, fail_times=10)

    with pytest.raises(RateLimitError):
        await client.chat_completion([{"role": "user", "content": "x"}], max_retries=3)

    # max_retries=3 -> 初次 + 3 次重试
    assert len(calls) == 4


async def test_chat_completion_default_zero_retries_calls_provider_once(monkeypatch):
    calls: list = []
    client = _build_client(monkeypatch, calls, fail_times=1)
    with pytest.raises(RateLimitError):
        await client.chat_completion([{"role": "user", "content": "x"}])
    assert len(calls) == 1


@pytest.mark.parametrize("status, retriable", [(401, False), (400, False), (429, True), (500, True)])
def test_provider_status_error_task_classification(status, retriable):
    from openai import APIStatusError

    from app.workers.runner import classify_error
    response = httpx.Response(status, request=httpx.Request("POST", "https://llm.test/v1/chat/completions"))
    _, actual = classify_error(APIStatusError("provider error", response=response, body=None))
    assert actual is retriable
