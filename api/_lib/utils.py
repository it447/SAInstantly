import hashlib
import hmac
import json
import os
import random
import re
import uuid
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

SEND_TZ_NAME = os.environ.get("SEND_TIMEZONE", "America/New_York")

# Domains that must never be connected as a sending account in this tool
# (e.g. the company's primary domain, kept separate from cold outreach to
# protect its deliverability/reputation). Configurable via PROTECTED_DOMAINS
# (comma-separated), with a hard-coded default so protection holds even if
# that env var is never set.
DEFAULT_PROTECTED_DOMAINS = ["scalearmy.com"]


def protected_domains():
    configured = os.environ.get("PROTECTED_DOMAINS", "")
    domains = [d.strip().lower() for d in configured.split(",") if d.strip()]
    return domains or DEFAULT_PROTECTED_DOMAINS


def is_protected_domain(email):
    email = (email or "").strip().lower()
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1]
    return any(domain == d or domain.endswith(f".{d}") for d in protected_domains())


def send_tz():
    if ZoneInfo is None:
        return timezone.utc
    return ZoneInfo(SEND_TZ_NAME)


def now_utc():
    return datetime.now(timezone.utc)


def now_local():
    return now_utc().astimezone(send_tz())


def today_str_local():
    return now_local().strftime("%Y-%m-%d")


def new_id(prefix=""):
    suffix = uuid.uuid4().hex[:12]
    return f"{prefix}{suffix}" if prefix else suffix


def json_body(request_body):
    if not request_body:
        return {}
    if isinstance(request_body, (bytes, bytearray)):
        request_body = request_body.decode("utf-8")
    if not request_body:
        return {}
    return json.loads(request_body)


def send_window_hours():
    start = int(os.environ.get("SEND_WINDOW_START_HOUR", "8"))
    end = int(os.environ.get("SEND_WINDOW_END_HOUR", "18"))
    return start, end


def next_send_time(after_days=0):
    """Pick a randomized send timestamp within the 8am-6pm local window,
    `after_days` days from now (0 = today if still inside/before the window,
    otherwise the next available day). Returns a unix timestamp (UTC).
    """
    start_hour, end_hour = send_window_hours()
    local_now = now_local()
    target_date = (local_now + timedelta(days=after_days)).date()

    window_start = datetime.combine(target_date, datetime.min.time(), tzinfo=send_tz()).replace(hour=start_hour)
    window_end = datetime.combine(target_date, datetime.min.time(), tzinfo=send_tz()).replace(hour=end_hour)

    if after_days == 0 and local_now > window_start:
        # today's window may already be underway or over
        if local_now >= window_end:
            # push to the next day's window
            target_date = target_date + timedelta(days=1)
            window_start = datetime.combine(target_date, datetime.min.time(), tzinfo=send_tz()).replace(hour=start_hour)
            window_end = datetime.combine(target_date, datetime.min.time(), tzinfo=send_tz()).replace(hour=end_hour)
        else:
            window_start = local_now

    span_seconds = int((window_end - window_start).total_seconds())
    if span_seconds <= 0:
        span_seconds = 60
    offset = random.randint(0, span_seconds)
    target = window_start + timedelta(seconds=offset)
    return int(target.astimezone(timezone.utc).timestamp())


def next_send_time_soon(min_delay_seconds=60, max_delay_seconds=600):
    """Like next_send_time, but for a contact's very first email: send
    within a few minutes of enrollment (default 1-10 min, comfortably under
    a 30-minute target once combined with the 15-minute cron cadence)
    instead of at a random point anywhere in the rest of the day. Still
    respects the 8am-6pm window - if enrollment happens outside it, the
    short random offset is applied from the next window's start instead.
    """
    start_hour, end_hour = send_window_hours()
    local_now = now_local()
    offset = timedelta(seconds=random.randint(min_delay_seconds, max_delay_seconds))

    window_start_today = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=send_tz()).replace(hour=start_hour)
    window_end_today = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=send_tz()).replace(hour=end_hour)

    if local_now < window_start_today:
        target = window_start_today + offset
    elif local_now >= window_end_today:
        tomorrow = local_now.date() + timedelta(days=1)
        target = datetime.combine(tomorrow, datetime.min.time(), tzinfo=send_tz()).replace(hour=start_hour) + offset
    else:
        candidate = local_now + offset
        # Don't let the short offset push past today's close - if enrollment
        # happens right near the end of the window, send just before close
        # instead of waiting for tomorrow's window (respects "within 30 min").
        target = candidate if candidate < window_end_today else window_end_today - timedelta(seconds=30)

    return int(target.astimezone(timezone.utc).timestamp())


MERGE_TAG_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render_merge_tags(text, contact):
    if not text:
        return text

    def _sub(match):
        key = match.group(1)
        value = contact.get(key)
        return value if value not in (None, "") else ""

    return MERGE_TAG_RE.sub(_sub, text)


def merge_tags_in(text):
    return set(MERGE_TAG_RE.findall(text or ""))


def sequence_merge_tag_properties(sequence):
    """Every {{property}} referenced across a sequence's steps, plus `email`
    (always needed for dedup/sending/threading)."""
    properties = {"email"}
    for step in sequence.get("steps", []):
        properties |= merge_tags_in(step.get("subject", ""))
        properties |= merge_tags_in(step.get("body", ""))
    return properties


def unsubscribe_token(email, sequence_id):
    secret = os.environ.get("UNSUBSCRIBE_SECRET", "").encode("utf-8")
    payload = f"{email}:{sequence_id}".encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()[:32]


def verify_unsubscribe_token(email, sequence_id, token):
    expected = unsubscribe_token(email, sequence_id)
    return hmac.compare_digest(expected, token or "")


def unsubscribe_link(email, sequence_id):
    base = os.environ.get("APP_BASE_URL", "").rstrip("/")
    token = unsubscribe_token(email, sequence_id)
    from urllib.parse import quote

    return f"{base}/api/unsubscribe?e={quote(email)}&s={quote(sequence_id)}&t={token}"
