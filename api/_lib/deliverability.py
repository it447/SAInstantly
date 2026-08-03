"""Deliverability helpers: gradual daily-limit ramp-up for newly connected
accounts, domain blocklist checks, SPF/DKIM/DMARC presence checks, and the
composite per-account health score built from all of it."""
import os
import re
import socket
from datetime import datetime, timezone

import dns.resolver


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


# --------------------------------------------------------------- SPF/DKIM/DMARC

DKIM_SELECTOR = "google"  # Google Workspace's default selector name


def _txt_records(name, timeout_seconds=6, tries=2):
    """Returns a list of TXT record strings, [] if the name genuinely has no
    such record (NXDOMAIN), or None if the lookup itself failed/timed out
    (meaning "couldn't determine" - not the same as "confirmed absent")."""
    for _ in range(tries):
        try:
            answers = dns.resolver.resolve(name, "TXT", lifetime=timeout_seconds)
            return ["".join(part.decode() if isinstance(part, bytes) else part for part in rec.strings) for rec in answers]
        except dns.resolver.NXDOMAIN:
            return []
        except Exception:
            continue
    return None


def _dkim_key_present(txt_value):
    """A DKIM TXT record with an empty p= value (e.g. "v=DKIM1; p=") means the
    key was explicitly revoked/retired, not that DKIM is actively working -
    confirmed by hand against example.com, which publishes exactly this as a
    placeholder. Only a non-empty p= counts as configured."""
    match = re.search(r"p=([^;]*)", txt_value, re.IGNORECASE)
    return bool(match and match.group(1).strip())


def check_domain_auth(domain):
    """Best-effort SPF/DKIM/DMARC presence check via real DNS TXT lookups
    (dnspython - plain socket only supports A records). DKIM only checks
    Google Workspace's default "google" selector, since a selector name
    isn't discoverable without knowing it - a custom selector would show as
    not-found even if DKIM is genuinely configured under a different name,
    so treat dkim=False as "couldn't confirm" rather than definitive proof
    it's missing."""
    domain = (domain or "").strip().lower()

    spf_records = _txt_records(domain)
    dkim_records = _txt_records(f"{DKIM_SELECTOR}._domainkey.{domain}")
    dmarc_records = _txt_records(f"_dmarc.{domain}")

    spf = spf_records is not None and any(r.lower().startswith("v=spf1") for r in spf_records)
    dkim = dkim_records is not None and any(
        "v=dkim1" in r.lower() and _dkim_key_present(r) for r in dkim_records
    )
    dmarc = dmarc_records is not None and any(r.lower().replace(" ", "").startswith("v=dmarc1") for r in dmarc_records)

    return {
        "spf": spf,
        "spf_checked": spf_records is not None,
        "dkim": dkim,
        "dkim_checked": dkim_records is not None,
        "dmarc": dmarc,
        "dmarc_checked": dmarc_records is not None,
    }


# ------------------------------------------------------------ health score

def account_health_score(domain_auth, domain_listed, bounce_rate, warming_up):
    """Composite 0-100 health score for one connected account, combining
    domain authentication (SPF/DKIM/DMARC), domain blocklist status, this
    account's own bounce rate, and warm-up progress. Deliberately
    transparent (not a black-box number) - `factors` lists exactly what
    contributed and why, each tagged ok=True/False/None (None = neutral/
    informational, not a pass or fail)."""
    score = 0
    factors = []

    if domain_auth.get("spf"):
        score += 15
        factors.append({"label": "SPF configured", "ok": True})
    else:
        factors.append({"label": "SPF record not found", "ok": False})

    if domain_auth.get("dkim"):
        score += 15
        factors.append({"label": "DKIM configured", "ok": True})
    else:
        factors.append({"label": "DKIM not found (only checks Google Workspace's default selector)", "ok": False})

    if domain_auth.get("dmarc"):
        score += 10
        factors.append({"label": "DMARC configured", "ok": True})
    else:
        factors.append({"label": "DMARC record not found", "ok": False})

    if not domain_listed:
        score += 20
        factors.append({"label": "Domain not on SURBL/URIBL", "ok": True})
    else:
        factors.append({"label": "Domain listed on a blocklist", "ok": False})

    if bounce_rate is None:
        score += 30
        factors.append({"label": "Not enough sends yet to measure bounce rate", "ok": None})
    elif bounce_rate < 0.02:
        score += 30
        factors.append({"label": f"Bounce rate {bounce_rate * 100:.1f}%", "ok": True})
    elif bounce_rate < 0.05:
        score += 15
        factors.append({"label": f"Bounce rate {bounce_rate * 100:.1f}% (elevated)", "ok": None})
    else:
        factors.append({"label": f"Bounce rate {bounce_rate * 100:.1f}% (high)", "ok": False})

    if not warming_up:
        score += 10
        factors.append({"label": "Fully ramped up", "ok": True})
    else:
        score += 5
        factors.append({"label": "Still in warm-up ramp", "ok": None})

    if score >= 80:
        grade = "Healthy"
    elif score >= 50:
        grade = "Needs attention"
    else:
        grade = "At risk"

    return {"score": score, "grade": grade, "factors": factors}
