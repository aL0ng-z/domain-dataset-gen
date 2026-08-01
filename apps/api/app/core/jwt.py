"""统一 JWT 令牌语义模块（T01）。

全仓库唯一负责令牌签发与解码的模块。所有受保护入口（HTTP、PDF、WebSocket）
必须通过 :func:`decode_token` 校验令牌，禁止自行调用 ``jwt.decode``。

令牌语义：
- access token  声明：sub、type=access、iat、exp
- refresh token 声明：sub、type=refresh、iat、exp

签发与验证只能使用 ``settings.jwt_algorithm`` 指定的算法（见任务卡 §5.1）。
"""

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from jose import JWTError, jwt

from app.config import settings

# 认证失败统一响应文案：不泄露令牌内容、用户是否存在或签名校验细节（任务卡 §5.2）。
INVALID_TOKEN_MESSAGE = "无效的认证令牌"


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


# decode_token 后必须存在的声明（python-jose 的 options.require 不强制存在，需手工校验）。
_REQUIRED_CLAIMS = ("sub", "type", "iat", "exp")


def encode_token(
    *,
    user_id: uuid.UUID,
    token_type: TokenType,
    expires_delta: timedelta,
    extra: dict[str, Any] | None = None,
) -> str:
    """签发指定类型的 JWT 令牌。

    声明固定包含 sub/type/iat/exp；extra 用于携带非授权用途的附加声明
    （如 role，仅用于展示，不作为最终授权依据）。
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": token_type.value,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """解码并校验 JWT，返回声明 dict；任一校验失败统一抛 :class:`JWTError`。

    校验项：
    - 签名与算法（仅接受 ``settings.jwt_algorithm``）
    - 必需声明 sub/type/iat/exp 存在
    - exp 未过期（jose 自动校验）
    - type 与 expected_type 一致

    sub 的 UUID 格式由调用方按用途解析；解析失败统一按 401 处理（任务卡 §4）。
    """
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        options={"verify_exp": True},
    )
    for claim in _REQUIRED_CLAIMS:
        if claim not in payload:
            raise JWTError(f"缺少必需声明: {claim}")
    if payload.get("type") != expected_type.value:
        raise JWTError("令牌类型不匹配")
    return payload


def create_access_token(user_id: uuid.UUID, *, role: str | None = None) -> str:
    """签发 access token（type=access）。"""
    extra = {"role": role} if role is not None else None
    return encode_token(
        user_id=user_id,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
        extra=extra,
    )


def create_refresh_token(user_id: uuid.UUID) -> str:
    """签发 refresh token（type=refresh）。"""
    return encode_token(
        user_id=user_id,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.jwt_refresh_token_expire_days),
    )


def resolve_user_id(token: str, expected_type: TokenType) -> uuid.UUID:
    """解码并返回 sub 对应的 UUID；任何失败抛 :class:`JWTError`（不产生 500）。"""
    payload = decode_token(token, expected_type)
    sub = payload.get("sub")
    if sub is None:
        raise JWTError("缺少 sub 声明")
    try:
        return uuid.UUID(str(sub))
    except (ValueError, AttributeError) as e:
        raise JWTError("sub 不是有效 UUID") from e
