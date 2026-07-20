"""
Lazy item-name cache backed by a JSON file (discord/items_cache.json).

Crime payloads only carry item ids. Names rarely change, so we look up
unknown ids via /torn/<ids>?selections=items the first time we see them
and persist the result. Subsequent ticks hit the cache.
"""

import json
import logging
import os
from typing import Dict, Iterable, Optional

CACHE_PATH = os.path.join(os.path.dirname(__file__), "items_cache.json")

log = logging.getLogger("item_cache")


class ItemNameCache:
    def __init__(self, path: str = CACHE_PATH):
        self.path = path
        self._cache: Dict[int, str] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r") as f:
                raw = json.load(f)
            self._cache = {int(k): v for k, v in raw.items()}
        except (OSError, ValueError) as e:
            log.warning("Failed to load item cache %s: %s", self.path, e)
            self._cache = {}

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({str(k): v for k, v in self._cache.items()}, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def get(self, item_id: int) -> Optional[str]:
        return self._cache.get(int(item_id))

    def get_or_fetch(self, item_ids: Iterable[int], api) -> Dict[int, str]:
        """
        Return {id: name} for the requested ids. Fetches and persists any that
        aren't yet cached. `api` must have a .get_item_data(List[int]) method.
        """
        ids = {int(i) for i in item_ids if i is not None}
        missing = sorted(i for i in ids if i not in self._cache)

        if missing:
            log.info("Fetching %d new item names: %s", len(missing), missing)
            try:
                data = api.get_item_data(missing) or {}
            except Exception as e:  # noqa: BLE001
                log.warning("Item lookup failed: %s — names will fall back to ids", e)
                data = {}
            updated = False
            for sid, item in data.items():
                try:
                    iid = int(sid)
                except (TypeError, ValueError):
                    continue
                name = (item or {}).get("name")
                if name:
                    self._cache[iid] = name
                    updated = True
            if updated:
                self._save()

        return {i: self._cache[i] for i in ids if i in self._cache}
