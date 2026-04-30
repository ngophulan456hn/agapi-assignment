"""
Authentication middleware.

Validates Bearer tokens on every request that is not in PUBLIC_PATHS.
The per-route dependencies (get_current_user, etc.) remain in place for
finer-grained checks (active flag, admin role, …).  This middleware acts
as the first gate so that protected routes never even reach the handler
with a missing or revoked token.
"""

from fastapi import Request, status
from fastapi.responses import JSONResponse
from jose import JWTError
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.deps import BLACKLIST_PREFIX
from app.core.redis import get_redis
from app.core.security import decode_token

# Exact paths that are accessible without a token.
# NOTE: /auth/logout and /auth/me intentionally require a token and are
#       therefore NOT listed here.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/auth/login",
        "/auth/register",
        "/auth/refresh",
        "/auth/otp/send",
        "/auth/otp/verify",
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)

_UNAUTHORIZED = JSONResponse(
    status_code=status.HTTP_401_UNAUTHORIZED,
    content={"detail": "Not authenticated"},
    headers={"WWW-Authenticate": "Bearer"},
)

_INVALID_TOKEN = JSONResponse(
    status_code=status.HTTP_401_UNAUTHORIZED,
    content={"detail": "Could not validate credentials"},
    headers={"WWW-Authenticate": "Bearer"},
)

_REVOKED_TOKEN = JSONResponse(
    status_code=status.HTTP_401_UNAUTHORIZED,
    content={"detail": "Token has been revoked"},
    headers={"WWW-Authenticate": "Bearer"},
)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Pass through public paths and FastAPI internal paths (e.g. /openapi.json)
        if path in PUBLIC_PATHS or path.startswith("/docs/"):
            return await call_next(request)

        # ── Extract token ──────────────────────────────────────────────
        authorization: str = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return _UNAUTHORIZED

        token = authorization[len("Bearer ") :].strip()

        # ── Validate JWT ───────────────────────────────────────────────
        try:
            payload = decode_token(token)
            user_id: str | None = payload.get("sub")
            token_type: str | None = payload.get("type")
            if not user_id or token_type != "access":
                return _INVALID_TOKEN
        except JWTError:
            return _INVALID_TOKEN

        # ── Check blacklist ────────────────────────────────────────────
        redis = await get_redis()
        if await redis.exists(f"{BLACKLIST_PREFIX}{token}"):
            return _REVOKED_TOKEN

        # Attach user_id to request state so downstream handlers can use it
        # without decoding the token a second time (optional optimisation).
        request.state.user_id = user_id

        return await call_next(request)
