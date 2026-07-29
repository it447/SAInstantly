import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib.auth import is_authenticated
from _lib.http import BaseHandler


class handler(BaseHandler):
    def do_GET(self):
        self._send_json(200, {"authenticated": is_authenticated(self)})
