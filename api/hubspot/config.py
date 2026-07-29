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
        config = models.get_hubspot_config()
        self._send_json(
            200,
            {
                "has_api_key": bool(config.get("api_key")),
                "mappings": config.get("mappings", []),
            },
        )
