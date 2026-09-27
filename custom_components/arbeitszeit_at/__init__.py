"""Arbeitszeit & Überstunden (AT) – Überstunden, Urlaub und ZA aus einem Kalender."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, DYNAMIC_KEYS, PLATFORMS, SERVICE_REFRESH
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
    return True


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
