import time

from _lib import deliverability, models
from _lib.auth import is_authenticated, require_auth
from _lib.gmail import build_auth_url, exchange_code, get_user_email
from _lib.redis_client import get_redis
from _lib.utils import is_protected_domain, new_id, now_utc, today_str_local

STATE_TTL_SECONDS = 600
DEFAULT_DAILY_LIMIT = 50


def connect(self):
    if not is_authenticated(self):
        self._redirect("/login.html")
        return

    state = new_id("oauth_")
    get_redis().set(f"oauth:state:{state}", "1", ex=STATE_TTL_SECONDS)
    self._redirect(build_auth_url(state))


def callback(self):
    query = self._query()
    error = query.get("error", [None])[0]
    if error:
        self._redirect(f"/accounts.html?error={error}")
        return

    code = query.get("code", [None])[0]
    state = query.get("state", [None])[0]

    if not code or not state:
        self._redirect("/accounts.html?error=missing_code")
        return

    r = get_redis()
    state_key = f"oauth:state:{state}"
    if not r.get(state_key):
        self._redirect("/accounts.html?error=invalid_state")
        return
    r.delete(state_key)

    try:
        tokens = exchange_code(code)
        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token")
        email = get_user_email(access_token)
    except Exception:
        self._redirect("/accounts.html?error=oauth_failed")
        return

    if is_protected_domain(email):
        # Never save tokens for a protected domain - not even long enough to
        # check for an existing refresh_token below.
        self._redirect("/accounts.html?error=protected_domain")
        return

    if not refresh_token:
        existing = next((a for a in models.list_accounts() if a.get("email") == email), None)
        if existing and existing.get("refresh_token"):
            refresh_token = existing["refresh_token"]
        else:
            self._redirect("/accounts.html?error=no_refresh_token")
            return

    existing = next((a for a in models.list_accounts() if a.get("email") == email), None)
    account_id = existing["id"] if existing else new_id("acct_")

    account = {
        "id": account_id,
        "email": email,
        "provider": "gmail",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expires_at": time.time() + tokens.get("expires_in", 3600),
        "daily_limit": existing.get("daily_limit", DEFAULT_DAILY_LIMIT) if existing else DEFAULT_DAILY_LIMIT,
        "status": "connected",
        "connected_at": existing.get("connected_at") if existing else now_utc().isoformat(),
        "updated_at": now_utc().isoformat(),
    }
    models.save_account(account)
    self._redirect("/accounts.html?connected=1")


def _public_account(account):
    date_str = today_str_local()
    daily_limit = account.get("daily_limit", 50)
    effective_limit = deliverability.effective_daily_limit(account)
    return {
        "id": account["id"],
        "email": account["email"],
        "provider": account.get("provider", "gmail"),
        "status": account.get("status", "connected"),
        "daily_limit": daily_limit,
        "effective_daily_limit": effective_limit,
        "warming_up": effective_limit < daily_limit,
        "sent_today": models.daily_sent_for_account(account["id"], date_str),
        "connected_at": account.get("connected_at"),
    }


def list_accounts(self):
    if not require_auth(self):
        return
    accounts = sorted(models.list_accounts(), key=lambda a: a.get("connected_at") or "")
    self._send_json(200, {"accounts": [_public_account(a) for a in accounts]})


def blocklist_status(self):
    """Domain-reputation blocklist status for every unique domain among
    connected accounts. Cached for 24h (checked_at is returned so the UI can
    show staleness); pass ?refresh=1 to force a fresh check."""
    if not require_auth(self):
        return

    force_refresh = self._query().get("refresh", ["0"])[0] == "1"
    domains = sorted({a["email"].split("@", 1)[1] for a in models.list_accounts() if a.get("status") == "connected" and "@" in a.get("email", "")})

    out = []
    for domain in domains:
        cached = None if force_refresh else models.get_cached_blocklist_check(domain)
        if cached is None:
            results = deliverability.check_domain_blocklists(domain)
            checked_at = now_utc().isoformat()
            models.save_blocklist_check(domain, results, checked_at)
        else:
            results = cached["results"]
            checked_at = cached["checked_at"]
        out.append({
            "domain": domain,
            "listed": any(r.get("listed") for r in results),
            "results": results,
            "checked_at": checked_at,
        })

    self._send_json(200, {"domains": out})


def update(self):
    if not require_auth(self):
        return

    body = self._read_json_body()
    account_id = body.get("id")
    account = models.get_account(account_id) if account_id else None
    if not account:
        self._send_json(404, {"error": "account not found"})
        return

    if "daily_limit" in body:
        try:
            account["daily_limit"] = max(int(body["daily_limit"]), 0)
        except (TypeError, ValueError):
            self._send_json(400, {"error": "daily_limit must be a number"})
            return

    account["updated_at"] = now_utc().isoformat()
    models.save_account(account)
    self._send_json(200, {"ok": True})


def disconnect(self):
    if not require_auth(self):
        return

    body = self._read_json_body()
    account_id = body.get("id")
    account = models.delete_account(account_id) if account_id else None
    if not account:
        self._send_json(404, {"error": "account not found"})
        return
    self._send_json(200, {"ok": True})
