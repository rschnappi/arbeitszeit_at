"""Arbeitszeit & Überstunden (AT) – Überstunden, Urlaub und ZA aus einem Kalender."""

from __future__ import annotations

from datetime import date

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_PAYOUT_ENTRIES, DOMAIN, DYNAMIC_KEYS, PLATFORMS, SERVICE_ADD_PAYOUT, SERVICE_REFRESH,
    SERVICE_REMOVE_PAYOUT,
)
from .coordinator import ArbeitszeitCoordinator

type ArbeitszeitConfigEntry = ConfigEntry[ArbeitszeitCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ArbeitszeitConfigEntry) -> bool:
    coordinator = ArbeitszeitCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):

        async def _refresh(call: ServiceCall) -> None:
            for e in hass.config_entries.async_loaded_entries(DOMAIN):
                await e.runtime_data.async_request_refresh()

        hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)

    if not hass.services.has_service(DOMAIN, SERVICE_ADD_PAYOUT):

        async def _add(call: ServiceCall) -> None:
            _payout_service(hass, call, True)

        async def _remove(call: ServiceCall) -> None:
            _payout_service(hass, call, False)

        hass.services.async_register(DOMAIN, SERVICE_ADD_PAYOUT, _add, schema=PAYOUT_SCHEMA)
        hass.services.async_register(DOMAIN, SERVICE_REMOVE_PAYOUT, _remove, schema=PAYOUT_REMOVE_SCHEMA)
    return True


PAYOUT_SCHEMA = vol.Schema({
    vol.Required("datum"): cv.date,
    vol.Required("stunden"): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000)),
    vol.Optional("config_entry_id"): cv.string,
})
PAYOUT_REMOVE_SCHEMA = vol.Schema({
    vol.Required("datum"): cv.date,
    vol.Optional("config_entry_id"): cv.string,
})


def _payout_service(hass: HomeAssistant, call: ServiceCall, add: bool) -> None:
    """Auszahlung mit Datum erfassen/entfernen. Gleiches Datum beim Erfassen -> Wert ersetzen."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if eid := call.data.get("config_entry_id"):
        entries = [e for e in entries if e.entry_id == eid]
    if len(entries) != 1:
        raise ServiceValidationError(
            "config_entry_id angeben" if entries else "Keine passende Arbeitszeit-Integration gefunden")
    entry = entries[0]
    day: date = call.data["datum"]
    items = [dict(i) for i in entry.options.get(CONF_PAYOUT_ENTRIES) or entry.data.get(CONF_PAYOUT_ENTRIES) or []]
    items = [i for i in items if i.get("datum") != day.isoformat()]
    if add:
        items.append({"datum": day.isoformat(), "stunden": float(call.data["stunden"])})
    items.sort(key=lambda i: i["datum"])
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_PAYOUT_ENTRIES: items})


async def _async_update_listener(hass: HomeAssistant, entry: ArbeitszeitConfigEntry) -> None:
    """Nur dynamische Werte geändert -> Refresh, sonst Reload."""
    coordinator = entry.runtime_data
    new = dict(entry.options)
    old = coordinator.last_options
    changed = {k for k in set(old) | set(new) if old.get(k) != new.get(k)}
    coordinator.last_options = new
    if changed and changed <= DYNAMIC_KEYS:
        await coordinator.async_request_refresh()
    elif changed:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ArbeitszeitConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
