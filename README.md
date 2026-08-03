# Cold Email Sequencer

Internal Scale Army tool that replaces Instantly for outbound cold email campaigns.
Python/Vercel serverless functions + Upstash Redis + vanilla JS frontend.

## Features

- Scale Army's shared design system: navy/cream/orange palette, Playfair Display + DM Sans, dark mode by
  default with a light-mode toggle (persisted in `localStorage`), sidebar navigation matching the other
  internal tools
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
- Scheduled sending (cron every 15 min): a contact's first email in a sequence goes out within a few minutes
  of enrollment (so leads hear back within ~30 minutes), later steps spread across the 8am–6pm ET window;
  respects a global daily cap (default 500) and per-account daily limits
- Per-sequence detail page (click a sequence from the dashboard) with roll-up stats (enrolled, active, sent,
  completed, replied, unsubscribed/failed) and a table of every enrolled contact's status and next send time.
  Clicking a contact opens the actual Gmail thread — the email(s) sent and any replies, read straight from
  Gmail, with a reply box to respond right there. Clicking a stat tile (Active/Completed/Replied/Bounced/
  Unsubscribed+failed) filters the contacts table to just that bucket
- Reply detection (cron every 30 min) that stops a contact's sequence automatically — this is also how
  opt-outs are handled: every email ends with a plain-text "Reply STOP to unsubscribe" line rather than a
  clickable link, and any reply (STOP or otherwise) stops that contact's sequence for good
- Bounce detection (same cron tick as reply detection) marks a contact `bounced` and stops their sequence.
  Searches each connected account's own inbox for bounce-shaped messages (from mailer-daemon/postmaster, or a
  "delivery status notification"/"undelivered mail" subject) and confirms which contact it's about by matching
  a currently-active enrolled email address in the message text - skips ambiguous matches rather than guessing
- Dashboard with active sequences, contacts enrolled, emails sent today, replies, bounces, unsubscribes

