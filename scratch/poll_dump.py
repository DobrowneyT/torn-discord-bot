"""
Phase 2 runner: poll Torn for live faction crimes + members, enrich with
names, run alerts.build_alerts on the result, and dump everything to
discord/scratch/dumps/<unix_ts>.json so we can confirm field shapes.

Does NOT touch Discord — this is for offline data exploration only.

Usage:
    cd discord
    source .venv/bin/activate
    python scratch/poll_dump.py            # runs forever, every 60s
    python scratch/poll_dump.py --once     # single fetch then exit
    python scratch/poll_dump.py --interval 30   # custom poll interval
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from dotenv import load_dotenv

import alerts as alerts_mod
import enrich
from config import POLL_INTERVAL_SECONDS
from item_cache import ItemNameCache
from torn_api import TornAPI, TornAPIError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("poll_dump")

DUMPS_DIR = os.path.join(_HERE, "dumps")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="single fetch then exit")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL_SECONDS,
                        help=f"poll interval in seconds (default {POLL_INTERVAL_SECONDS})")
    args = parser.parse_args()

    load_dotenv(os.path.join(_PARENT, ".env"))
    api_key = os.environ.get("TORN_API_KEY") or os.environ.get("VITE_TORN_API_KEY")
    if not api_key:
        raise SystemExit("TORN_API_KEY must be set in discord/.env")

    os.makedirs(DUMPS_DIR, exist_ok=True)
    api = TornAPI(api_key)
    item_cache = ItemNameCache()

    while True:
        try:
            tick(api, item_cache)
        except TornAPIError as e:
            log.error("Torn API error: %s — skipping this tick", e)
        except Exception as e:  # noqa: BLE001
            log.exception("Unexpected error: %s — skipping this tick", e)

        if args.once:
            break
        time.sleep(args.interval)


def tick(api: TornAPI, item_cache: ItemNameCache) -> None:
    now_ts = int(time.time())

    # cat=available returns crimes that are still in flight (Recruiting/Planning).
    crimes_resp = api.get_faction_crimes(
        category="available",
        filters="ready_at",
        sort="ASC",
        comment="OC_Watcher_Poll",
    )
    raw_crimes = crimes_resp.get("crimes", [])

    members_resp = api.get_faction_members(comment="OC_Watcher_Poll")
    member_lookup = enrich.build_member_lookup(members_resp)
    status_map = enrich.build_status_map(members_resp)

    item_ids = enrich.collect_item_ids(raw_crimes)
    item_names = item_cache.get_or_fetch(item_ids, api)

    enriched = enrich.enrich_crimes(raw_crimes, member_lookup, item_names)
    alerts_dict = alerts_mod.build_alerts(enriched, status_map, now_ts)

    dump_path = os.path.join(DUMPS_DIR, f"{now_ts}.json")
    payload = {
        "fetched_at": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
        "raw_crimes_count": len(raw_crimes),
        "members_count": len(member_lookup),
        "unavailable_members": len(status_map),
        "item_ids_seen": sorted(item_ids),
        "item_names_resolved": item_names,
        "status_map": status_map,
        "alerts": alerts_dict,
        "enriched_crimes": enriched,
    }
    with open(dump_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    log.info(
        "crimes=%d members=%d unavailable=%d items=%d alerts=%d dump=%s",
        len(raw_crimes), len(member_lookup), len(status_map),
        len(item_names), len(alerts_dict["crimes"]), os.path.relpath(dump_path),
    )


if __name__ == "__main__":
    main()
