"""Enrollment + send-queue business logic shared by the HubSpot sync cron
and the send cron.
"""
import os

from . import models
from .redis_client import get_redis
from .utils import new_id, next_send_time, now_utc

QUEUE_KEY = "queue:pending"
ACTIVE_SET_KEY = "active_enrollments"


def _queue_member(sequence_id, email):
    return f"{sequence_id}|{email.strip().lower()}"


def enroll_contact(sequence_id, contact, source="hubspot"):
    """contact: dict with `email`, `hubspot_id`, and `properties` (a flat
    dict keyed by HubSpot's own property names, e.g. "firstname" - matching
    whatever merge tags the sequence's steps reference).
    Returns the enrollment dict, or None if the contact was already enrolled
    in this sequence before (dedup).
    """
    email = contact["email"].strip().lower()

    if models.has_been_enrolled(email, sequence_id):
        return None

    existing = models.get_enrollment(email)
    if existing and existing.get("status") == "active":
        # Contact is already actively moving through a (possibly different)
        # sequence; don't double-enroll them concurrently.
        return None

    send_at = next_send_time(after_days=0)
    enrollment = {
        "id": new_id("enr_"),
        "email": email,
        "contact": {
            "email": email,
            "hubspot_id": contact.get("hubspot_id"),
            "properties": contact.get("properties", {}),
        },
        "sequence_id": sequence_id,
        "step_index": 0,
        "status": "active",
        "source": source,
        "thread_id": None,
        "last_message_id": None,
        "account_id": None,
        "enrolled_at": now_utc().isoformat(),
        "next_send_at": send_at,
        "updated_at": now_utc().isoformat(),
    }
    models.save_enrollment(enrollment)
    models.mark_enrolled_dedup(email, sequence_id)
    models.incr_stat("stats:enrolled_total")
    models.incr_stat("stats:active_enrollments")

    r = get_redis()
    r.zadd(QUEUE_KEY, {_queue_member(sequence_id, email): send_at})
    r.sadd(ACTIVE_SET_KEY, email)

    models.append_log(
        sequence_id,
        {
            "type": "enrolled",
            "email": email,
            "at": now_utc().isoformat(),
        },
    )
    return enrollment


def dequeue(sequence_id, email):
    r = get_redis()
    r.zrem(QUEUE_KEY, _queue_member(sequence_id, email))


def requeue(sequence_id, email, send_at):
    r = get_redis()
    r.zadd(QUEUE_KEY, {_queue_member(sequence_id, email): send_at})


def due_members(limit, before_ts=None):
    """Returns up to `limit` (sequence_id, email, score) tuples due at or
    before `before_ts` (default: now), oldest first."""
    r = get_redis()
    before_ts = before_ts if before_ts is not None else now_utc().timestamp()
    raw = r.zrangebyscore(QUEUE_KEY, "-inf", before_ts, withscores=True, offset=0, count=limit)
    out = []
    for member, score in raw:
        sequence_id, email = member.split("|", 1)
        out.append((sequence_id, email, score))
    return out


def advance_or_complete(enrollment, sequence):
    """Call after successfully sending the current step. Moves the
    enrollment to the next step (and re-queues it) or marks it completed."""
    next_index = enrollment["step_index"] + 1
    steps = sequence.get("steps", [])

    if next_index >= len(steps):
        enrollment["status"] = "completed"
        enrollment["updated_at"] = now_utc().isoformat()
        models.save_enrollment(enrollment)
        dequeue(enrollment["sequence_id"], enrollment["email"])
        get_redis().srem(ACTIVE_SET_KEY, enrollment["email"])
        models.incr_stat("stats:active_enrollments", -1)
        models.append_log(
            enrollment["sequence_id"],
            {"type": "completed", "email": enrollment["email"], "at": now_utc().isoformat()},
        )
        return

    delay_days = max(int(steps[next_index].get("delay_days", 1)), 0)
    send_at = next_send_time(after_days=delay_days)
    enrollment["step_index"] = next_index
    enrollment["next_send_at"] = send_at
    enrollment["updated_at"] = now_utc().isoformat()
    models.save_enrollment(enrollment)
    requeue(enrollment["sequence_id"], enrollment["email"], send_at)


def stop_sequence(enrollment, status, reason=None):
    was_active = enrollment.get("status") == "active"
    enrollment["status"] = status
    enrollment["updated_at"] = now_utc().isoformat()
    models.save_enrollment(enrollment)
    dequeue(enrollment["sequence_id"], enrollment["email"])
    get_redis().srem(ACTIVE_SET_KEY, enrollment["email"])
    if was_active:
        models.incr_stat("stats:active_enrollments", -1)
    models.append_log(
        enrollment["sequence_id"],
        {
            "type": status,
            "email": enrollment["email"],
            "reason": reason,
            "at": now_utc().isoformat(),
        },
    )


def list_active_emails():
    r = get_redis()
    return list(r.smembers(ACTIVE_SET_KEY) or [])


def daily_cap():
    return int(os.environ.get("DAILY_SEND_CAP", "500"))


def pick_account(accounts, remaining_by_account):
    """Pick the connected account with the most remaining daily capacity."""
    best = None
    best_remaining = -1
    for acc in accounts:
        if acc.get("status") != "connected":
            continue
        remaining = remaining_by_account.get(acc["id"], 0)
        if remaining > best_remaining:
            best = acc
            best_remaining = remaining
    if best and best_remaining > 0:
        return best
    return None
