import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import hubspot_client, models
from _lib.auth import require_auth
from _lib.http import BaseHandler


class handler(BaseHandler):
    def do_POST(self):
        if not require_auth(self):
            return

        body = self._read_json_body()
        list_id = str(body.get("list_id") or "").strip()
        sequence_id = (body.get("sequence_id") or "").strip()

        if not list_id or not sequence_id:
            self._send_json(400, {"error": "list_id and sequence_id are required"})
            return

        sequence = models.get_sequence(sequence_id)
        if not sequence or sequence.get("archived"):
            self._send_json(404, {"error": "sequence not found"})
            return

        config = models.get_hubspot_config()
        if not config.get("api_key"):
            self._send_json(400, {"error": "connect a HubSpot API key first"})
            return

        try:
            hs_list = hubspot_client.get_list(config["api_key"], list_id)
        except Exception:
            self._send_json(400, {"error": "could not find that HubSpot list with the configured API key"})
            return

        list_name = hs_list.get("list", {}).get("name") or hs_list.get("name") or f"List {list_id}"

        mappings = [m for m in config.get("mappings", []) if m["list_id"] != list_id]
        mappings.append(
            {
                "list_id": list_id,
                "list_name": list_name,
                "sequence_id": sequence_id,
                "sequence_name": sequence["name"],
            }
        )
        config["mappings"] = mappings
        models.save_hubspot_config(config)
        self._send_json(200, {"mappings": mappings})
