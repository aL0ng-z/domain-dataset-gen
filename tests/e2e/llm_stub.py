"""仅用于本地浏览器验收的确定性 OpenAI-compatible 替身。

启动：conda activate DatasetGen 后，仓库根目录执行
python -m uvicorn tests.e2e.llm_stub:app --host 127.0.0.1 --port 18765
不访问真实模型服务，避免浏览器验收产生费用。
"""

import json

from fastapi import FastAPI, Request

app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok", "mode": "test-stub"}


@app.post("/v1/chat/completions")
async def complete(request: Request):
    payload = await request.json()
    answer = {"question": "What does a compressor increase?", "answer": "A compressor increases pressure."}
    if "reference_answer" in str(payload.get("messages")):
        answer = {"question": answer["question"], "reference_answer": answer["answer"]}
    return {
        "id": "review-local", "object": "chat.completion", "created": 1,
        "model": payload.get("model", "review-stub"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(answer)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
    }
