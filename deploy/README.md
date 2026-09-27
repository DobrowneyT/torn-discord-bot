# Deploying choonbot

One box, one checkout, pull-based. `deploy/update.sh` is the only thing that
touches it — by hand or from cron.

```bash
./deploy/update.sh                # deploy now
./deploy/update.sh --if-changed   # what cron runs; silent when there is nothing new
```

Cron, as the `ubuntu` user, alongside the dashboard and mug-bot lines:

```
*/5 * * * * cd $HOME/torn/torn-discord-bot && ./deploy/update.sh --if-changed >> $HOME/torn/choonbot-deploy.log 2>&1
```

## What the box is on

⚠️ **`main`, not `dev`.** The bot runs in a Discord server full of real people,
and the same `dev → main` promotion that gates the dashboard gates this. A
checkout on `dev` would put every merged PR straight in front of the faction
with no step in between.

## Why it looks like the mug-bot's

Same box, same operator, same failure modes — a second shape to learn is a
second shape to get wrong at 2am. Two differences, both called out in the
script:

- **No database.** `state.json` is backed up instead, and a plain `cp` is
  enough because `state.save_state()` writes a `.tmp` and `os.replace()`s it.
  It is not re-derivable: the tenant list, the auto-matched Torn→Discord
  identities, and the id of every message the bot is tracking live there.
- **No HTTP health endpoint.** ⚠️ `systemctl is-active` is not a substitute:
  the unit is `Restart=on-failure`, so a bot that cannot log in reports
  `active` while crash-looping. Readiness is the `Logged in as` line in the
  journal, written after the gateway handshake, and only lines newer than the
  restart count.

## When a deploy fails

The marker (`.last-deployed`) is written **only** after the bot reaches
Discord, so a failed deploy leaves it stale and the next cron tick retries.
That is deliberate: a broken deploy that repeats loudly beats one that marks
itself done and goes quiet.

```bash
journalctl -u choonbot -n 50 --no-pager
tail -40 ~/torn/choonbot-deploy.log
```
