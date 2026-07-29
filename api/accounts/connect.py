import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib.auth import is_authenticated
from _lib.gmail import build_auth_url
from _lib.http import BaseHandler
from _lib.redis_client import get_redis
from _lib.utils import new_id

STATE_TTL_SECONDS = 600


class handler(BaseHandler):
    def do_GET(self):
        if not is_authenticated(self):
            self._redirect("/login.html")
            return

        state = new_id("oauth_")
        get_redis().set(f"oauth:state:{state}", "1", ex=STATE_TTL_SECONDS)
        self._redirect(build_auth_url(state))
