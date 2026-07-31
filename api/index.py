"""Single Vercel Python entrypoint for the whole API.

Vercel's Python runtime supports exactly one recognized entrypoint per
project (it errors out if it finds more than one file exporting a
`handler`/`app`, and even a single file needs its module path declared in
pyproject.toml's `[tool.vercel] entrypoint`), so every route is dispatched
from here rather than from one file per endpoint. vercel.json rewrites all
`/api/*` requests to this file via a `:path*` wildcard; since the
destination doesn't reference that wildcard inline, Vercel appends it as a
`path` query parameter instead of preserving it in the URL path (e.g. a
request for `/api/sequences/save` arrives here as `self.path ==
"/api/index?path=sequences%2Fsave"`), so the real route is reconstructed
from that query param below rather than from the URL path directly.
"""
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib.http import BaseHandler
from _views import accounts, auth, cron, dashboard, debug, hubspot, sequences

ROUTES = {
    ("GET", "/api/debug/redis_check"): debug.redis_check,
    ("GET", "/api/debug/queue_check"): debug.queue_check,
    ("GET", "/api/auth/status"): auth.status,
    ("GET", "/api/auth/login"): auth.login,
    ("POST", "/api/auth/logout"): auth.logout,
    ("GET", "/api/accounts/connect"): accounts.connect,
    ("GET", "/api/accounts/callback"): accounts.callback,
    ("GET", "/api/accounts/list"): accounts.list_accounts,
    ("POST", "/api/accounts/update"): accounts.update,
    ("POST", "/api/accounts/disconnect"): accounts.disconnect,
    ("GET", "/api/sequences/list"): sequences.list_sequences,
    ("POST", "/api/sequences/save"): sequences.save,
    ("POST", "/api/sequences/delete"): sequences.delete,
    ("GET", "/api/sequences/logs"): sequences.logs,
    ("GET", "/api/sequences/detail"): sequences.detail,
    ("GET", "/api/hubspot/config"): hubspot.config,
    ("GET", "/api/hubspot/properties"): hubspot.properties,
    ("GET", "/api/hubspot/lists"): hubspot.lists,
    ("POST", "/api/hubspot/add_mapping"): hubspot.add_mapping,
    ("POST", "/api/hubspot/remove_mapping"): hubspot.remove_mapping,
    ("GET", "/api/cron/hubspot_sync"): cron.hubspot_sync,
    ("POST", "/api/cron/hubspot_sync"): cron.hubspot_sync,
    ("GET", "/api/cron/send"): cron.send,
    ("POST", "/api/cron/send"): cron.send,
    ("GET", "/api/cron/poll_replies"): cron.poll_replies,
    ("POST", "/api/cron/poll_replies"): cron.poll_replies,
    ("GET", "/api/dashboard/stats"): dashboard.stats,
}


class handler(BaseHandler):
    def _dispatch(self, method):
        parsed = urlparse(self.path)
        wildcard = parse_qs(parsed.query).get("path", [None])[0]
        if wildcard is not None:
            path = f"/api/{wildcard}".rstrip("/") or "/"
        else:
            path = parsed.path.rstrip("/") or "/"

        view = ROUTES.get((method, path))
        if view is None:
            self._send_json(404, {"error": "not found", "method": method, "path": path, "raw_path": self.path})
            return
        view(self)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")
