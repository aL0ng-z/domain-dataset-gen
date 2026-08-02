from pydantic import BaseModel, Field

from domain.schemas import RequestSchema


class LoginRequest(RequestSchema):
    username: str
    password: str


class RegisterRequest(RequestSchema):
    username: str
    email: str
    password: str
    role: str = "editor"


class TokenResponse(BaseModel):
    """登录/刷新响应。access_token 仅用于受保护入口；refresh_token 仅用于刷新接口。"""

    access_token: str = Field(description="access token（type=access），用于受保护 HTTP/PDF/WebSocket 入口")
    refresh_token: str = Field(description="refresh token（type=refresh），仅用于 POST /api/auth/refresh")
    token_type: str = Field(default="bearer", description="令牌类型，恒为 bearer")


class RefreshRequest(RequestSchema):
    refresh_token: str = Field(description="有效的 refresh token（type=refresh）")
