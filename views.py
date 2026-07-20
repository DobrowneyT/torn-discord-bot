"""
Discord UI for managing low-CPR overrides.

Layout:
  - The main message has one persistent button: "Manage CPR overrides".
  - Click → bot replies (ephemeral) with a select menu listing every
    currently-flagged low-CPR member plus every active override.
  - Pre-selected items = currently approved. Submit reconciles the store:
    items selected become approved, items deselected become unapproved.
"""

import logging
from typing import Callable, Dict, List, Optional

import discord

from state import CprOverrideStore

log = logging.getLogger("views")

PERSISTENT_BUTTON_ID = "oc_watcher_manage_overrides_v1"
SELECT_OPTION_LIMIT = 25  # Discord's hard cap


class ManageOverridesView(discord.ui.View):
    """Persistent (timeout=None) view holding the single management button.

    Constructed once at bot startup and registered with `bot.add_view(...)`.
    The button callback reads the latest alerts via `alerts_provider` and
    mutates the shared `store`.
    """

    def __init__(self, store: CprOverrideStore, alerts_provider: Callable[[], Optional[Dict]]):
        super().__init__(timeout=None)
        self.store = store
        self.alerts_provider = alerts_provider

    @discord.ui.button(
        label="Manage CPR overrides",
        style=discord.ButtonStyle.secondary,
        custom_id=PERSISTENT_BUTTON_ID,
        emoji="🛡",
    )
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        await open_override_menu(
            interaction,
            store=self.store,
            alerts_provider=self.alerts_provider,
        )


class OverrideSelectView(discord.ui.View):
    """Per-interaction view holding the toggle select. Times out after 5 minutes."""

    def __init__(self, store: CprOverrideStore, options_data: List[Dict]):
        super().__init__(timeout=300)
        self.add_item(OverrideSelect(store, options_data))


class OverrideSelect(discord.ui.Select):
    def __init__(self, store: CprOverrideStore, options_data: List[Dict]):
        self.store = store
        self._meta_by_value = {d["value"]: d for d in options_data}
        super().__init__(
            placeholder="Toggle approvals…",
            min_values=0,
            max_values=len(options_data),
            options=[
                discord.SelectOption(
                    label=d["label"],
                    value=d["value"],
                    default=d["default"],
                )
                for d in options_data
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            log.info(
                "Override selection submitted: user=%s values=%d",
                getattr(interaction.user, "id", "?"),
                len(self.values),
            )
            selected = set(self.values)
            actions: List[str] = []

            for value, meta in self._meta_by_value.items():
                cid = meta["crime_id"]
                uid = meta["user_id"]
                pos = meta["position"]
                currently = self.store.has(cid, uid, pos)
                wants = value in selected

                if wants and not currently:
                    self.store.add(cid, uid, pos,
                                   crime_name=meta["crime_name"],
                                   user_name=meta["user_name"])
                    actions.append(f"✓ approved · {meta['crime_name']} · {meta['user_name']} · {pos}")
                elif currently and not wants:
                    self.store.remove(cid, uid, pos)
                    actions.append(f"✗ removed · {meta['crime_name']} · {meta['user_name']} · {pos}")

            msg = "\n".join(actions) if actions else "No changes."
            await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            log.exception("Override selection failed")
            raise


async def open_override_menu(
    interaction: discord.Interaction,
    *,
    store: CprOverrideStore,
    alerts_provider: Callable[[], Optional[Dict]],
) -> None:
    try:
        log.warning(
            "Manage overrides clicked: user=%s channel=%s message=%s",
            getattr(interaction.user, "id", "?"),
            getattr(interaction.channel, "id", "?"),
            getattr(interaction.message, "id", "?"),
        )
        latest = alerts_provider() or {"crimes": []}
        log.info(
            "Latest alerts: crimes=%d overrides=%d",
            len(latest.get("crimes", [])),
            len(store.list()),
        )
        options_data = _build_options(latest, store)
        log.info("Override options built: count=%d", len(options_data))

        if not options_data:
            await interaction.response.send_message(
                "No low-CPR alerts and no active overrides to manage right now.",
                ephemeral=True,
            )
            return

        view = OverrideSelectView(store, options_data)
        await interaction.response.send_message(
            "Approved members are pre-selected (✓). "
            "Adjust the selection and submit — anything you check will be approved, "
            "anything you uncheck will be removed.",
            view=view,
            ephemeral=True,
        )
    except Exception:
        log.exception("Manage overrides failed")
        raise


def _build_options(alerts_dict: Dict, store: CprOverrideStore) -> List[Dict]:
    """
    Return up to SELECT_OPTION_LIMIT option dicts combining:
      - currently flagged low_cpr alerts (default=False)
      - currently active overrides not present in the alerts (default=True)
    """
    out: List[Dict] = []
    seen = set()

    for crime in alerts_dict.get("crimes", []):
        for entry in crime.get("low_cpr", []):
            key = (crime["id"], entry["user_id"], entry["position"])
            seen.add(key)
            out.append({
                "value": _encode_value(*key),
                "label": _truncate(
                    f"{crime['name']} · {entry['name']} · {entry['position']} "
                    f"({entry['cpr']}/{entry['required']})"
                ),
                "default": False,
                "crime_id": crime["id"],
                "user_id": entry["user_id"],
                "position": entry["position"],
                "crime_name": crime["name"],
                "user_name": entry["name"],
            })

    for o in store.list():
        key = (o["crime_id"], o["user_id"], o["position"])
        if key in seen:
            continue
        crime_name = o.get("crime_name") or "?"
        user_name = o.get("user_name") or f"user_{o['user_id']}"
        out.append({
            "value": _encode_value(*key),
            "label": _truncate(f"✓ {crime_name} · {user_name} · {o['position']}"),
            "default": True,
            "crime_id": o["crime_id"],
            "user_id": o["user_id"],
            "position": o["position"],
            "crime_name": o.get("crime_name", ""),
            "user_name": o.get("user_name", ""),
        })

    return out[:SELECT_OPTION_LIMIT]


def _encode_value(crime_id: int, user_id: int, position: str) -> str:
    return f"{crime_id}|{user_id}|{position}"[:100]


def _truncate(s: str, limit: int = 100) -> str:
    return s if len(s) <= limit else s[: limit - 1] + "…"