Lead/contact data is never deleted — enrollments are only ever marked `completed`,
`replied`, `bounced`, `unsubscribed`, or `failed`.

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
public/            static vanilla JS/HTML/CSS frontend
```

## Redis data model (Upstash)

| Key | Type | Purpose |
|---|---|---|
| `sequences` | hash | `{sequence_id: json(sequence)}` — never hard-deleted, only `archived: true` |
| `enrollments:{email}` | string | json enrollment record (sequence, step, status, thread info) |
| `sent:{email}:{sequence_id}` | string | dedup marker — a contact is never enrolled twice in the same sequence |
| `accounts` | hash | `{account_id: json(account)}` — connected Gmail accounts + OAuth tokens |
| `logs:{sequence_id}` | list | activity log entries (enrolled / sent / replied / bounced / unsubscribed / completed / errors), newest first, capped at 1000 |
| `queue:pending` | zset | `{sequence_id}\|{email} -> next_send_at unix ts`, drives the send cron |
| `active_enrollments` | set | emails with a currently-active enrollment, used by the reply/bounce-poll cron |
| `sequence_contacts:{sequence_id}` | set | every email ever enrolled in that sequence, powers the sequence detail page |
| `bounce_seen:{account_id}` | set | bounce-notification message IDs already checked for that account, so the same one isn't reprocessed every tick |
| `blocklist_check:{domain}` | string | cached `{results, checked_at}` from the last domain blocklist check, TTL 24h |
| `domain_auth_check:{domain}` | string | cached `{result, checked_at}` from the last SPF/DKIM/DMARC check, TTL 24h |
| `hubspot:config` | string | json `{mappings: [{list_id, list_name, sequence_id, sequence_name}]}` — the API key is never stored here, only `HUBSPOT_API_KEY` |
| `hubspot:seen:{list_id}` | set | HubSpot contact IDs already scanned for that list |
| `oauth:state:{state}` | string | CSRF state for the Gmail OAuth flow, TTL 10 min |
| `stats:sent:{date}` / `stats:sent:{account_id}:{date}` | string | daily send counters (global + per account) |
| `stats:sent_total:{account_id}` / `stats:bounces_total:{account_id}` | string | lifetime per-account counters, used for the health score's bounce rate |
| `stats:enrolled_total`, `stats:active_enrollments`, `stats:replies:{date}`, `stats:replies_total`, `stats:bounces:{date}`, `stats:bounces_total`, `stats:unsubscribes:{date}`, `stats:unsubscribes_total` | string | dashboard counters |

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

### 3. Deliverability: DKIM / SPF / DMARC

This tool sends through the **Gmail API** using your own connected Gmail/Workspace accounts - every email is
physically routed through Google's mail servers, not a dedicated IP this app controls. That means the hard
parts of sender authentication are Google's problem, not this codebase's: there's no SMTP server or IP
reputation for this app to manage. But Google only signs/authenticates mail correctly if the sending domain
is configured for it, and that configuration lives in Google Workspace admin + your domain's DNS - outside
anything this app's code can reach or set on your behalf. One-time setup, per sending domain (e.g.
`thescalearmy.com`), done by whoever administers that domain's Google Workspace and DNS:

1. **DKIM** - Google Workspace Admin Console → Apps → Google Workspace → Gmail → Authenticate email → generate
   a DKIM key for the domain, add the resulting DKIM TXT record to DNS, then come back and click "Start
   authentication" once DNS has propagated.
2. **SPF** - add (or update) a DNS TXT record on the domain: `v=spf1 include:_spf.google.com ~all`. Only one
   SPF record is allowed per domain - if one already exists, merge the `include` into it rather than adding a
   second record.
3. **DMARC** - add a DNS TXT record at `_dmarc.<yourdomain>`, e.g. `v=DMARC1; p=none; rua=mailto:you@yourdomain.com`.
   Start at `p=none` (monitor-only) so you see DMARC reports without risking mail getting rejected, then
   tighten to `p=quarantine` once you've confirmed SPF/DKIM are passing consistently.
4. Verify all three with a tool like [MXToolbox](https://mxtoolbox.com/SuperTool.aspx) or by sending a real
   test email to [mail-tester.com](https://www.mail-tester.com) and checking the SPF/DKIM/DMARC lines in the
   report.

Do this once per sending domain before connecting mailboxes on it — it protects deliverability for every
mailbox on that domain, not just the ones this app sends from.

Also worth setting up: [Google Postmaster Tools](https://postmaster.google.com) (verify the domain via a DNS
TXT record). Since every email here goes out through Gmail's own infrastructure, Postmaster Tools is the one
source that shows real domain/IP reputation, spam-rate, and delivery data straight from Google - more directly
relevant than generic third-party blocklists, which are more useful for setups sending through a dedicated
IP/custom SMTP server (like Instantly's), which this tool doesn't have.

### 4. HubSpot

Create a private app in HubSpot with the `contacts` scope, and set its access token as the `HUBSPOT_API_KEY`
environment variable in Vercel. That's the only place it's configured — it's never entered through the UI or
stored in Redis. The app's HubSpot page reads live contact properties and lists straight from HubSpot using
this key, for the merge-tag picker and the list dropdown.

List browsing and list membership use HubSpot's legacy v1 contacts API (`/contacts/v1/lists`), not the newer
v3 CRM Lists API — v3 Lists can require scopes or plan tiers that aren't available on every HubSpot account,
while v1 has been broadly available for years. The merge-tag property picker still uses the standard v3
properties-schema endpoint, which is unrelated to the Lists API and unaffected by this.

### 5. Environment variables

See `.env.example`. Required: `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`,
`APP_PASSWORD`, `APP_BASE_URL`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `CRON_SECRET`.

### 6. Deploy to Vercel

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
  (`effective_daily_limit - stats:sent:{account_id}:{date}`), rotating sends across accounts. Each sequence
  can optionally be scoped to specific connected accounts (a checklist in the sequence editor) - leaving none
  checked keeps the default of rotating across every connected account.
- **Warm-up**: a newly-connected account's `effective_daily_limit` ramps linearly from `WARMUP_START_LIMIT`
  (default 10/day) up to its configured `daily_limit` over `WARMUP_DAYS` (default 14) days since it was
  connected, instead of sending at full volume from day one. The Accounts page shows a "Ramping (N/day)" badge
  while this is in effect. Set `WARMUP_ENABLED=false` to disable and always use the configured `daily_limit`.
- The global `DAILY_SEND_CAP` (default 500) is checked before any sends happen in a tick.
- **Domain reputation**: the Accounts page checks each connected account's sending domain against SURBL and
  URIBL (cached 24h, "Recheck" to force). Deliberately excludes Spamhaus's DBL/ZEN - hand-verified that Spamhaus
  silently returns "not listed" for queries from public/shared DNS resolvers (the kind a serverless platform
  like Vercel uses) rather than a real answer, so including it would show a false "Clean" status. See Google
  Postmaster Tools above for a more authoritative check.
- **Health score**: each connected account gets a transparent 0-100 score (click it to see exactly what
  contributed) combining real DNS checks for SPF/DKIM/DMARC on its sending domain, that domain's blocklist
  status, this account's own bounce rate (needs at least 10 sends before it's judged - no data isn't treated as
  bad data), and warm-up progress. DKIM detection only recognizes Google Workspace's default `google` selector,
  since a custom selector name isn't discoverable - a "DKIM not found" result there means "couldn't confirm,"
  not certain proof it's missing. Domain-level checks (auth + blocklist) are shared and cached across every
  account on the same domain; account-level stats (bounce rate, warm-up) are computed live from Redis, no
  network calls. `GET /api/accounts/health_status`, `?refresh=1` to force fresh domain checks.

## Deliverability: deliberately not built

Two Instantly-style features were considered and left out on purpose, not overlooked:

- **Open/click tracking** - would require switching every email from plain text to HTML (a tracking pixel or
  rewritten link doesn't exist in plain text), which cuts directly against the plain-text, no-link approach
  this tool already uses for deliverability. Tracking pixels are also an increasingly unreliable signal (Apple
  Mail Privacy Protection and Gmail's own image proxy both prefetch images regardless of whether a human opened
  the email) and are a common spam-filter trigger in their own right - a bad trade for a domain that's still
  building sending reputation.
- **Full mailbox warm-up** (automated send/open/reply traffic across a network of seed mailboxes, the way
  Instantly/Mailreach/Warmup Inbox do it) - this only works if it's plugged into many real mailboxes across
  many real providers, so the receiving side's spam filters see genuine, varied engagement. A handful of this
  team's own connected accounts emailing each other wouldn't produce that signal - it would just be internal
  traffic, giving false confidence without the real effect. The daily-limit ramp-up above covers the practical
  benefit a small in-house tool can actually deliver; for the full effect, connect accounts through a dedicated
  warm-up service *before* connecting them here, rather than building a fake warm-up network in this app.

## Local development

Vercel's Python functions run one-per-file under `api/`. Use `vercel dev` to run the
whole app (API + static frontend) locally against your real Upstash instance.
