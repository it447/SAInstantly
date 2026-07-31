import json

from .redis_client import get_redis
from .utils import today_str_local

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


def record_reply_stat():
    incr_stat(f"stats:replies:{today_str_local()}")
    incr_stat("stats:replies_total")


def record_unsubscribe_stat():
    incr_stat(f"stats:unsubscribes:{today_str_local()}")
    incr_stat("stats:unsubscribes_total")


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
