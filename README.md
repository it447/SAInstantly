# Cold Email Sequencer

Internal Scale Army tool that replaces Instantly for outbound cold email campaigns.
Python/Vercel serverless functions + Upstash Redis + vanilla JS frontend.

## Features

- Password-protected login. The typed password is sent as an `X-Auth-Token` header on every request and
  checked directly against `APP_PASSWORD` - no server-side session, cookies, or Redis storage involved. The
  frontend keeps it in `localStorage` after a successful login
- Connect one or more Gmail accounts via OAuth, for inbox rotation. Mailboxes on `PROTECTED_DOMAINS`
  (defaults to `scalearmy.com`) are refused outright, so the primary domain can never be connected here and
  mixed into cold-outreach sending — only dedicated cold-outreach domains should be connected
- Multi-step sequence builder with merge tags and per-step delay. Merge tags use HubSpot's own contact
  property names (e.g. `{{firstname}}`, `{{company}}`) so the sequence editor's "Insert merge tag" picker
  can search your actual HubSpot contact properties (`GET /api/hubspot/properties`) and drop the right tag
  in with a click — no HubSpot key connected yet just falls back to a small default set
  (`email`, `firstname`, `lastname`, `company`)
- HubSpot list → sequence connection: new list members are auto-enrolled (deduped so a contact is never enrolled twice in the same sequence)
- Scheduled sending (cron every 15 min) that spreads emails between 8am–6pm ET, respects a global daily cap (default 500) and per-account daily limits
- Reply detection (cron every 30 min) that stops a contact's sequence automatically
- One-click unsubscribe link on every email that stops the sequence permanently
- Dashboard with active sequences, contacts enrolled, emails sent today, replies, unsubscribes

Lead/contact data is never deleted — enrollments are only ever marked `completed`,
`replied`, `unsubscribed`, or `failed`.

## Architecture

Vercel's Python runtime only recognizes a single entrypoint per project (it errors
out if it finds more than one file exporting a `handler`/`app`), so all routes are
served from one catch-all dynamic route, `api/[...path].py`, which dispatches to
plain view functions based on the real request path:

```
api/
  [...path].py     single entrypoint; dispatches (method, path) -> view function
  _lib/            shared helpers (redis, auth, gmail, hubspot, scheduling, models)
  _views/
    auth.py        login (header test) / logout / auth status
    accounts.py    Gmail OAuth connect/callback + account management
    sequences.py   sequence CRUD + activity logs
    hubspot.py     API key + list-to-sequence mapping config
    cron.py        send (every 15 min), poll_replies (every 30 min), hubspot_sync (every 15 min)
    dashboard.py   stats endpoint
    unsubscribe.py public unsubscribe landing page
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
| `hubspot:config` | string | json `{mappings: [{list_id, list_name, sequence_id, sequence_name}]}` — the API key is never stored here, only `HUBSPOT_API_KEY` |
| `hubspot:seen:{list_id}` | set | HubSpot contact IDs already scanned for that list |
| `oauth:state:{state}` | string | CSRF state for the Gmail OAuth flow, TTL 10 min |
| `stats:sent:{date}` / `stats:sent:{account_id}:{date}` | string | daily send counters (global + per account) |
| `stats:enrolled_total`, `stats:active_enrollments`, `stats:replies:{date}`, `stats:replies_total`, `stats:unsubscribes:{date}`, `stats:unsubscribes_total` | string | dashboard counters |

## Setup

### 1. Upstash Redis

Either create a database directly at [upstash.com](https://upstash.com) and copy the REST URL/token into
`UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`, or connect Upstash through Vercel's Storage tab
Marketplace integration. The Vercel integration injects the credentials under its legacy "KV" names instead
(`KV_REST_API_URL` / `KV_REST_API_TOKEN`) - the code checks for either naming automatically, so no manual
renaming is needed either way.

### 2. Google OAuth (Gmail)

1. In the [Google Cloud Console](https://console.cloud.google.com/apis/credentials), create an
   OAuth 2.0 Client ID (Web application).
2. Enable the **Gmail API** for the project.
3. Add an authorized redirect URI: `https://<your-deployment>/api/accounts/callback`.
4. Add scopes `gmail.send` and `gmail.readonly` (and `userinfo.email`) — if the app is in
   "Testing" publishing status, add every Gmail address you plan to connect as a test user.
5. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`.

### 3. HubSpot

Create a private app in HubSpot with the `contacts` scope, and set its access token as the `HUBSPOT_API_KEY`
environment variable in Vercel. That's the only place it's configured — it's never entered through the UI or
stored in Redis. The app's HubSpot page reads live contact properties and lists straight from HubSpot using
this key, for the merge-tag picker and the list dropdown.

List browsing and list membership use HubSpot's legacy v1 contacts API (`/contacts/v1/lists`), not the newer
v3 CRM Lists API — v3 Lists can require scopes or plan tiers that aren't available on every HubSpot account,
while v1 has been broadly available for years. The merge-tag property picker still uses the standard v3
properties-schema endpoint, which is unrelated to the Lists API and unaffected by this.

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
