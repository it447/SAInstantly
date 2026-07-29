import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import models
from _lib.auth import require_auth
from _lib.http import BaseHandler


class handler(BaseHandler):
    def do_POST(self):
        if not require_auth(self):
            return

        body = self._read_json_body()
        account_id = body.get("id")
        account = models.delete_account(account_id) if account_id else None
        if not account:
            self._send_json(404, {"error": "account not found"})
            return
        self._send_json(200, {"ok": True})
