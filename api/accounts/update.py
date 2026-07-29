import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import models
from _lib.auth import require_auth
from _lib.http import BaseHandler
from _lib.utils import now_utc


class handler(BaseHandler):
    def do_POST(self):
        if not require_auth(self):
            return

        body = self._read_json_body()
        account_id = body.get("id")
        account = models.get_account(account_id) if account_id else None
        if not account:
            self._send_json(404, {"error": "account not found"})
            return

        if "daily_limit" in body:
            try:
                account["daily_limit"] = max(int(body["daily_limit"]), 0)
            except (TypeError, ValueError):
                self._send_json(400, {"error": "daily_limit must be a number"})
                return

        account["updated_at"] = now_utc().isoformat()
        models.save_account(account)
        self._send_json(200, {"ok": True})
