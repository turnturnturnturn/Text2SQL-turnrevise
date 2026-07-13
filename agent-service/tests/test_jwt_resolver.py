from datetime import datetime, timedelta, timezone

import jwt
import pytest
from vanna.core.user import RequestContext

from app.security.jwt_resolver import JwtUserResolver


SECRET = "test-secret-that-is-at-least-thirty-two-characters"


@pytest.mark.asyncio
async def test_resolves_role_from_valid_jwt():
    token = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000001",
            "username": "operator",
            "role": "operator",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        SECRET,
        algorithm="HS256",
    )
    user = await JwtUserResolver(SECRET).resolve_user(
        RequestContext(headers={"authorization": f"Bearer {token}"})
    )
    assert user.group_memberships == ["operator"]
    assert user.metadata["role"] == "operator"


@pytest.mark.asyncio
async def test_rejects_missing_token():
    with pytest.raises(ValueError, match="Bearer"):
        await JwtUserResolver(SECRET).resolve_user(RequestContext(headers={}))

