from urllib.parse import urlencode

from _lib import auth as auth_lib
from _lib import gmail
from _lib.auth import current_user_email, require_auth
from _lib.redis_client import get_redis
from _lib.utils import new_id

STATE_TTL_SECONDS = 600


def google_login(self):
    """Kicks off the Google OAuth flow for signing into the app itself -
    separate from the Gmail-account-connect flow in accounts.py, which needs
    send/read scopes this doesn't. Access is still enforced server-side in
    google_callback() regardless of what Google's account picker shows."""
    state = new_id("login_")
    get_redis().set(f"oauth:login_state:{state}", "1", ex=STATE_TTL_SECONDS)
    domains = auth_lib.allowed_login_domains()
    hd = domains[0] if len(domains) == 1 else None
    self._redirect(gmail.build_login_auth_url(state, hd=hd))


def google_callback(self):
    query = self._query()
    if query.get("error", [None])[0]:
        self._redirect("/login.html?error=oauth_failed")
        return

    code = query.get("code", [None])[0]
    state = query.get("state", [None])[0]
    if not code or not state:
        self._redirect("/login.html?error=oauth_failed")
        return

    r = get_redis()
    state_key = f"oauth:login_state:{state}"
    if not r.get(state_key):
        self._redirect("/login.html?error=invalid_state")
        return
    r.delete(state_key)

    try:
        tokens = gmail.exchange_login_code(code)
        email = gmail.get_user_email(tokens["access_token"])
    except Exception:
        self._redirect("/login.html?error=oauth_failed")
        return

    if not email or not auth_lib.is_allowed_login_email(email):
        self._redirect("/login.html?error=domain_not_allowed")
        return

    token = auth_lib.create_session(email)
    self._redirect(f"/login.html?{urlencode({'session_token': token, 'email': email})}")


def logout(self):
    auth_lib.delete_session(auth_lib.token_from_request(self))
    self._send_json(200, {"ok": True})


def status(self):
    if not require_auth(self):
        return
    self._send_json(200, {"authenticated": True, "email": current_user_email(self)})
