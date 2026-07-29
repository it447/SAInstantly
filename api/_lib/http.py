"""Small helpers shared by every api/**/*.py Vercel Python function.

Vercel's Python runtime expects each endpoint file to export a class named
`handler` that extends BaseHTTPRequestHandler. These helpers cut down the
boilerplate around JSON responses, cookies, and request bodies.
"""
import json
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

SESSION_COOKIE_NAME = "sa_session"


class BaseHandler(BaseHTTPRequestHandler):
    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _query(self):
        return parse_qs(urlparse(self.path).query)

    def _cookies(self):
        cookie_header = self.headers.get("Cookie")
        jar = SimpleCookie()
        if cookie_header:
            jar.load(cookie_header)
        return {k: v.value for k, v in jar.items()}

    def _send_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status, html, extra_headers=None):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location, extra_headers=None):
        self.send_response(302)
        self.send_header("Location", location)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _set_cookie_header(self, name, value, max_age=None, path="/"):
        cookie = SimpleCookie()
        cookie[name] = value
        cookie[name]["path"] = path
        cookie[name]["httponly"] = True
        cookie[name]["samesite"] = "Lax"
        cookie[name]["secure"] = True
        if max_age is not None:
            cookie[name]["max-age"] = max_age
        # Strip the leading "Set-Cookie: " that SimpleCookie's output() adds.
        return cookie[name].OutputString()
