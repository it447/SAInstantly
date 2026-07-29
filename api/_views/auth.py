from _lib.auth import check_app_password, create_session, destroy_session, is_authenticated
from _lib.http import SESSION_COOKIE_NAME


def login(self):
    body = self._read_json_body()
    password = body.get("password", "")

    if not check_app_password(password):
        self._send_json(401, {"error": "invalid password"})
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
