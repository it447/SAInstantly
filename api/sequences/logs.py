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
        query = self._query()
        sequence_id = query.get("id", [None])[0]
        if not sequence_id:
            self._send_json(400, {"error": "id is required"})
            return
        logs = models.get_logs(sequence_id, limit=200)
        self._send_json(200, {"logs": logs})
