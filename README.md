# OC Watcher — Discord Bot

Self-contained Discord bot that polls the Torn faction-crimes API every minute and maintains a single edit-in-place message in a channel, flagging:

1. **Missing items** — crime executes in ≤24 h and an assigned member doesn't have the slot's required item.
2. **Unavailable members** — crime executes in ≤6 h and a member is in Hospital / Jail / Abroad / Traveling. The list updates live as members come back online.
3. **Below CPR** — assigned member's CPR is below the threshold in `config.py` `CPR_REQUIREMENTS` (mirrored from `src/config.js`).

On API errors the bot logs and **does not edit** the message — the last good state stays visible.

## Chain Watch: running it

Two ways, and the choice is about the **Discord token**, not about tidiness.

**On the bot you already have** (recommended — one token, one process). Set any
`CHAIN_WATCH_TOKEN_*` in `.env` and `bot.py` picks Chain Watch up on its next
start. Nothing else changes; with no token set, the OC watcher behaves exactly as
before.

```bash
.venv/bin/python bot.py
```

**As its own bot**, with a Discord token nothing else is using:

```bash
.venv/bin/python chain_bot.py
```

⚠️ **Never both on the same token.** Two processes sharing one token open two
gateway connections, and Discord routes each interaction to only ONE of them —
so the OC watcher's "Manage CPR overrides" button silently stops working about
half the time, with nothing logged. That failure looks like Discord being flaky
and is nearly impossible to diagnose from the symptom.

⚠️ A Chain Watch misconfiguration never takes the OC watcher down: `setup` and
`start` are guarded, and a failure logs and leaves the OC watcher running.

⚠️ **The members intent must be enabled** in the Discord developer portal. It is
what populates `guild.members`, and without it the guild looks empty, nobody is
auto-linked, and there is no error to explain why.

What it does each cycle, per faction:

- **draws the board**, editing one standing message in place. ⚠️ A failed poll
  leaves the last good board up with a staleness note rather than blanking it —
  an empty board reads as "nobody is signed up", which is the one message that
  must never be wrong.
- **pings a watcher** `shift_lead_minutes` before their hour.
- **pings a flyer early**, at `flight_lead_minutes`, when the landing band says
  they may not make it. Five minutes' notice is useless to somebody over the
  Atlantic; the point is that it arrives while they or a leader can still act.
- **announces an unfilled slot** inside the horizon the dashboard serves —
  ⚠️ **twice at most**: once on entering the horizon, once as a last call two
  hours out, then silence. A six-hour horizon re-checked every five minutes
  would be 72 identical messages about the same empty 3am slot, and a channel
  that mutes the bot is worse than no bot.

⚠️ Every ping is recorded so it fires once, and the record **survives a
restart** — otherwise every redeploy re-pings everybody, and redeploys happen
most while the thing is being tuned. Nothing is sent once a chain has ended.

## Chain Watch: adding a faction

The bot polls each faction's dashboard rather than Torn, so a faction costs a
URL and a token — no extra API key and no extra share of anybody's rate limit.

1. On the dashboard box: `node db/chain-watch-token.mjs <slug>`
2. Put that value in this bot's environment as `CHAIN_WATCH_TOKEN_<SLUG>` and
   restart. ⚠️ **Never through a slash command** — command arguments are visible
   client-side and land in logs.
3. In the faction's Discord server: `/chain tenant add slug:<slug>
   base_url:https://<slug>.monchoon.me board_channel:#chain`

`/chain tenant list` shows every faction and flags any whose token is missing.
Everything else — cadence, lead times, channels — is `/chain set`, per faction,
live.

## Chain Watch: who is who

The bot has to know which Discord user is which Torn member, or a shift ping
reaches nobody. It works this out **automatically** — on startup, whenever
somebody joins the server, and on demand with `/chain link-sync`:

1. an id in the Discord name — `Goosey [873341]`. Exact.
2. the Torn name matching the display name or username, ignoring case and
   punctuation.
3. the Torn name contained in one of them — `xX_Goosey_Xx`. Lower confidence,
   so it needs a name of at least four characters and exactly one hit.

