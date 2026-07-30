import os
import secrets

from .http import SESSION_COOKIE_NAME
from .redis_client import get_redis

SESSION_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


def create_session():
    token = secrets.token_urlsafe(32)
    get_redis().set(f"sessions:{token}", "1", ex=SESSION_TTL_SECONDS)
    return token


def destroy_session(token):
    if token:
        get_redis().delete(f"sessions:{token}")


def is_authenticated(handler):
    cookies = handler._cookies()
    token = cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return False
    return get_redis().get(f"sessions:{token}") is not None


def require_auth(handler):
    """Returns True if authenticated. If not, writes a 401 JSON response
    and returns False -- callers should `return` immediately after."""
    if is_authenticated(handler):
        return True
    handler._send_json(401, {"error": "unauthorized"})
    return False


def check_app_password(password):
    expected = os.environ.get("APP_PASSWORD", "")
    return bool(expected) and secrets.compare_digest(password or "", expected)


def require_cron_auth(handler):
    """Vercel signs scheduled cron requests with `Authorization: Bearer
    $CRON_SECRET`. Verify it so the cron endpoints can't be triggered by
    anyone who guesses the URL. If CRON_SECRET isn't configured, allow the
    request through (useful for local/manual testing) but this should
    always be set in production.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if not expected:
        return True
    auth_header = handler.headers.get("Authorization", "")
    provided = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    if secrets.compare_digest(provided, expected):
        return True
    handler._send_json(401, {"error": "unauthorized"})
    return False
