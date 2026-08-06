import json

from .redis_client import get_redis
from .utils import now_utc, today_str_local

# ---------------------------------------------------------------- sequences

def list_sequences():
    r = get_redis()
    raw = r.hgetall("sequences") or {}
    return [json.loads(v) for v in raw.values()]


def get_sequence(sequence_id):
    r = get_redis()
    raw = r.hget("sequences", sequence_id)
    return json.loads(raw) if raw else None


def save_sequence(sequence):
    r = get_redis()
    r.hset("sequences", sequence["id"], json.dumps(sequence))


def delete_sequence(sequence_id):
    """Sequences are never hard-deleted; mark archived so historical
    enrollments/logs still resolve correctly."""
    seq = get_sequence(sequence_id)
    if not seq:
        return None
    seq["archived"] = True
    save_sequence(seq)
    return seq


# ----------------------------------------------------------------- accounts

def list_accounts():
    r = get_redis()
    raw = r.hgetall("accounts") or {}
    return [json.loads(v) for v in raw.values()]


def get_account(account_id):
    r = get_redis()
    raw = r.hget("accounts", account_id)
    return json.loads(raw) if raw else None


def save_account(account):
    r = get_redis()
    r.hset("accounts", account["id"], json.dumps(account))


def delete_account(account_id):
    """Never used for tokens with active sends in flight; disconnect just
    flips status so history/logs referencing this account_id still make
    sense."""
    account = get_account(account_id)
    if not account:
        return None
    account["status"] = "disconnected"
    save_account(account)
    return account


# -------------------------------------------------------------- enrollments

def enrollment_key(email):
    return f"enrollments:{email.strip().lower()}"


def get_enrollment(email):
    r = get_redis()
    raw = r.get(enrollment_key(email))
    return json.loads(raw) if raw else None


def save_enrollment(enrollment):
    r = get_redis()
    r.set(enrollment_key(enrollment["email"]), json.dumps(enrollment))


def has_been_enrolled(email, sequence_id):
    r = get_redis()
    return r.get(f"sent:{email.strip().lower()}:{sequence_id}") is not None


def mark_enrolled_dedup(email, sequence_id):
    r = get_redis()
    r.set(f"sent:{email.strip().lower()}:{sequence_id}", "1")


def sequence_contacts_key(sequence_id):
    return f"sequence_contacts:{sequence_id}"


def add_sequence_contact(sequence_id, email):
    """Indexes which emails have ever been enrolled in this sequence, so the
    per-sequence contacts view can list them even after they've completed,
    replied, or unsubscribed (the enrollment record itself is keyed by email
    only, so this is the only way to look them up by sequence)."""
    r = get_redis()
    r.sadd(sequence_contacts_key(sequence_id), email.strip().lower())


def list_enrollments_for_sequence(sequence_id):
    r = get_redis()
    emails = r.smembers(sequence_contacts_key(sequence_id)) or []
    return [e for e in (get_enrollment(email) for email in emails) if e]


# --------------------------------------------------------------------- logs

def append_log(sequence_id, entry):
    r = get_redis()
    entry_json = json.dumps(entry)
    r.lpush(f"logs:{sequence_id}", entry_json)
    r.ltrim(f"logs:{sequence_id}", 0, 999)


def get_logs(sequence_id, limit=100):
    r = get_redis()
    raw = r.lrange(f"logs:{sequence_id}", 0, limit - 1) or []
    return [json.loads(v) for v in raw]


# ------------------------------------------------------------- suppression

def is_suppressed(email):
    r = get_redis()
    raw = r.hget("suppression_list", email.strip().lower())
    if not raw:
        return False
    return json.loads(raw).get("active", True)


def add_to_suppression_list(email, reason, source=None):
    """Adds an email to the global, cross-sequence suppression list - checked
    before enrolling a contact into ANY sequence, so someone who unsubscribed
    or hard-bounced on one campaign never gets re-enrolled by a later list
    upload into a different one. Entries are never hard-deleted (see
    remove_from_suppression_list) so there's always a record of when/why
    someone was suppressed, even if a later manual action reverses it."""
    r = get_redis()
    email = email.strip().lower()
    existing_raw = r.hget("suppression_list", email)
    if existing_raw and json.loads(existing_raw).get("active", True):
        return  # already suppressed - keep the original record, not this duplicate event
    entry = {"reason": reason, "source": source, "added_at": now_utc().isoformat(), "active": True}
    r.hset("suppression_list", email, json.dumps(entry))
    append_suppression_audit({"email": email, "action": "added", "reason": reason, "source": source})