⚠️ **Anything ambiguous is left unlinked on purpose.** Two people who could both
be the same member means guessing, and a wrong link pings the wrong person about
somebody else's 3am shift — with no way for them to know it was not for them.
Unlinked is visible in `/chain link-status`; wrong is not.

`/chain link @user <torn_id>` is the escape hatch for the rest. ⚠️ A manual link
always beats the auto-match and survives a re-sync — otherwise the one
correction leadership bothered to make is silently undone on the next restart.

⚠️ **The map is standing state, not per-event.** Link somebody once and every
future chain resolves through it; nothing re-runs when the sign-up sheet
changes. Members with no link render as `Name [ID]` so leadership can see who to
chase.

The OC watcher is separate and single-faction; none of this touches it.

## Branch policy

`main` is what runs live. `dev` is the staging branch the test environment
tracks. Features branch off `dev`, PR into `dev` with CI green, and an approved
**`dev → main`** merge is what promotes. Never force-push `main`.

Same policy as `DobrowneyT/torn-dashboard`, deliberately — the two repos ship
one feature between them (Chain Watch, dashboard epic #768), and a change that
lands live on one side while the other is still on a branch is how the board
starts rendering a payload that does not exist yet.

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

CI runs the same on every PR and on pushes to `dev` and `main`.

## Phases

- **Phase 1 — message format with mock data.** `mock_data.py` + `alerts.py` + `formatter.py` + `scratch/format_preview.py`.
- **Phase 2 — live data exploration.** `scratch/poll_dump.py` polls the real API and writes raw + parsed JSON to `scratch/dumps/` for inspection.
- **Phase 3 — live bot.** `bot.py` ties phases 1 and 2 together as a long-lived process that maintains one edit-in-place message.

## Setup

```bash
cd discord
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, TORN_API_KEY, FACTION_ID
```

Bot needs these Discord permissions in the target channel:

- View Channel
- Send Messages
- Embed Links
- Read Message History (so it can fetch and edit its own past message after a restart)

## Phase 1 — preview the embed

```bash
python scratch/format_preview.py
```

First run sends a new message and saves its id to `state.json`. Subsequent runs **edit that same message** so you can iterate on `formatter.py` without spamming the channel.

## Phase 2 — live API poll & dump

`scratch/poll_dump.py` polls Torn for real faction crimes + members, runs them through `alerts.build_alerts`, and writes the raw API payload + parsed alert dict to `scratch/dumps/<unix_ts>.json` every tick. **It does not touch Discord.** Use it to confirm field shapes (especially whether `slot.user` already carries a status field) before going live.

```bash
python scratch/poll_dump.py            # runs forever, every 60s
python scratch/poll_dump.py --once     # single fetch
python scratch/poll_dump.py --interval 30
```

Each dump includes the enriched crime payload, the `status_map`, and the resolved `item_names`, so you can spot-check end-to-end without going live.

## Phase 3 — run the live bot

```bash
python bot.py
```

What it does each tick (default every 60s, see `POLL_INTERVAL_SECONDS`):

1. Calls `/v2/faction/crimes?cat=available&filters=ready_at&sort=ASC` and `/v2/faction/members`.
2. Enriches with names + statuses, runs `alerts.build_alerts`, renders the embed.
3. Edits the persisted message in `DISCORD_CHANNEL_ID`.

Resilience:

- **API errors** are logged and the existing message is left untouched (last good payload stays visible).
- **Identical alerts** are not re-edited every tick — the bot skips the edit until alerts change OR ~10 minutes have passed (so the "Last updated" footer doesn't go too stale).
- **Restart-safe** — the message id is in `state.json`; the bot reattaches to the same message on restart, or sends a new one if it was deleted.

## Low-CPR overrides

Some members are intentionally placed in roles below their CPR threshold. To stop them from being flagged forever, click the **🛡 Manage CPR overrides** button under the message:

- An ephemeral select menu (only you see it) lists every currently-flagged low-CPR member plus any active overrides (✓-marked and pre-selected).
- Toggle the items you want approved and submit. Items left selected become approved; items unselected are removed.
- Approvals are scoped to **one specific (crime_id, user_id, position) tuple**. If the member changes role, joins a different crime, or this crime executes, the approval no longer applies.
- The bot prunes overrides whose `crime_id` is no longer active each tick, so old approvals don't leak into future crimes.
- Persisted in `state.json` so approvals survive bot restarts. Anyone in the channel can approve.

## Posting to a thread instead of a channel

The bot doesn't care whether `DISCORD_CHANNEL_ID` points at a normal channel or at a thread — both behave the same as far as the message-edit API is concerned. To run inside a thread:

1. Create a thread off your OC channel, set its auto-archive to **1 week** (the longest option Discord offers).
2. Send a placeholder message in the thread, copy its parent thread id, and put it in `DISCORD_CHANNEL_ID`.
3. Make sure the bot has **Send Messages**, **Embed Links**, **Read Message History**, and **Manage Threads** in the parent channel. Manage Threads is needed for the auto-unarchive step described next.

**About auto-archive:** Discord thread timers reset only when a *new message* is sent. Editing an existing message does **not** count, so an idle thread will still auto-archive after the configured duration (1h / 24h / 3d / 7d). To deal with that, the bot detects an archived thread before each edit and calls `thread.edit(archived=False)` to revive it. The next edit goes through normally and you don't see any spam in the thread.

If the bot lacks Manage Threads, it logs a warning and silently skips the edit — once you fix the permission, the next tick recovers automatically.

To keep it alive across logouts, run under tmux/systemd/etc.:

```bash
tmux new -s oc-watcher
source .venv/bin/activate
python bot.py
# Ctrl-b d to detach
```

## Files

| File | Purpose |
|---|---|
| `config.py` | CPR thresholds (mirror of `src/config.js`), windows, status sets |
| `torn_api.py` | Torn API v2 wrapper (copied from `scripts/utils/torn_api.py`) |
| `enrich.py` | Joins crimes + `/faction/members` + item-name cache → enriched crimes |
| `item_cache.py` | Lazy JSON-backed item-name cache (`items_cache.json`) |
| `mock_data.py` | Hand-crafted crime payloads exercising all 3 alert types |
| `alerts.py` | Pure logic: enriched crimes → structured alerts dict |
| `formatter.py` | alerts dict → `discord.Embed` |
| `state.py` | Persists `message_id` and `cpr_overrides` in `state.json` across restarts |
| `views.py` | Persistent "Manage CPR overrides" button + ephemeral toggle UI |
| `bot.py` | Phase 3 entry — long-lived poller that edits the message in place |
| `scratch/format_preview.py` | Phase 1 runner — post/edit mock embed |
| `scratch/poll_dump.py` | Phase 2 runner — poll API and dump JSON for inspection |
| `scratch/smoke_real.py` | Phase 2 fixture — runs a real API excerpt through the pipeline |

## Data shape notes (from real `/v2/faction/crimes?cat=available`)

- `slot.user` only carries `id`/`joined_at`/`progress`/`outcome`/`item_outcome` — **no name**, **no status**. Names come from `/faction/members`; status comes from the same call's `member.status.state` field.
- `slot.item_requirement` carries `id`/`is_reusable`/`is_available` — **no name**. Names are looked up via `/torn/<ids>?selections=items` and persisted in `items_cache.json`.
- Position matching needs `slot.position_info.label` (e.g. `"Muscle #1"`), not the bare `slot.position` (`"Muscle"`). `alerts.py` falls back to the base name when the suffixed label isn't in the config.

## Env vars

| Var | Purpose |
|---|---|
| `DISCORD_BOT_TOKEN` | Bot token from the Discord developer portal |
| `DISCORD_CHANNEL_ID` | Target channel id (right-click → Copy ID with developer mode on) |
| `TORN_API_KEY` | Faction API key (Phase 2+) |
| `FACTION_ID` | Numeric faction id, e.g. `10739` (Phase 2+) |
