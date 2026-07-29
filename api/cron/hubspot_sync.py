import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import enrollment, hubspot_client, models
from _lib.auth import require_cron_auth
from _lib.http import BaseHandler

BATCH_SIZE = 100


def sync_mapping(mapping):
    config = models.get_hubspot_config()
    api_key = config["api_key"]
    list_id = mapping["list_id"]
    sequence_id = mapping["sequence_id"]

    sequence = models.get_sequence(sequence_id)
    if not sequence or sequence.get("archived") or sequence.get("status") != "active":
        return {"list_id": list_id, "skipped": "sequence not active"}

    member_ids = hubspot_client.get_all_list_member_ids(api_key, list_id)
    new_ids = [i for i in member_ids if not models.hubspot_contact_seen(list_id, i)]

    enrolled = 0
    for i in range(0, len(new_ids), BATCH_SIZE):
        chunk = new_ids[i : i + BATCH_SIZE]
        contacts = hubspot_client.batch_read_contacts(api_key, chunk)
        contacts_by_id = {c["hubspot_id"]: c for c in contacts}

        for hubspot_id in chunk:
            models.mark_hubspot_contact_seen(list_id, hubspot_id)
            contact = contacts_by_id.get(hubspot_id)
            if not contact:
                continue
            result = enrollment.enroll_contact(sequence_id, contact, source="hubspot")
            if result:
                enrolled += 1

    return {"list_id": list_id, "checked": len(new_ids), "enrolled": enrolled}


class handler(BaseHandler):
    def do_GET(self):
        if not require_cron_auth(self):
            return

        config = models.get_hubspot_config()
        if not config.get("api_key") or not config.get("mappings"):
            self._send_json(200, {"ok": True, "results": [], "note": "no HubSpot API key or list mappings configured"})
            return

        results = []
        for mapping in config["mappings"]:
            try:
                results.append(sync_mapping(mapping))
            except Exception as exc:
                results.append({"list_id": mapping.get("list_id"), "error": str(exc)})

        self._send_json(200, {"ok": True, "results": results})

    def do_POST(self):
        self.do_GET()
