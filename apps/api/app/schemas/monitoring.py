from pydantic import BaseModel


class MonitoringSummary(BaseModel):
    total_input_tokens: int
    total_output_tokens: int
    total_requests: int
    total_errors: int
    avg_latency_ms: float


class UsageByGroup(BaseModel):
    group: str
    input_tokens: int
    output_tokens: int
    request_count: int


class DailyTrend(BaseModel):
    date: str
    input_tokens: int
    output_tokens: int
    request_count: int
