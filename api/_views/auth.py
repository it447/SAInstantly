import os

from _lib.auth import check_app_password, create_session, destroy_session, is_authenticated
from _lib.http import SESSION_COOKIE_NAME


def login(self):
    body = self._read_json_body()
    password = body.get("password", "")

    if not check_app_password(password):
        # Temporary diagnostic (never the actual values): confirms what the
        # server received vs. what it expects, without revealing either.
        expected = os.environ.get("APP_PASSWORD") or ""
        self._send_json(
            401,
            {
                "error": "invalid password",
                "received_length": len(password),
                "expected_length": len(expected),
                "received_matches_expected_after_strip": password.strip() == expected.strip(),
            },
        )
        return

    token = create_session()
    cookie_header = self._set_cookie_header(SESSION_COOKIE_NAME, token, max_age=60 * 60 * 24 * 7)
    self._send_json(200, {"ok": True}, extra_headers={"Set-Cookie": cookie_header})


def logout(self):
    token = self._cookies().get(SESSION_COOKIE_NAME)
    destroy_session(token)
    cookie_header = self._set_cookie_header(SESSION_COOKIE_NAME, "", max_age=0)
    self._send_json(200, {"ok": True}, extra_headers={"Set-Cookie": cookie_header})


def status(self):
    self._send_json(200, {"authenticated": is_authenticated(self)})


def debug_password_config(self):
    """Temporary diagnostic: reveals whether APP_PASSWORD is configured and
    a couple of common copy-paste footguns, without ever exposing the
    actual value. Safe to remove once login issues are resolved."""
    raw = os.environ.get("APP_PASSWORD")
    self._send_json(
        200,
        {
            "is_set": raw is not None,
            "length": len(raw) if raw is not None else 0,
            "has_leading_or_trailing_whitespace": bool(raw) and raw != raw.strip(),
        },
    )
