import random
import time

from _lib import enrollment, gmail, hubspot_client, models
from _lib.auth import require_cron_auth
from _lib.utils import now_local, render_merge_tags, send_window_hours, unsubscribe_link

BATCH_SIZE = 100


def _sync_mapping(mapping):
    cfg = models.get_hubspot_config()
    api_key = cfg["api_key"]
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


def hubspot_sync(self):
    if not require_cron_auth(self):
        return

    cfg = models.get_hubspot_config()
    if not cfg.get("api_key") or not cfg.get("mappings"):
        self._send_json(200, {"ok": True, "results": [], "note": "no HubSpot API key or list mappings configured"})
        return

    results = []
    for mapping in cfg["mappings"]:
        try:
            results.append(_sync_mapping(mapping))
        except Exception as exc:
            results.append({"list_id": mapping.get("list_id"), "error": str(exc)})

    self._send_json(200, {"ok": True, "results": results})


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


def _run_send():
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
            continue

        step_index = enr["step_index"]
        steps = sequence.get("steps", [])
        if step_index >= len(steps):
            enrollment.advance_or_complete(enr, sequence)
            continue

        account = enrollment.pick_account(accounts, remaining_by_account)
        if not account:
            break

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


def send(self):
    if not require_cron_auth(self):
        return
    self._send_json(200, _run_send())


def _thread_has_reply_from_contact(thread, contact_email, account_email):
    contact_email = contact_email.lower()
    account_email = account_email.lower()
    for message in thread.get("messages", []):
        headers = message.get("payload", {}).get("headers", [])
        from_header = next((h["value"] for h in headers if h.get("name") == "From"), "")
        from_header = from_header.lower()
        if contact_email in from_header and account_email not in from_header:
            return True
    return False


def _run_poll_replies():
    checked = 0
    replied = []
    account_cache = {}

    for email in enrollment.list_active_emails():
        enr = models.get_enrollment(email)
        if not enr or enr.get("status") != "active":
            continue
        if not enr.get("thread_id") or not enr.get("account_id"):
            continue

        account_id = enr["account_id"]
        account = account_cache.get(account_id)
        if account is None:
            account = models.get_account(account_id)
            account_cache[account_id] = account or False
        if not account:
            continue

        try:
            access_token, refreshed = gmail.get_valid_access_token(account)
            if refreshed:
                account.update(refreshed)
                models.save_account(account)
                account_cache[account_id] = account

            thread = gmail.get_thread(access_token, enr["thread_id"])
            checked += 1

            if _thread_has_reply_from_contact(thread, email, account["email"]):
                enrollment.stop_sequence(enr, "replied")
                models.record_reply_stat()
                replied.append(email)
        except Exception as exc:
            models.append_log(
                enr["sequence_id"],
                {"type": "reply_check_error", "email": email, "error": str(exc)[:300]},
            )

    return {"ok": True, "checked": checked, "replied": replied}


def poll_replies(self):
    if not require_cron_auth(self):
        return
    self._send_json(200, _run_poll_replies())
