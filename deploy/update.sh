#!/usr/bin/env bash
# Pull the latest code onto this box and restart the bot.
#
#   ./deploy/update.sh                # pull + deps + backup + restart + verify
#   ./deploy/update.sh --if-changed   # cron: quiet unless origin has moved since the last
#                                     #   SUCCESSFUL deploy (not since the last pull — see marker)
#   SKIP_BACKUP=1 ./deploy/update.sh  # skip the pre-restart state.json snapshot
#
# To force a redeploy of the current checkout — the recovery path after an interrupted run — drop
# the flag. Detached, so a dropped connection cannot kill it half-way:
#   setsid nohup ./deploy/update.sh >> $HOME/torn/choonbot-deploy.log 2>&1 &
#
# Deploys whatever branch this checkout is ON. Pull-based on purpose: the box asks GitHub for
# changes rather than CI pushing to the box, so no credentials to this machine live anywhere off it.
# Anything on main has already passed CI (branch protection), so this script does not re-run tests.
#
# ⚠️ Modelled on torn-mug-bot's deploy/update.sh, deliberately. Same box, same operator, same
# failure modes — a second shape to learn is a second shape to get wrong at 2am. The differences
# are called out where they occur, and there are only two that matter: this bot has no database
# and no HTTP health endpoint.
#
# Operator state is never touched: .env and state.json are gitignored, so `git merge` cannot
# clobber them.
set -euo pipefail

# Everything lives in main() so the whole script is parsed into memory before it runs — this script
# updates its own file mid-execution, and bash reads scripts lazily, so a top-level body could be
# corrupted by the very `git merge` it just performed.
main() {
  local repo_dir branch local_sha remote_sha marker deployed_sha service restart_at

  repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  cd "$repo_dir"
  marker="$repo_dir/.last-deployed"
  service="${CHOONBOT_SERVICE:-choonbot}"

  # One deploy at a time — a 5-minute cron must never overlap a slow run (pip, a cold restart).
  exec 9>"/tmp/choonbot-update.lock"
  flock -n 9 || { echo "another update is already running — skipping"; exit 0; }

  branch="$(git rev-parse --abbrev-ref HEAD)"
  git fetch --quiet origin "$branch"
  local_sha="$(git rev-parse HEAD)"
  remote_sha="$(git rev-parse "origin/$branch")"

  # Nothing to do: exit before any other check, so a cron run stays silent and a dirty tree on an
  # up-to-date box isn't reported as a failure every five minutes.
  #
  # ⚠️ Compared against what was last successfully DEPLOYED, not against HEAD. The merge below
  # happens before the deps, the backup and the restart, so an interrupted run (a closed SSH
  # session is the usual way) leaves HEAD already equal to origin with none of the work done — and
  # a HEAD-based guard would then answer "nothing to do" forever, silently, with the box left on
  # the old code. This cost the dashboard box five hours on 2026-08-15. An absent marker reads as
  # "never deployed", so the first run after this change deploys once and re-arms the mechanism.
  deployed_sha="$(cat "$marker" 2>/dev/null || true)"
  if [ "${1:-}" = "--if-changed" ] && [ "$deployed_sha" = "$remote_sha" ]; then
    exit 0
  fi

  # Locally MODIFIED TRACKED files mean someone edited the deployed code in place; merging over that
  # would silently discard their work, so stop. Checked before anything is reported or changed, so
  # the refusal is the first thing you see.
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "ERROR: tracked files have local changes — refusing to deploy. Commit, stash or revert:" >&2
    git status --short --untracked-files=no >&2
    exit 1
  fi

  # UNTRACKED files are only noted. They can't be lost by a fast-forward, and if an incoming commit
  # would overwrite one, git itself refuses the merge with a precise message. Blocking on them was
  # too strict on the mug-bot box and produced repeated false stops — here it would fire on
  # state-backups/ and every scratch dump.
  local untracked
  untracked="$(git ls-files --others --exclude-standard)"
  if [ -n "$untracked" ]; then
    echo "[note] untracked files present (not a problem for the deploy):"
    printf '%s\n' "$untracked" | sed 's/^/  /'
  fi

  if [ "$local_sha" = "$remote_sha" ]; then
    echo "already up to date at $(git rev-parse --short HEAD) — restarting anyway"
  fi

  echo "==> deploying $branch: $(git rev-parse --short HEAD) -> $(git rev-parse --short "origin/$branch")"
  # --ff-only: a server checkout must never invent merge commits. If this fails, someone committed
  # on the server — resolve it by hand rather than forcing.
  git merge --ff-only "origin/$branch" --quiet

  # Reinstall deps only when they actually changed — pip is by far the slowest step here.
  if [ "$local_sha" != "$remote_sha" ] \
     && git diff --name-only "$local_sha" "$remote_sha" | grep -qx 'requirements.txt'; then
    echo "==> requirements.txt changed — updating the venv"
    # `python -m pip` rather than `.venv/bin/pip`: console-script shebangs hardcode an absolute
    # interpreter path, so they break if the checkout is ever renamed or moved, while the
    # interpreter symlink keeps working. Running the module sidesteps that entirely.
    "$repo_dir/.venv/bin/python" -m pip install --quiet -r "$repo_dir/requirements.txt"
  fi

  [ "${SKIP_BACKUP:-0}" = "1" ] || backup_state "$repo_dir"

  # ⚠️ Captured BEFORE the restart, and in LOCAL time, because that is what journalctl --since
  # expects. The readiness check below reads only lines newer than this — without it, the previous
  # boot's "Logged in as" would satisfy the check instantly and every failed deploy would report
  # success.
  restart_at="$(date '+%Y-%m-%d %H:%M:%S')"
  echo "==> restarting $service"
  sudo systemctl restart "$service"

  # Only reached when wait_ready succeeded — it returns non-zero otherwise and `set -e` stops the
  # script here, deliberately leaving the marker stale so the next cron tick retries. A broken
  # deploy that repeats loudly beats one that marks itself done and goes quiet.
  wait_ready "$service" "$restart_at"
  git rev-parse HEAD > "$marker"
  echo "==> deployed $(git rev-parse --short HEAD) ($(date -u +%FT%TZ))"
}

