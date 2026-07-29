import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import models
from _lib.auth import require_auth
from _lib.http import BaseHandler
from _lib.utils import today_str_local


def _public_account(account):
    date_str = today_str_local()
    return {
        "id": account["id"],
        "email": account["email"],
        "provider": account.get("provider", "gmail"),
        "status": account.get("status", "connected"),
        "daily_limit": account.get("daily_limit", 50),
        "sent_today": models.daily_sent_for_account(account["id"], date_str),
        "connected_at": account.get("connected_at"),
    }


class handler(BaseHandler):
    def do_GET(self):
        if not require_auth(self):
            return
        accounts = sorted(models.list_accounts(), key=lambda a: a.get("connected_at") or "")
        self._send_json(200, {"accounts": [_public_account(a) for a in accounts]})
