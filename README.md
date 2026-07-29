# Cold Email Sequencer

Internal Scale Army tool that replaces Instantly for outbound cold email campaigns.
Python/Vercel serverless functions + Upstash Redis + vanilla JS frontend.

## Features

- Password-protected login (session stored in Redis)
- Connect one or more Gmail accounts via OAuth, for inbox rotation
- Multi-step sequence builder with merge tags (`{{first_name}}`, `{{last_name}}`, `{{company}}`, `{{email}}`) and per-step delay
- HubSpot list → sequence connection: new list members are auto-enrolled (deduped so a contact is never enrolled twice in the same sequence)
- Scheduled sending (cron every 15 min) that spreads emails between 8am–6pm ET, respects a global daily cap (default 500) and per-account daily limits
- Reply detection (cron every 30 min) that stops a contact's sequence automatically
- One-click unsubscribe link on every email that stops the sequence permanently
- Dashboard with active sequences, contacts enrolled, emails sent today, replies, unsubscribes

Lead/contact data is never deleted — enrollments are only ever marked `completed`,
`replied`, `unsubscribed`, or `failed`.

## Architecture

```
api/
  _lib/            shared helpers (redis, auth, gmail, hubspot, scheduling, models)
  auth/            login / logout / session status
  accounts/        Gmail OAuth connect/callback + account management
  sequences/       sequence CRUD + activity logs
  hubspot/         API key + list-to-sequence mapping config
  cron/            send.py (every 15 min), poll_replies.py (every 30 min), hubspot_sync.py (every 15 min)
  unsubscribe.py   public unsubscribe landing page
  dashboard/       stats endpoint
public/            static vanilla JS/HTML/CSS frontend
```

## Redis data model (Upstash)

| Key | Type | Purpose |
|---|---|---|
| `sequences` | hash | `{sequence_id: json(sequence)}` — never hard-deleted, only `archived: true` |
| `enrollments:{email}` | string | json enrollment record (sequence, step, status, thread info) |
| `sent:{email}:{sequence_id}` | string | dedup marker — a contact is never enrolled twice in the same sequence |
| `accounts` | hash | `{account_id: json(account)}` — connected Gmail accounts + OAuth tokens |
| `logs:{sequence_id}` | list | activity log entries (enrolled / sent / replied / unsubscribed / completed / errors), newest first, capped at 1000 |
| `queue:pending` | zset | `{sequence_id}\|{email} -> next_send_at unix ts`, drives the send cron |
| `active_enrollments` | set | emails with a currently-active enrollment, used by the reply-poll cron |
| `hubspot:config` | string | json `{api_key, mappings: [{list_id, list_name, sequence_id, sequence_name}]}` |
| `hubspot:seen:{list_id}` | set | HubSpot contact IDs already scanned for that list |
| `sessions:{token}` | string | login session, TTL 7 days |
| `oauth:state:{state}` | string | CSRF state for the Gmail OAuth flow, TTL 10 min |
| `stats:sent:{date}` / `stats:sent:{account_id}:{date}` | string | daily send counters (global + per account) |
| `stats:enrolled_total`, `stats:active_enrollments`, `stats:replies:{date}`, `stats:replies_total`, `stats:unsubscribes:{date}`, `stats:unsubscribes_total` | string | dashboard counters |

## Setup

### 1. Upstash Redis

Create a database at [upstash.com](https://upstash.com), copy the REST URL and token into
`UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`.

### 2. Google OAuth (Gmail)

1. In the [Google Cloud Console](https://console.cloud.google.com/apis/credentials), create an
   OAuth 2.0 Client ID (Web application).
2. Enable the **Gmail API** for the project.
3. Add an authorized redirect URI: `https://<your-deployment>/api/accounts/callback`.
4. Add scopes `gmail.send` and `gmail.readonly` (and `userinfo.email`) — if the app is in
   "Testing" publishing status, add every Gmail address you plan to connect as a test user.
5. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`.

### 3. HubSpot

Create a private app in HubSpot with `crm.objects.contacts.read` and `crm.lists.read` scopes,
paste the access token into the app's HubSpot page (stored in Redis, or set
`HUBSPOT_API_KEY` as a fallback default).

### 4. Environment variables

See `.env.example`. Required: `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`,
`APP_PASSWORD`, `UNSUBSCRIBE_SECRET`, `APP_BASE_URL`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `CRON_SECRET`.

### 5. Deploy to Vercel

```
vercel deploy
```

`vercel.json` registers the three cron jobs. **Note:** frequent (sub-daily) cron schedules
require a Vercel Pro plan — on the Hobby plan, either upgrade or trigger
`/api/cron/send`, `/api/cron/poll_replies`, `/api/cron/hubspot_sync` from an external
scheduler (e.g. cron-job.org) with the `Authorization: Bearer $CRON_SECRET` header.

## Sending behavior

- `next_send_at` for every step is a randomized timestamp inside the 8am–6pm
  (`SEND_TIMEZONE`, default America/New_York) window, so sends are naturally spread
  through the day rather than firing all at once.
- The send cron additionally caps itself to 20 emails per 15-minute tick, so a large
  batch enrollment (e.g. a big HubSpot list import) trickles out over many ticks instead
  of bursting.
- Account selection picks whichever connected account has the most daily capacity left
  (`daily_limit - stats:sent:{account_id}:{date}`), rotating sends across accounts.
- The global `DAILY_SEND_CAP` (default 500) is checked before any sends happen in a tick.

## Local development

Vercel's Python functions run one-per-file under `api/`. Use `vercel dev` to run the
whole app (API + static frontend) locally against your real Upstash instance.