# Snapshot state.json next to the checkout, keeping the most recent few.
#
# ⚠️ **This is the mug-bot's database backup, and it matters as much.** state.json is gitignored
# operator state that CANNOT be re-derived: the tenant list, the 93 auto-matched Torn→Discord
# identities, the id of every board and ping message the bot is tracking. Lose it and the bot
# posts a second standing board beside the first and forgets who everybody is.
#
# ⚠️ A plain `cp` is safe here. state.save_state() writes to a .tmp and os.replace()s it, so the
# file is never observed half-written — which is exactly why the mug-bot needs `sqlite3 .backup`
# and this does not.
backup_state() {
  local repo_dir="$1" src dir stamp
  src="$repo_dir/state.json"
  if [ ! -f "$src" ]; then
    echo "[warn] no state.json — skipping backup (a first run on a fresh box, or the wrong directory)"
    return 0
  fi
  dir="$repo_dir/state-backups"
  mkdir -p "$dir"
  # Timestamp + the SHA being replaced: two deploys in the same second would otherwise write the
  # same filename, and the SHA tells you at a glance which version a backup belongs to.
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  echo "==> backing up state.json"
  cp -p "$src" "$dir/state-${stamp}-$(git -C "$repo_dir" rev-parse --short HEAD).json"
  # Keep the newest 10, by mtime. find+sort rather than parsing `ls` output (shellcheck SC2012).
  find "$dir" -maxdepth 1 -name 'state-*.json' -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | tail -n +11 | cut -d' ' -f2- \
    | while read -r old; do rm -f "$old"; done
}

# Wait until the freshly-restarted bot has actually reached Discord.
#
# ⚠️ **`systemctl is-active` is NOT a readiness check for this service.** The unit is
# Restart=on-failure with RestartSec=10, so a bot that cannot log in — a revoked token, a bad
# CHAIN_* value, a syntax error — spends most of its life reporting `active` or `activating` while
# crash-looping. The mug-bot polls its own /health endpoint; this bot has no port to poll, so the
# equivalent signal is the on_ready line it writes once the gateway handshake succeeds.
#
# Polling rather than a single shot: a cold start takes a few seconds, and a single check would log
# a spurious failure on every deploy.
#
# ⚠️ This reads the journal WITHOUT sudo, which works only because the unit runs as `ubuntu` and a
# user can always read their own unit's messages. If the service is ever changed to run as another
# user, this check matches nothing and EVERY deploy fails — loudly and forever, with a cause that
# is nowhere near the symptom. Add `ubuntu` to the `systemd-journal` group, or use `sudo -n`.
wait_ready() {
  local service="$1" since="$2" i
  echo "==> waiting for the gateway handshake in the journal"
  for i in $(seq 1 30); do
    if journalctl -u "$service" --since "$since" --no-pager 2>/dev/null | grep -q 'Logged in as'; then
      # ⚠️ And still running. Seeing the line is not enough on its own: a bot that logs in and then
      # dies on the first tick prints it once per restart, so a crash loop would otherwise read as
      # a healthy deploy.
      if [ "$(systemctl is-active "$service")" = "active" ]; then
        echo "  ok   reached Discord after ${i}x2s"
        return 0
      fi
    fi
    sleep 2
  done
  echo "  FAIL bot did not reach Discord within 60s" >&2
  echo "       check: journalctl -u ${service} -n 50 --no-pager" >&2
  return 1
}

main "$@"
