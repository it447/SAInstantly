import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import enrollment, gmail, models
from _lib.auth import require_cron_auth
from _lib.http import BaseHandler
from _lib.utils import now_local, render_merge_tags, send_window_hours, unsubscribe_link

# Cap how many emails a single 15-minute cron tick can send, so a large
# backlog (e.g. right after a big HubSpot list import) still trickles out
# over many ticks instead of firing all at once.
PER_TICK_CAP = 20
RETRY_DELAY_SECONDS = 30 * 60
MAX_SEND_ATTEMPTS = 5


def _in_send_window():
    start_hour, end_hour = send_window_hours()
    hour = now_local().hour
    return start_hour <= hour < end_hour


def _build_body(step, contact, sequence_id):
    rendered = render_merge_tags(step["body"], contact)
    link = unsubscribe_link(contact["email"], sequence_id)
    return f"{rendered}\n\n---\nUnsubscribe: {link}"


def run():
    if not _in_send_window():
        return {"ok": True, "skipped": "outside send window"}

    cap = enrollment.daily_cap()
    sent_today = models.daily_sent_total()
    remaining_global = cap - sent_today
    if remaining_global <= 0:
        return {"ok": True, "skipped": "daily cap reached"}

    per_tick_limit = min(remaining_global, PER_TICK_CAP)

    accounts = models.list_accounts()
    remaining_by_account = {
        acc["id"]: acc.get("daily_limit", 0) - models.daily_sent_for_account(acc["id"])
        for acc in accounts
        if acc.get("status") == "connected"
    }

    due = enrollment.due_members(limit=per_tick_limit * 4)

    sent_count = 0
    results = []

    for sequence_id, email, _score in due:
        if sent_count >= per_tick_limit:
            break

        enr = models.get_enrollment(email)
        if not enr or enr.get("sequence_id") != sequence_id or enr.get("status") != "active":
            enrollment.dequeue(sequence_id, email)
            continue

        sequence = models.get_sequence(sequence_id)
        if not sequence or sequence.get("archived") or sequence.get("status") != "active":
            continue  # leave queued; re-checked next tick once the sequence is active again

        step_index = enr["step_index"]
        steps = sequence.get("steps", [])
        if step_index >= len(steps):
            enrollment.advance_or_complete(enr, sequence)
            continue

        account = enrollment.pick_account(accounts, remaining_by_account)
        if not account:
            break  # no connected account has remaining daily capacity

        step = steps[step_index]
        contact = enr["contact"]
        subject = render_merge_tags(step["subject"], contact)
        body = _build_body(step, contact, sequence_id)

        try:
            access_token, refreshed = gmail.get_valid_access_token(account)
            if refreshed:
                account.update(refreshed)
                models.save_account(account)

            send_result = gmail.send_message(
                access_token,
                account["email"],
                email,
                subject,
                body,
                thread_id=enr.get("thread_id"),
                in_reply_to_message_id=enr.get("last_message_id"),
            )

            message_id_header = None
            try:
                msg = gmail.get_message(access_token, send_result["id"])
                for header in msg.get("payload", {}).get("headers", []):
                    if header.get("name") == "Message-ID":
                        message_id_header = header.get("value")
            except Exception:
                pass

            enr["thread_id"] = send_result.get("threadId")
            enr["last_message_id"] = message_id_header
            enr["account_id"] = account["id"]
            enr["attempts"] = 0

            models.record_send_stat(account["id"])
            models.append_log(
                sequence_id,
                {
                    "type": "sent",
                    "email": email,
                    "step_index": step_index,
                    "account_id": account["id"],
                    "subject": subject,
                },
            )
            enrollment.advance_or_complete(enr, sequence)

            remaining_by_account[account["id"]] = remaining_by_account.get(account["id"], 0) - 1
            remaining_global -= 1
            sent_count += 1
            results.append({"email": email, "sequence_id": sequence_id, "status": "sent"})

            time.sleep(random.uniform(0.3, 1.0))

        except Exception as exc:
            attempts = enr.get("attempts", 0) + 1
            enr["attempts"] = attempts
            if attempts >= MAX_SEND_ATTEMPTS:
                enrollment.stop_sequence(enr, "failed", reason=str(exc)[:300])
                results.append({"email": email, "sequence_id": sequence_id, "status": "failed"})
            else:
                models.save_enrollment(enr)
                enrollment.requeue(sequence_id, email, time.time() + RETRY_DELAY_SECONDS)
                results.append({"email": email, "sequence_id": sequence_id, "status": "retry_scheduled"})
            models.append_log(
                sequence_id,
                {"type": "send_error", "email": email, "error": str(exc)[:300]},
            )

    return {"ok": True, "sent": sent_count, "results": results}


class handler(BaseHandler):
    def do_GET(self):
        if not require_cron_auth(self):
            return
        self._send_json(200, run())

    def do_POST(self):
        self.do_GET()
