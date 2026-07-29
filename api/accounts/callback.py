import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import models
from _lib.gmail import exchange_code, get_user_email
from _lib.http import BaseHandler
from _lib.redis_client import get_redis
from _lib.utils import new_id, now_utc

DEFAULT_DAILY_LIMIT = 50


class handler(BaseHandler):
    def do_GET(self):
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

        if not refresh_token:
            # Google only returns a refresh_token on the very first consent.
            # If this email was connected before and got disconnected, ask
            # the user to revoke access in their Google account and retry.
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
