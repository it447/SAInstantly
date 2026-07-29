import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import enrollment, gmail, models
from _lib.auth import require_cron_auth
from _lib.http import BaseHandler


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


def run():
    checked = 0
    replied = []

    account_cache = {}

    for email in enrollment.list_active_emails():
        enr = models.get_enrollment(email)
        if not enr or enr.get("status") != "active":
            continue
        if not enr.get("thread_id") or not enr.get("account_id"):
            continue  # no message sent yet, nothing to check for a reply on

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


class handler(BaseHandler):
    def do_GET(self):
        if not require_cron_auth(self):
            return
        self._send_json(200, run())

    def do_POST(self):
        self.do_GET()
