"""Deliverability helpers: gradual daily-limit ramp-up for newly connected
accounts (this module will grow to cover domain blocklist checks and bounce
detection too)."""
import os
from datetime import datetime, timezone


def warmup_enabled():
    return os.environ.get("WARMUP_ENABLED", "true").strip().lower() not in ("false", "0", "")


def warmup_start_limit():
    return max(int(os.environ.get("WARMUP_START_LIMIT", "10")), 1)


def warmup_days():
    return max(int(os.environ.get("WARMUP_DAYS", "14")), 1)


def effective_daily_limit(account, now=None):
    """The daily send cap an account should actually be held to right now:
    ramps linearly from warmup_start_limit() up to the account's configured
    daily_limit over warmup_days() days since it was connected, so a newly
    connected mailbox doesn't start sending at full volume on day one."""
    configured = max(int(account.get("daily_limit", 0)), 0)
    if not warmup_enabled():
        return configured

    connected_at = account.get("connected_at")
    if not connected_at:
        return configured

    start_limit = warmup_start_limit()
    if configured <= start_limit:
        return configured

    try:
        connected_dt = datetime.fromisoformat(connected_at)
    except (TypeError, ValueError):
        return configured
    if connected_dt.tzinfo is None:
        connected_dt = connected_dt.replace(tzinfo=timezone.utc)

    now = now or datetime.now(timezone.utc)
    total_days = warmup_days()
    days_since = max((now - connected_dt).total_seconds() / 86400, 0)
    if days_since >= total_days:
        return configured

    fraction = days_since / total_days
    ramped = start_limit + (configured - start_limit) * fraction
    return max(start_limit, min(configured, round(ramped)))
