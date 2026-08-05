"""Global, cross-sequence do-not-contact list. Checked in enrollment.enroll_contact
before enrolling a contact in ANY sequence, auto-populated on bounces and
unsubscribes (see enrollment.stop_sequence), plus manual add/remove here for
compliance-driven cases (a phone request, a legal ask, an accidental add)."""
from _lib import models
from _lib.auth import require_auth


def list_suppression(self):
    if not require_auth(self):
        return
    self._send_json(200, {"suppressed": models.list_suppressed()})


def add_suppression(self):
    if not require_auth(self):
        return
    body = self._read_json_body()
    email = (body.get("email") or "").strip().lower()
    reason = (body.get("reason") or "").strip() or "manually added"
    if not email or "@" not in email:
        self._send_json(400, {"error": "a valid email is required"})
        return
    models.add_to_suppression_list(email, reason=reason, source="manual")
    self._send_json(200, {"ok": True})


def remove_suppression(self):
    if not require_auth(self):
        return
    body = self._read_json_body()
    email = (body.get("email") or "").strip().lower()
    if not email:
        self._send_json(400, {"error": "email is required"})
        return
    removed = models.remove_from_suppression_list(email)
    if not removed:
        self._send_json(404, {"error": "that email is not currently on the suppression list"})
        return
    self._send_json(200, {"ok": True})


def audit_log(self):
    if not require_auth(self):
        return
    self._send_json(200, {"entries": models.get_suppression_audit(limit=200)})
