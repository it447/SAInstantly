from _lib.auth import require_auth


def login(self):
    """There's no server-side session to create: the frontend sends the
    just-typed password as X-Auth-Token to test it here, and on success
    saves that same value to use as the header on every future request."""
    if not require_auth(self):
        return
    self._send_json(200, {"ok": True})


def logout(self):
    # Nothing server-side to invalidate - the frontend just clears its
    # stored token. Kept as a no-op endpoint in case anything still calls it.
    self._send_json(200, {"ok": True})


def status(self):
    if not require_auth(self):
        return
    self._send_json(200, {"authenticated": True})
