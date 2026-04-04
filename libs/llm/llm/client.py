import time

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from llm.usage import LLMResponse


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def chat_completion(
        self,
        messages: list[dict],
        response_format: dict | None = None,
        max_retries: int = 3,
    ) -> LLMResponse:
        last_error = None

        for attempt in range(max_retries):
            try:
                start_ms = int(time.time() * 1000)

                kwargs = {
                    "model": self.model_name,
                    "messages": messages,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                }
                if response_format:
                    kwargs["response_format"] = response_format

                response = await self.client.chat.completions.create(**kwargs)

                latency_ms = int(time.time() * 1000) - start_ms
                usage = response.usage

                return LLMResponse(
                    content=response.choices[0].message.content or "",
                    input_tokens=usage.prompt_tokens if usage else 0,
                    output_tokens=usage.completion_tokens if usage else 0,
                    latency_ms=latency_ms,
                )

            except (RateLimitError, APITimeoutError, APIError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    import asyncio
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                continue

        raise last_error
