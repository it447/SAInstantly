import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib.auth import check_app_password, create_session
from _lib.http import SESSION_COOKIE_NAME, BaseHandler


class handler(BaseHandler):
    def do_POST(self):
        body = self._read_json_body()
        password = body.get("password", "")

        if not check_app_password(password):
            self._send_json(401, {"error": "invalid password"})
            return

        token = create_session()
        cookie_header = self._set_cookie_header(SESSION_COOKIE_NAME, token, max_age=60 * 60 * 24 * 7)
        self._send_json(200, {"ok": True}, extra_headers={"Set-Cookie": cookie_header})
