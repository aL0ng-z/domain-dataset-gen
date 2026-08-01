from pydantic import BaseModel

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
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(RequestSchema):
    refresh_token: str
