from dataclasses import dataclass


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


# Usage callback type: async function that receives usage data
# Will be set by the API app to log to llm_usage_logs table
UsageCallback = None  # Placeholder, set at app startup
