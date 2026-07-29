import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import enrollment, models
from _lib.http import BaseHandler
from _lib.utils import verify_unsubscribe_token

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Unsubscribed</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f5f5f4; color: #1c1c1c; display: flex; min-height: 100vh;
         align-items: center; justify-content: center; margin: 0; }}
  .card {{ background: #fff; padding: 2.5rem 3rem; border-radius: 12px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); max-width: 420px; text-align: center; }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .5rem; }}
  p {{ color: #555; line-height: 1.5; }}
</style>
</head>
<body>
  <div class="card">
    <h1>{heading}</h1>
    <p>{message}</p>
  </div>
</body>
</html>"""


class handler(BaseHandler):
    def do_GET(self):
        query = self._query()
        email = query.get("e", [None])[0]
        sequence_id = query.get("s", [None])[0]
        token = query.get("t", [None])[0]

        if not email or not sequence_id or not verify_unsubscribe_token(email, sequence_id, token):
            self._send_html(
                400,
                PAGE_TEMPLATE.format(
                    heading="Invalid link",
                    message="This unsubscribe link is invalid or has expired.",
                ),
            )
            return

        enr = models.get_enrollment(email)
        if enr and enr.get("sequence_id") == sequence_id and enr.get("status") == "active":
            enrollment.stop_sequence(enr, "unsubscribed")
            models.record_unsubscribe_stat()

        self._send_html(
            200,
            PAGE_TEMPLATE.format(
                heading="You've been unsubscribed",
                message=f"{email} will not receive any further emails in this sequence.",
            ),
        )
