from _lib import enrollment, gmail, models
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

    account_ids = body.get("account_ids")
    if account_ids is not None and not isinstance(account_ids, list):
        return "account_ids must be a list"

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

    account_ids = body.get("account_ids")
    if account_ids is None:
        account_ids = existing.get("account_ids", []) if existing else []

    sequence = {
        "id": existing["id"] if existing else new_id("seq_"),
        "name": body["name"].strip(),
        "status": body.get("status", existing.get("status", "active") if existing else "active"),
        "archived": False,
        "steps": steps,
        # Which connected accounts this sequence is allowed to send from -
        # empty means "any connected account" (today's default rotation).
        "account_ids": [str(a) for a in account_ids],
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


def detail(self):
    """Everything the standalone per-sequence page needs: the sequence
    itself, roll-up stats, and every contact ever enrolled in it with their
    current status and next send time."""
    if not require_auth(self):
        return
    query = self._query()
    sequence_id = query.get("id", [None])[0]
    if not sequence_id:
        self._send_json(400, {"error": "id is required"})
        return

    sequence = models.get_sequence(sequence_id)
    if not sequence:
        self._send_json(404, {"error": "sequence not found"})
        return

    # Self-heal: contacts enrolled before the sequence_contacts index existed
    # (or before this specific contact's index entry was otherwise written)
    # are still tracked in active_enrollments, so backfill from there on
    # every load instead of requiring a one-off manual fix.
    for active_email in enrollment.list_active_emails():
        active_enr = models.get_enrollment(active_email)
        if active_enr:
            models.add_sequence_contact(active_enr["sequence_id"], active_email)

    records = models.list_enrollments_for_sequence(sequence_id)
    stats = {"total": len(records), "active": 0, "completed": 0, "replied": 0, "unsubscribed": 0, "failed": 0, "moved_to_other_sequence": 0}
    contacts = []
    for e in records:
        contact = e.get("contact") or {}
        properties = contact.get("properties") or {}
        # An enrollment record only tracks a contact's current sequence, so a
        # contact who has since moved on to a different one shows up here
        # with whatever status they were left at, not this sequence's steps.
        in_this_sequence = e.get("sequence_id") == sequence_id
        status = e.get("status") if in_this_sequence else "moved_to_other_sequence"
        stats[status] = stats.get(status, 0) + 1
        contacts.append(
            {
                "email": e["email"],
                "firstname": properties.get("firstname"),
                "lastname": properties.get("lastname"),
                "status": status,
                "step_index": e.get("step_index", 0) if in_this_sequence else None,
                "next_send_at": e.get("next_send_at") if in_this_sequence and status == "active" else None,
                "enrolled_at": e.get("enrolled_at"),
                "updated_at": e.get("updated_at"),
            }
        )
    contacts.sort(key=lambda e: e.get("enrolled_at") or "", reverse=True)
    stats["sent"] = models.get_stat(f"stats:sequence:{sequence_id}:sent") or 0

    account_ids = sequence.get("account_ids") or []
    if account_ids:
        accounts_by_id = {a["id"]: a for a in models.list_accounts()}
        sending_accounts = [accounts_by_id[a_id]["email"] for a_id in account_ids if a_id in accounts_by_id]
    else:
        sending_accounts = []

    self._send_json(
        200,
        {
            "sequence": {
                "id": sequence["id"],
                "name": sequence["name"],
                "status": sequence.get("status"),
                "steps": len(sequence.get("steps", [])),
                "sending_accounts": sending_accounts,
            },
            "stats": stats,
            "contacts": contacts,
        },
    )


def thread(self):
    """The actual sent email(s) and any replies for one enrolled contact, so
    you can read the conversation instead of just a status badge."""
    if not require_auth(self):
        return
    query = self._query()
    email = (query.get("email", [None])[0] or "").strip().lower()
    sequence_id = query.get("sequence_id", [None])[0]
    if not email or not sequence_id:
        self._send_json(400, {"error": "email and sequence_id are required"})
        return

    enr = models.get_enrollment(email)
    if not enr or enr.get("sequence_id") != sequence_id:
        self._send_json(404, {"error": "no enrollment found for this contact in this sequence"})
        return

    if not enr.get("thread_id") or not enr.get("account_id"):
        self._send_json(200, {"messages": [], "note": "No email has been sent to this contact yet."})
        return

    account = models.get_account(enr["account_id"])
    if not account:
        self._send_json(404, {"error": "The account this was sent from no longer exists."})
        return

    access_token, refreshed = gmail.get_valid_access_token(account)
    if refreshed:
        account.update(refreshed)
        models.save_account(account)

    thread_data = gmail.get_thread_full(access_token, enr["thread_id"])
    account_email = account["email"].strip().lower()

    messages = []
    for msg in thread_data.get("messages", []):
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        from_header = headers.get("From", "")
        messages.append(
            {
                "from": from_header,
                "to": headers.get("To", ""),
                "date": headers.get("Date", ""),
                "subject": headers.get("Subject", ""),
                "body": gmail.extract_message_text(msg.get("payload", {})),
                "direction": "sent" if account_email in from_header.lower() else "received",
            }
        )

    self._send_json(200, {"messages": messages})
