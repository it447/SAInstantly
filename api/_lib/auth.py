import os
import secrets


def _dashboard_password():
    return os.environ.get("APP_PASSWORD", "")


def is_authenticated(handler):
    # TEMPORARY escape hatch while auth was being debugged: set
    # DISABLE_AUTH=true in Vercel env vars to skip the password check
    # entirely. This removes ALL access control - anyone with the URL can
    # use every feature. Only use on a deployment nobody else can reach yet,
    # and unset it (redeploy) before any real use.
    if os.environ.get("DISABLE_AUTH", "").strip().lower() == "true":
        return True

    expected = _dashboard_password()
    if not expected:
        return False

    # Normal calls send the password as a header (see public/js/api.js,
    # which reads it from localStorage on every request). A handful of
    # protected endpoints are reached via a plain browser navigation instead
    # of a fetch call (e.g. the "Connect Gmail" link, which has to do a real
    # redirect to Google) - browsers can't attach custom headers to those,
    # so those links pass the token as a query param instead.
    token = handler.headers.get("X-Auth-Token", "")
    if not token:
        token = handler._query().get("token", [""])[0]
    return secrets.compare_digest(token, expected)


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
