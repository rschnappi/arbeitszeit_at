"""Freitext für ausbezahlte Überstunden."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.core import HomeAssistant
try:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
except ImportError:  # HA < 2025.2
    from homeassistant.helpers.entity_platform import AddEntitiesCallback as AddConfigEntryEntitiesCallback

from . import ArbeitszeitConfigEntry
from .const import CONF_PAYOUTS
from .coordinator import merged_config
from .entity import ArbeitszeitEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ArbeitszeitConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([PayoutText(entry.runtime_data)])


class PayoutText(ArbeitszeitEntity, TextEntity):
    _attr_icon = "mdi:cash-clock"
    _attr_native_max = 255

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "text", "ausbezahlter_za", "Ausbezahlter ZA (z.B. Mai: 20h, Aug 16h)")

    @property
    def native_value(self) -> str:
        return merged_config(self.coordinator.config_entry).get(CONF_PAYOUTS) or ""

    async def async_set_value(self, value: str) -> None:
        entry = self.coordinator.config_entry
        self.hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_PAYOUTS: value})
        self.async_write_ha_state()
