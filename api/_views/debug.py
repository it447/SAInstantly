"""Temporary diagnostic endpoints. Safe to remove once the app is stable --
none of these expose secret values, only connectivity/config status."""
import os

from _lib.redis_client import get_redis


def redis_check(self):
    result = {
        "upstash_url_is_set": bool(os.environ.get("UPSTASH_REDIS_REST_URL")),
        "upstash_token_is_set": bool(os.environ.get("UPSTASH_REDIS_REST_TOKEN")),
    }
    try:
        r = get_redis()
        r.set("debug:ping", "pong", ex=30)
        result["ok"] = r.get("debug:ping") == "pong"
    except Exception as exc:
        result["ok"] = False
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:500]
    self._send_json(200, result)
