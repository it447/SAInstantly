import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import models
from _lib.auth import require_auth
from _lib.http import BaseHandler


class handler(BaseHandler):
    def do_GET(self):
        if not require_auth(self):
            return
        sequences = [s for s in models.list_sequences() if not s.get("archived")]
        sequences.sort(key=lambda s: s.get("created_at") or "", reverse=True)
        self._send_json(200, {"sequences": sequences})
