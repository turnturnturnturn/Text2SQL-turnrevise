from __future__ import annotations

import jwt

from vanna.core.user import RequestContext, User, UserResolver


class JwtUserResolver(UserResolver):
    def __init__(self, secret: str):
        if len(secret) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        self.secret = secret

    async def resolve_user(self, request_context: RequestContext) -> User:
        header = request_context.get_header("authorization") or request_context.get_header(
            "Authorization"
        )
        if not header or not header.startswith("Bearer "):
            raise ValueError("Missing Bearer token")
        token = header.removeprefix("Bearer ").strip()
        return self.resolve_token(token)

    def resolve_token(self, token: str) -> User:
        """Resolve a raw token for authenticated management endpoints."""
        try:
            claims = jwt.decode(
                token,
                self.secret,
                algorithms=["HS256"],
                options={"require": ["sub", "exp", "role"]},
            )
        except jwt.PyJWTError as exc:
            raise ValueError("Invalid or expired access token") from exc

        role = claims["role"]
        if role not in {"analyst", "operator", "admin"}:
            raise ValueError("Unsupported role")
        return User(
            id=str(claims["sub"]),
            username=claims.get("username"),
            email=claims.get("email"),
            group_memberships=[role],
            metadata={"role": role},
        )
