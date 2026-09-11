import html
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


# Every email is sent as multipart/alternative: a real HTML part (real
# <strong>/<em>/<u>/<a> tags) plus a plain-text fallback in the same message -
# not HTML plus tracking. The actual deliverability risk this tool avoids is
# specifically open/click tracking (a tracking pixel, or a link rewritten
# through a redirect domain) - see README - not HTML formatting itself, which
# every legitimate business email uses with no penalty. [text](url) and
# **bold**/*italic*/__underline__ are the authoring syntax; render_html() and
# render_plain() below both read the same syntax so they never disagree about
# what counts as a marker.
LINK_RE = re.compile(r"\[([^\[\]]+)\]\((https?://[^\s()]+)\)")

_TAG_HTML = {"b": "strong", "i": "em", "u": "u"}


def _tokenize_markup(text):
    """Walks `text` once, yielding ('text', str) for literal spans,
    ('link', label, url) for [label](url), and ('toggle', 'b'|'i'|'u') for a
    **/*/__ marker - a single shared pass so the plain-text and HTML
    renderers can never disagree about what's a marker versus a stray
    character. Markers simply toggle on/off in the order they appear, so
    proper nesting (**bold *and italic* still bold**) round-trips correctly;
    only genuinely overlapping (not nested) markers are on the user to avoid.
    """
    i, n = 0, len(text)
    buf = []
    while i < n:
        m = LINK_RE.match(text, i)
        if m:
            if buf:
                yield ("text", "".join(buf))
                buf = []
            yield ("link", m.group(1), m.group(2))
            i = m.end()
            continue
        if text.startswith("**", i):
            if buf:
                yield ("text", "".join(buf))
                buf = []
            yield ("toggle", "b")
            i += 2
            continue
        if text.startswith("__", i):
            if buf:
                yield ("text", "".join(buf))
                buf = []
            yield ("toggle", "u")
            i += 2
            continue
        if text[i] == "*":
            if buf:
                yield ("text", "".join(buf))
                buf = []
            yield ("toggle", "i")
            i += 1
            continue
        buf.append(text[i])
        i += 1
    if buf:
        yield ("text", "".join(buf))


def render_plain(text):
    """The plain-text alternative part: markers are simply removed (no fake
    styling), and a link renders as "label (url)" - a real, clickable URL,
    just always shown next to its label instead of hidden."""
    if not text:
        return text
    out = []
    for tok in _tokenize_markup(text):
        if tok[0] == "text":
            out.append(tok[1])
        elif tok[0] == "link":
            out.append(f"{tok[1]} ({tok[2]})")
        # ("toggle", ...) contributes nothing in plain text
    return "".join(out)


def render_html(text):
    """The real HTML part: **/*/__ become <strong>/<em>/<u>, [label](url)
    becomes a real <a href>, and newlines become <br> since HTML otherwise
    collapses them. All literal text is escaped."""
    if not text:
        return text
    out = []
    open_tags = []
    for tok in _tokenize_markup(text):
        if tok[0] == "text":
            out.append(html.escape(tok[1]).replace("\n", "<br>\n"))
        elif tok[0] == "link":
            out.append(f'<a href="{html.escape(tok[2])}">{html.escape(tok[1])}</a>')
        elif tok[0] == "toggle":
            tag = tok[1]
            if tag in open_tags:
                out.append(f"</{_TAG_HTML[tag]}>")
                open_tags.remove(tag)
            else:
                out.append(f"<{_TAG_HTML[tag]}>")
                open_tags.append(tag)
    for tag in reversed(open_tags):
        # An odd number of markers (user forgot to close one) shouldn't leave
        # invalid, unclosed HTML - close whatever's still open at the end.
        out.append(f"</{_TAG_HTML[tag]}>")
    return "".join(out)


def sequence_merge_tag_properties(sequence):
    """Every {{property}} referenced across a sequence's steps, plus `email`
    (always needed for dedup/sending/threading)."""
    properties = {"email"}
    for step in sequence.get("steps", []):
        properties |= merge_tags_in(step.get("subject", ""))
        properties |= merge_tags_in(step.get("body", ""))
    return properties


