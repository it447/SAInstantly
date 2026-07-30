"""Shared Upstash Redis client and small helpers around it.

Redis schema (see README.md for the full reference):
  sequences                      hash   { sequence_id: json(sequence) }
  enrollments:{contact_email}    string json(enrollment)
  sent:{email}:{sequence_id}     string "1"                       (dedup marker, never expires)
  accounts                       hash   { account_id: json(account) }
  logs:{sequence_id}             list   json(log_entry), newest first
  queue:pending                  zset   member=f"{sequence_id}|{email}" score=next_send_at (unix ts)
  hubspot:config                 string json({api_key, list mappings})
  hubspot:seen:{list_id}         set    contact emails already scanned for that list
  sessions:{token}               string "1"                       (auth session, has TTL)
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
        url = os.environ["UPSTASH_REDIS_REST_URL"]
        token = os.environ["UPSTASH_REDIS_REST_TOKEN"]
        _client = Redis(url=url, token=token)
    return _client
