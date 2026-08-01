import os
from concurrent.futures import ThreadPoolExecutor

from _lib import models
from _lib.auth import require_auth
from _lib.utils import today_str_local


def _gather():
    date_str = today_str_local()

    jobs = {
        "sequences": models.list_sequences,
        "accounts": models.list_accounts,
        "emails_sent_today": models.daily_sent_total,
        "contacts_enrolled_total": lambda: models.get_stat("stats:enrolled_total"),
        "active_enrollments": lambda: models.get_stat("stats:active_enrollments"),
        "replies_today": lambda: models.get_stat(f"stats:replies:{date_str}"),
        "replies_total": lambda: models.get_stat("stats:replies_total"),
        "unsubscribes_today": lambda: models.get_stat(f"stats:unsubscribes:{date_str}"),
        "unsubscribes_total": lambda: models.get_stat("stats:unsubscribes_total"),
        "bounces_today": lambda: models.get_stat(f"stats:bounces:{date_str}"),
        "bounces_total": lambda: models.get_stat("stats:bounces_total"),
    }

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(fn) for name, fn in jobs.items()}
        results = {name: future.result() for name, future in futures.items()}

    sequences = [s for s in results["sequences"] if not s.get("archived")]
    active_sequences = [s for s in sequences if s.get("status") == "active"]
    accounts = results["accounts"]

    return {
        "active_sequences": len(active_sequences),
        "total_sequences": len(sequences),
        "contacts_enrolled": results["contacts_enrolled_total"],
        "active_enrollments": results["active_enrollments"],
        "emails_sent_today": results["emails_sent_today"],
        "daily_cap": int(os.environ.get("DAILY_SEND_CAP", "500")),
        "replies_today": results["replies_today"],
        "replies_total": results["replies_total"],
        "unsubscribes_today": results["unsubscribes_today"],
        "unsubscribes_total": results["unsubscribes_total"],
        "bounces_today": results["bounces_today"],
        "bounces_total": results["bounces_total"],
        "connected_accounts": len([a for a in accounts if a.get("status") == "connected"]),
        "sequences": [
            {
                "id": s["id"],
                "name": s["name"],
                "status": s.get("status"),
                "steps": len(s.get("steps", [])),
            }
            for s in sequences
        ],
    }


def stats(self):
    if not require_auth(self):
        return
    self._send_json(200, _gather())
