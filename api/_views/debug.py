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


def poll_replies_now(self):
    """Manually runs the same reply-check the cron does (every 30 min on
    schedule), for troubleshooting - lets you confirm reply detection works
    right after replying instead of waiting for the next scheduled tick.
    Also reports why a contact was skipped (no thread yet, account missing,
    etc.) so a real bug doesn't look identical to "just needs to wait".
    Safe to remove once the app is stable."""
    if not require_auth(self):
        return

    from _lib import gmail
    from _views.cron import _thread_has_reply_from_contact

    checked = []
    for email in enrollment.list_active_emails():
        enr = models.get_enrollment(email)
        if not enr or enr.get("status") != "active":
            checked.append({"email": email, "skipped": "not an active enrollment"})
            continue
        if not enr.get("thread_id") or not enr.get("account_id"):
            checked.append({"email": email, "skipped": "no email sent yet (no thread_id/account_id)"})
            continue

        account = models.get_account(enr["account_id"])
        if not account:
            checked.append({"email": email, "skipped": "sending account no longer exists"})
            continue

        try:
            access_token, refreshed = gmail.get_valid_access_token(account)
            if refreshed:
                account.update(refreshed)
                models.save_account(account)
            thread = gmail.get_thread(access_token, enr["thread_id"])
            has_reply = _thread_has_reply_from_contact(thread, email, account["email"])
            checked.append(
                {
                    "email": email,
                    "thread_id": enr["thread_id"],
                    "message_count_in_thread": len(thread.get("messages", [])),
                    "reply_detected": has_reply,
                }
            )
        except Exception as exc:
            checked.append({"email": email, "error_type": type(exc).__name__, "error": str(exc)[:300]})

    from _views.cron import _run_poll_replies

    run_result = _run_poll_replies()

    self._send_json(200, {"diagnostic": checked, "run_result": run_result})
