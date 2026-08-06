"""Shared Upstash Redis client and small helpers around it.

Redis schema (see README.md for the full reference):
  sequences                      hash   { sequence_id: json(sequence) }
  enrollments:{contact_email}    string json(enrollment)
  sent:{email}:{sequence_id}     string "1"                       (dedup marker, never expires)
  accounts                       hash   { account_id: json(account) }
  logs:{sequence_id}             list   json(log_entry), newest first
  queue:pending                  zset   member=f"{sequence_id}|{email}" score=next_send_at (unix ts)
  active_enrollments             set    emails with a currently-active enrollment
  hubspot:config                 string json({mappings: [...]})    (the API key lives in HUBSPOT_API_KEY, not here)
  hubspot:seen:{list_id}         set    contact IDs already scanned for that list
  oauth:state:{state}            string "1"                       (CSRF state for OAuth, has TTL)
  stats:sent:{date}              string int counter (all accounts, that day)
  stats:sent:{account_id}:{date} string int counter (per account, that day)
  stats:replies:{date}           string int counter
  stats:unsubscribes:{date}      string int counter
"""
import os
from upstash_redis import Redis

_client = None


def get_redis():
    global _client
    if _client is None:
        # Support both naming conventions: UPSTASH_REDIS_REST_* if Upstash was
        # connected directly, or Vercel's legacy KV_REST_API_* names if it was
        # connected via Vercel's Marketplace integration (which still uses the
        # old "Vercel KV" variable names under the hood).
        url = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ["KV_REST_API_URL"]
        token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ["KV_REST_API_TOKEN"]
        _client = Redis(url=url, token=token)
    return _client
