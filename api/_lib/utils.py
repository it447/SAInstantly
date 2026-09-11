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


# A tag can optionally carry a fallback after a pipe - {{firstname|there}} -
# used verbatim when the contact's property is missing or blank, instead of
# rendering as an empty string.
MERGE_TAG_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*(?:\|([^}]*))?\}\}")


def render_merge_tags(text, contact):
    if not text:
        return text

    def _sub(match):
        key, default = match.group(1), match.group(2)
        value = contact.get(key)
        if value not in (None, ""):
            return value
        return default if default is not None else ""

    return MERGE_TAG_RE.sub(_sub, text)


def merge_tags_in(text):
    return {name for name, _default in MERGE_TAG_RE.findall(text or "")}


# Emails stay plain text on purpose (see README - links/HTML are a deliverability
# trade-off this tool avoids), so a link can't hide its URL under different
# display text the way a real <a href> would. [text](url) instead renders as
# "text (url)" - a real, clickable URL in any mail client, always shown next to
# the text describing it rather than disguised.
LINK_RE = re.compile(r"\[([^\[\]]+)\]\((https?://[^\s()]+)\)")


def render_links(text):
    if not text:
        return text
    return LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)


# Plain-text bold/italic/underline: **bold**, *italic*, __underline__ render as
# real Unicode "styled" characters (the Mathematical Alphanumeric Symbols
# block, plus a combining underline mark) rather than markup - there's no
# formatting layer in a plain-text email, so this is the only way bold/italic/
# underline can show up as anything other than literal asterisks. Only covers
# basic Latin letters and digits; accented letters, emoji, and non-Latin
# scripts pass through unstyled instead of breaking. A URL is never restyled,
# so a bolded or italicized link is still the exact, working address it
# started as.
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
ITALIC_RE = re.compile(r"\*([^*]+)\*")
UNDERLINE_RE = re.compile(r"__([^_]+)__")
URL_RE = re.compile(r"https?://\S+")


def _bold_char(c):
    if "A" <= c <= "Z":
        return chr(0x1D400 + (ord(c) - ord("A")))
    if "a" <= c <= "z":
        return chr(0x1D41A + (ord(c) - ord("a")))
    if "0" <= c <= "9":
        return chr(0x1D7CE + (ord(c) - ord("0")))
    return c


def _italic_char(c):
    if c == "h":
        return "ℎ"  # the italic block has no lowercase h; this is its standard stand-in
    if "A" <= c <= "Z":
        return chr(0x1D434 + (ord(c) - ord("A")))
    if "a" <= c <= "z":
        return chr(0x1D44E + (ord(c) - ord("a")))
    return c  # no italic variant exists for digits/punctuation


def _underline_char(c):
    return c + "̲"  # combining low line, drawn under the preceding character


def _map_styled(text, char_fn):
    parts = []
    last = 0
    for m in URL_RE.finditer(text):
        parts.append("".join(char_fn(c) for c in text[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append("".join(char_fn(c) for c in text[last:]))
    return "".join(parts)


def render_text_styles(text):
    if not text:
        return text
    text = BOLD_RE.sub(lambda m: _map_styled(m.group(1), _bold_char), text)
    text = ITALIC_RE.sub(lambda m: _map_styled(m.group(1), _italic_char), text)
    text = UNDERLINE_RE.sub(lambda m: _map_styled(m.group(1), _underline_char), text)
    return text


def sequence_merge_tag_properties(sequence):
    """Every {{property}} referenced across a sequence's steps, plus `email`
    (always needed for dedup/sending/threading)."""
    properties = {"email"}
    for step in sequence.get("steps", []):
        properties |= merge_tags_in(step.get("subject", ""))
        properties |= merge_tags_in(step.get("body", ""))
    return properties


