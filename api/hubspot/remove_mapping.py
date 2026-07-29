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
        list_id = str(body.get("list_id") or "").strip()
        if not list_id:
            self._send_json(400, {"error": "list_id is required"})
            return

        config = models.get_hubspot_config()
        config["mappings"] = [m for m in config.get("mappings", []) if m["list_id"] != list_id]
        models.save_hubspot_config(config)
        self._send_json(200, {"mappings": config["mappings"]})
