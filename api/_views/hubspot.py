from _lib import hubspot_client, models
from _lib.auth import require_auth


def config(self):
    if not require_auth(self):
        return
    cfg = models.get_hubspot_config()
    self._send_json(
        200,
        {
            "has_api_key": bool(hubspot_client.get_api_key()),
            "mappings": cfg.get("mappings", []),
        },
    )


def properties(self):
    if not require_auth(self):
        return
    api_key = hubspot_client.get_api_key()
    if not api_key:
        self._send_json(400, {"error": "set the HUBSPOT_API_KEY environment variable in Vercel first"})
        return
    try:
        props = hubspot_client.get_contact_properties(api_key)
    except Exception:
        self._send_json(502, {"error": "could not load contact properties from HubSpot"})
        return
    self._send_json(200, {"properties": props})


def lists(self):
    if not require_auth(self):
        return
    api_key = hubspot_client.get_api_key()
    if not api_key:
        self._send_json(400, {"error": "set the HUBSPOT_API_KEY environment variable in Vercel first"})
        return
    try:
        hs_lists = hubspot_client.list_all_lists(api_key)
    except Exception:
        self._send_json(502, {"error": "could not load lists from HubSpot"})
        return
    self._send_json(200, {"lists": hs_lists})


def add_mapping(self):
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

    api_key = hubspot_client.get_api_key()
    if not api_key:
        self._send_json(400, {"error": "set the HUBSPOT_API_KEY environment variable in Vercel first"})
        return

    try:
        hs_list = hubspot_client.get_list(api_key, list_id)
    except Exception:
        self._send_json(400, {"error": "could not find that HubSpot list with the configured API key"})
        return

    list_name = hs_list.get("list", {}).get("name") or hs_list.get("name") or f"List {list_id}"

    cfg = models.get_hubspot_config()
    mappings = [m for m in cfg.get("mappings", []) if m["list_id"] != list_id]
    mappings.append(
        {
            "list_id": list_id,
            "list_name": list_name,
            "sequence_id": sequence_id,
            "sequence_name": sequence["name"],
        }
    )
    cfg["mappings"] = mappings
    models.save_hubspot_config(cfg)
    self._send_json(200, {"mappings": mappings})


def remove_mapping(self):
    if not require_auth(self):
        return

    body = self._read_json_body()
    list_id = str(body.get("list_id") or "").strip()
    if not list_id:
        self._send_json(400, {"error": "list_id is required"})
        return

    cfg = models.get_hubspot_config()
    cfg["mappings"] = [m for m in cfg.get("mappings", []) if m["list_id"] != list_id]
    models.save_hubspot_config(cfg)
    self._send_json(200, {"mappings": cfg["mappings"]})
