import json
import os
import secrets
import time

from .redis_client import get_redis
from .utils import new_id

SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days

# Domains allowed to sign into the app via Google. Configurable via
# ALLOWED_LOGIN_DOMAINS (comma-separated), with a hard-coded default so
# access stays restricted even if that env var is never set - mirrors
# utils.protected_domains()'s pattern for the same reason.
DEFAULT_ALLOWED_LOGIN_DOMAINS = ["scalearmy.com"]


def allowed_login_domains():
    configured = os.environ.get("ALLOWED_LOGIN_DOMAINS", "")
    domains = [d.strip().lower() for d in configured.split(",") if d.strip()]
    return domains or DEFAULT_ALLOWED_LOGIN_DOMAINS


def is_allowed_login_email(email):
    email = (email or "").strip().lower()
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1]
    return any(domain == d or domain.endswith(f".{d}") for d in allowed_login_domains())


def create_session(email):
    token = new_id("sess_")
    r = get_redis()
    r.set(f"session:{token}", json.dumps({"email": email, "created_at": time.time()}), ex=SESSION_TTL_SECONDS)
    return token


def get_session(token):
    if not token:
        return None
    r = get_redis()
    raw = r.get(f"session:{token}")
    return json.loads(raw) if raw else None


def delete_session(token):
    if not token:
        return
    get_redis().delete(f"session:{token}")


def token_from_request(handler):
    # Normal calls send the session token as a header (see public/js/api.js,
    # which reads it from localStorage on every request). A handful of
    # protected endpoints are reached via a plain browser navigation instead
    # of a fetch call (e.g. the "Connect Gmail" link, which has to do a real
    # redirect to Google) - browsers can't attach custom headers to those,
    # so those links pass the token as a query param instead.
    token = handler.headers.get("X-Auth-Token", "")
    if not token:
        token = handler._query().get("token", [""])[0]
    return token


def current_user_email(handler):
    session = get_session(token_from_request(handler))
    return session["email"] if session else None


def is_authenticated(handler):
    # TEMPORARY escape hatch while auth was being debugged: set
    # DISABLE_AUTH=true in Vercel env vars to skip the session check
    # entirely. This removes ALL access control - anyone with the URL can
    # use every feature. Only use on a deployment nobody else can reach yet,
    # and unset it (redeploy) before any real use.
    if os.environ.get("DISABLE_AUTH", "").strip().lower() == "true":
        return True

    return get_session(token_from_request(handler)) is not None


def require_auth(handler):
    """Returns True if authenticated. If not, writes a 401 JSON response
    and returns False -- callers should `return` immediately after."""
    if is_authenticated(handler):
        return True
    handler._send_json(401, {"error": "unauthorized"})
    return False


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
