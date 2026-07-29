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
        sequence_id = body.get("id")
        sequence = models.delete_sequence(sequence_id) if sequence_id else None
        if not sequence:
            self._send_json(404, {"error": "sequence not found"})
            return
        self._send_json(200, {"ok": True})
