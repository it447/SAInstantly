import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib.auth import destroy_session
from _lib.http import SESSION_COOKIE_NAME, BaseHandler


class handler(BaseHandler):
    def do_POST(self):
        token = self._cookies().get(SESSION_COOKIE_NAME)
        destroy_session(token)
        cookie_header = self._set_cookie_header(SESSION_COOKIE_NAME, "", max_age=0)
        self._send_json(200, {"ok": True}, extra_headers={"Set-Cookie": cookie_header})
