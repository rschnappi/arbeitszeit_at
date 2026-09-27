"""Editierbare Werte: Startsaldo und Resturlaub-Stand."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
try:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
except ImportError:  # HA < 2025.2
    from homeassistant.helpers.entity_platform import AddEntitiesCallback as AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ArbeitszeitConfigEntry
from .const import CONF_LEAVE_BALANCE, CONF_LEAVE_BALANCE_DATE, CONF_START_BALANCE
from .coordinator import merged_config
from .entity import ArbeitszeitEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ArbeitszeitConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities([
        OptionNumber(c, "uberstunden_startsaldo", "Überstunden Startsaldo / Übertrag", CONF_START_BALANCE,
                     "h", -1000, 1000, 0.25, "mdi:clock-start"),
        OptionNumber(c, "resturlaub_stand", "Resturlaub Stand (Lohnzettel)", CONF_LEAVE_BALANCE,
                     "Tage", 0, 365, 0.5, "mdi:palm-tree", stamp_key=CONF_LEAVE_BALANCE_DATE),
    ])


class OptionNumber(ArbeitszeitEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, key, name, option, unit, vmin, vmax, step, icon, stamp_key=None):
        super().__init__(coordinator, "number", key, name)
        self._option = option
        self._stamp_key = stamp_key
        self._attr_native_unit_of_measurement = unit
        self._attr_native_min_value = vmin
        self._attr_native_max_value = vmax
        self._attr_native_step = step
        self._attr_icon = icon

    @property
    def native_value(self) -> float:
        return float(merged_config(self.coordinator.config_entry)[self._option])

    @property
    def extra_state_attributes(self):
        if self._stamp_key:
            return {"stand_datum": merged_config(self.coordinator.config_entry).get(self._stamp_key)}
        return None

    async def async_set_native_value(self, value: float) -> None:
        entry = self.coordinator.config_entry
        opts = {**entry.options, self._option: value}
        if self._stamp_key:  # neuer Lohnzettel-Stand gilt ab heute
            opts[self._stamp_key] = dt_util.now().date().isoformat()
        self.hass.config_entries.async_update_entry(entry, options=opts)
        self.async_write_ha_state()
