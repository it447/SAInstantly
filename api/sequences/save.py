import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import models
from _lib.auth import require_auth
from _lib.http import BaseHandler
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


class handler(BaseHandler):
    def do_POST(self):
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
