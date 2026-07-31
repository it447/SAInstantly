"""Temporary diagnostic endpoints. Safe to remove once the app is stable --
none of these expose secret values. queue_check does return contact emails,
so it requires auth; the others don't touch anything sensitive."""
import os
import time

from _lib import enrollment, models
from _lib.auth import require_auth
from _lib.redis_client import get_redis
from _lib.utils import now_local, now_utc, send_window_hours


def redis_check(self):
    result = {
        "upstash_url_is_set": bool(os.environ.get("UPSTASH_REDIS_REST_URL")),
        "upstash_token_is_set": bool(os.environ.get("UPSTASH_REDIS_REST_TOKEN")),
    }
    try:
        r = get_redis()
        r.set("debug:ping", "pong", ex=30)
        result["ok"] = r.get("debug:ping") == "pong"
    except Exception as exc:
        result["ok"] = False
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:500]
    self._send_json(200, result)


def queue_check(self):
    if not require_auth(self):
        return

    start_hour, end_hour = send_window_hours()
    local_now = now_local()
    now_ts = time.time()

    due = enrollment.due_members(limit=50, before_ts=now_ts + 24 * 3600)
    queue_preview = []
    for sequence_id, email, score in due:
        queue_preview.append(
            {
                "sequence_id": sequence_id,
                "email": email,
                "next_send_at_unix": score,
                "seconds_until_due": round(score - now_ts),
                "is_due_now": score <= now_ts,
            }
        )
    queue_preview.sort(key=lambda i: i["next_send_at_unix"])

    accounts = models.list_accounts()
    connected = [a for a in accounts if a.get("status") == "connected"]

    self._send_json(
        200,
        {
            "now_utc": now_utc().isoformat(),
            "now_local": local_now.isoformat(),
            "send_window_hours": [start_hour, end_hour],
            "currently_in_send_window": start_hour <= local_now.hour < end_hour,
            "daily_cap": enrollment.daily_cap(),
            "sent_today": models.daily_sent_total(),
            "connected_accounts": len(connected),
            "account_daily_limits": [
                {"email": a["email"], "daily_limit": a.get("daily_limit"), "sent_today": models.daily_sent_for_account(a["id"])}
                for a in connected
            ],
            "queue_preview": queue_preview,
        },
    )


def backfill_sequence_index(self):
    """One-time fix: the sequence.html contacts view is powered by a
    sequence_contacts:{sequence_id} index that only started being populated
    when that feature shipped, so contacts enrolled before then are invisible
    to it. This walks the active_enrollments set (the only existing registry
    of enrollment emails) and backfills the index for anyone still active.
    Contacts that already completed/replied/unsubscribed before this
    shipped won't be recovered - only currently-active ones are. Safe to
    remove once run."""
    if not require_auth(self):
        return

    backfilled = []
    for email in enrollment.list_active_emails():
        enr = models.get_enrollment(email)
        if not enr:
            continue
        models.add_sequence_contact(enr["sequence_id"], email)
        backfilled.append({"email": email, "sequence_id": enr["sequence_id"]})

    self._send_json(200, {"ok": True, "backfilled": backfilled})