def remove_from_suppression_list(email):
    """Manually un-suppresses an email (e.g. an accidental add, or someone
    who explicitly asks to be re-contacted). Marks the record inactive
    rather than deleting it, preserving the history of when it was
    suppressed and why - the audit trail Joel flagged as the highest-risk
    surface to get right."""
    r = get_redis()
    email = email.strip().lower()
    raw = r.hget("suppression_list", email)
    if not raw:
        return False
    entry = json.loads(raw)
    if not entry.get("active", True):
        return False
    entry["active"] = False
    entry["removed_at"] = now_utc().isoformat()
    r.hset("suppression_list", email, json.dumps(entry))
    append_suppression_audit({"email": email, "action": "removed"})
    return True


def list_suppressed(active_only=True):
    r = get_redis()
    raw = r.hgetall("suppression_list") or {}
    out = []
    for email, value in raw.items():
        entry = json.loads(value)
        if active_only and not entry.get("active", True):
            continue
        entry["email"] = email
        out.append(entry)
    out.sort(key=lambda e: e.get("added_at") or "", reverse=True)
    return out


def append_suppression_audit(entry):
    r = get_redis()
    entry = dict(entry, at=now_utc().isoformat())
    r.lpush("logs:suppression", json.dumps(entry))
    r.ltrim("logs:suppression", 0, 999)


def get_suppression_audit(limit=100):
    r = get_redis()
    raw = r.lrange("logs:suppression", 0, limit - 1) or []
    return [json.loads(v) for v in raw]


# -------------------------------------------------------------------- stats

def incr_stat(key, amount=1):
    r = get_redis()
    r.incrby(key, amount)


def get_stat(key):
    r = get_redis()
    val = r.get(key)
    return int(val) if val else 0


def daily_sent_total(date_str=None):
    return get_stat(f"stats:sent:{date_str or today_str_local()}")


def daily_sent_for_account(account_id, date_str=None):
    return get_stat(f"stats:sent:{account_id}:{date_str or today_str_local()}")


def record_send_stat(account_id):
    date_str = today_str_local()
    incr_stat(f"stats:sent:{date_str}")
    incr_stat(f"stats:sent:{account_id}:{date_str}")
    incr_stat(f"stats:sent_total:{account_id}")


def record_reply_stat():
    incr_stat(f"stats:replies:{today_str_local()}")
    incr_stat("stats:replies_total")


def record_unsubscribe_stat():
    incr_stat(f"stats:unsubscribes:{today_str_local()}")
    incr_stat("stats:unsubscribes_total")


def record_bounce_stat(account_id=None):
    incr_stat(f"stats:bounces:{today_str_local()}")
    incr_stat("stats:bounces_total")
    if account_id:
        incr_stat(f"stats:bounces_total:{account_id}")


def bounce_message_seen(account_id, message_id):
    r = get_redis()
    return bool(r.sismember(f"bounce_seen:{account_id}", message_id))


def mark_bounce_message_seen(account_id, message_id):
    r = get_redis()
    r.sadd(f"bounce_seen:{account_id}", message_id)


# ------------------------------------------------------------------ hubspot

def get_hubspot_config():
    """Only holds list<->sequence mappings; the API key lives in the
    HUBSPOT_API_KEY environment variable, not Redis."""
    r = get_redis()
    raw = r.get("hubspot:config")
    config = json.loads(raw) if raw else {"mappings": []}
    config.pop("api_key", None)  # drop any key saved by an older version of this UI
    return config


def save_hubspot_config(config):
    r = get_redis()
    r.set("hubspot:config", json.dumps(config))


def hubspot_contact_seen(list_id, email):
    r = get_redis()
    return bool(r.sismember(f"hubspot:seen:{list_id}", email.strip().lower()))


def mark_hubspot_contact_seen(list_id, email):
    r = get_redis()
    r.sadd(f"hubspot:seen:{list_id}", email.strip().lower())


# ------------------------------------------------------------ deliverability

BLOCKLIST_CACHE_TTL_SECONDS = 24 * 3600


def get_cached_blocklist_check(domain):
    r = get_redis()
    raw = r.get(f"blocklist_check:{domain.strip().lower()}")
    return json.loads(raw) if raw else None


def save_blocklist_check(domain, results, checked_at):
    r = get_redis()
    payload = json.dumps({"results": results, "checked_at": checked_at})
    r.set(f"blocklist_check:{domain.strip().lower()}", payload, ex=BLOCKLIST_CACHE_TTL_SECONDS)


def get_cached_domain_auth(domain):
    r = get_redis()
    raw = r.get(f"domain_auth_check:{domain.strip().lower()}")
    return json.loads(raw) if raw else None


def save_domain_auth_check(domain, result, checked_at):
    r = get_redis()
    payload = json.dumps({"result": result, "checked_at": checked_at})
    r.set(f"domain_auth_check:{domain.strip().lower()}", payload, ex=BLOCKLIST_CACHE_TTL_SECONDS)
