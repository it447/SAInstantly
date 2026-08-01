"""Deliverability helpers: gradual daily-limit ramp-up for newly connected
accounts, and domain blocklist checks (this module will grow to cover
bounce detection too)."""
import os
import socket
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


# --------------------------------------------------------- domain blocklists

# Domain-reputation blocklists (not IP blocklists) - sending goes out through
# Gmail's own shared IPs, which this app has no control over and which
# checking would say nothing about this specific domain's reputation. These
# zones instead flag a *domain* that's been reported for spam/malware, e.g.
# because a compromised account or bad actor sent spam using it.
#
# Deliberately excludes Spamhaus DBL/ZEN: verified by hand that they silently
# return NXDOMAIN ("not listed") for queries from public/shared DNS
# resolvers (confirmed against their own documented always-listed test
# entries, e.g. dbltest.com) - a documented Spamhaus anti-abuse policy aimed
# at exactly the kind of resolver a serverless platform like Vercel uses.
# Shipping that zone would silently show "Clean" regardless of the real
# status, which is worse than not checking at all. SURBL and URIBL were
# verified working correctly against their own test entries from this same
# environment, so only those two are used.
DNSBL_ZONES = [
    ("SURBL", "multi.surbl.org"),
    ("URIBL", "multi.uribl.com"),
]


def check_domain_blocklists(domain, timeout_seconds=5):
    """Looks up `domain` against each zone in DNSBL_ZONES via a plain DNS A
    lookup (the standard way these lists work: query "<domain>.<zone>" -
    an A record response means listed, NXDOMAIN means clean). No API key or
    extra dependency needed - socket.gethostbyname does a real DNS lookup.
    Returns a list of {"list": name, "listed": True/False/None, ["error"]}."""
    domain = (domain or "").strip().lower()
    if not domain:
        return []

    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout_seconds)
    try:
        results = []
        for name, zone in DNSBL_ZONES:
            query = f"{domain}.{zone}"
            try:
                socket.gethostbyname(query)
                results.append({"list": name, "listed": True})
            except socket.gaierror:
                results.append({"list": name, "listed": False})
            except OSError as exc:
                results.append({"list": name, "listed": None, "error": str(exc)[:200]})
        return results
    finally:
        socket.setdefaulttimeout(old_timeout)
