from _lib import models
from _lib.auth import require_auth
from _lib.utils import new_id, now_utc


def _validate(body):
    name = (body.get("name") or "").strip()
    if not name:
        return "name is required"

    steps = body.get("steps") or []
    if not isinstance(steps, list) or len(steps) == 0:
        return "at least one step is required"

    for i, step in enumerate(steps):
        if not (step.get("subject") or "").strip():
            return f"step {i + 1}: subject is required"
        if not (step.get("body") or "").strip():
            return f"step {i + 1}: body is required"
        try:
            int(step.get("delay_days", 0))
        except (TypeError, ValueError):
            return f"step {i + 1}: delay_days must be a number"

    return None


def list_sequences(self):
    if not require_auth(self):
        return
    sequences = [s for s in models.list_sequences() if not s.get("archived")]
    sequences.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    self._send_json(200, {"sequences": sequences})


def save(self):
    if not require_auth(self):
        return

    body = self._read_json_body()
    error = _validate(body)
    if error:
        self._send_json(400, {"error": error})
        return

    steps = [
        {
            "subject": step["subject"].strip(),
            "body": step["body"],
            "delay_days": max(int(step.get("delay_days", 0)), 0),
        }
        for step in body["steps"]
    ]

    sequence_id = body.get("id")
    existing = models.get_sequence(sequence_id) if sequence_id else None

    sequence = {
        "id": existing["id"] if existing else new_id("seq_"),
        "name": body["name"].strip(),
        "status": body.get("status", existing.get("status", "active") if existing else "active"),
        "archived": False,
        "steps": steps,
        "created_at": existing.get("created_at") if existing else now_utc().isoformat(),
        "updated_at": now_utc().isoformat(),
    }
    models.save_sequence(sequence)
    self._send_json(200, {"sequence": sequence})


def delete(self):
    if not require_auth(self):
        return

    body = self._read_json_body()
    sequence_id = body.get("id")
    sequence = models.delete_sequence(sequence_id) if sequence_id else None
    if not sequence:
        self._send_json(404, {"error": "sequence not found"})
        return
    self._send_json(200, {"ok": True})


def logs(self):
    if not require_auth(self):
        return
    query = self._query()
    sequence_id = query.get("id", [None])[0]
    if not sequence_id:
        self._send_json(400, {"error": "id is required"})
        return
    entries = models.get_logs(sequence_id, limit=200)
    self._send_json(200, {"logs": entries})


def enrollments(self):
    if not require_auth(self):
        return
    query = self._query()
    sequence_id = query.get("id", [None])[0]
    if not sequence_id:
        self._send_json(400, {"error": "id is required"})
        return

    records = models.list_enrollments_for_sequence(sequence_id)
    out = []
    for e in records:
        contact = e.get("contact") or {}
        properties = contact.get("properties") or {}
        # An enrollment record only tracks a contact's current sequence, so a
        # contact who has since moved on to a different one shows up here
        # with whatever status they were left at, not this sequence's steps.
        in_this_sequence = e.get("sequence_id") == sequence_id
        out.append(
            {
                "email": e["email"],
                "firstname": properties.get("firstname"),
                "lastname": properties.get("lastname"),
                "status": e.get("status") if in_this_sequence else "moved_to_other_sequence",
                "step_index": e.get("step_index", 0) if in_this_sequence else None,
                "next_send_at": e.get("next_send_at") if in_this_sequence and e.get("status") == "active" else None,
                "enrolled_at": e.get("enrolled_at"),
                "updated_at": e.get("updated_at"),
            }
        )
    out.sort(key=lambda e: e.get("enrolled_at") or "", reverse=True)
    self._send_json(200, {"enrollments": out})
