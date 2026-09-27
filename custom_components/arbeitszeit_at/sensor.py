"""Sensoren."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
try:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
except ImportError:  # HA < 2025.2
    from homeassistant.helpers.entity_platform import AddEntitiesCallback as AddConfigEntryEntitiesCallback

from . import ArbeitszeitConfigEntry
from .entity import ArbeitszeitEntity

H = "h"
D = "Tage"


@dataclass(frozen=True)
class Desc:
    key: str
    name: str
    unit: str
    icon: str


SENSORS: tuple[Desc, ...] = (
    Desc("uberstunden_woche", "Überstunden (Woche bisher)", H, "mdi:timer-plus-outline"),
    Desc("uberstunden_monat", "Überstunden (Monat bisher)", H, "mdi:calendar-clock"),
    Desc("uberstunden_jahr", "Überstunden (Jahr)", H, "mdi:chart-timeline-variant"),
    Desc("uberstunden_saldo_netto", "Überstunden Saldo netto", H, "mdi:scale-balance"),
    Desc("arbeitszeit_woche_ist", "Arbeitszeit Woche (Ist)", H, "mdi:clock-check-outline"),
    Desc("arbeitszeit_monat_ist", "Arbeitszeit Monat (Ist)", H, "mdi:briefcase-check"),
    Desc("arbeitszeit_jahr_ist", "Arbeitszeit Jahr (Ist)", H, "mdi:clock-end"),
    Desc("urlaub_rest", "Resturlaub", D, "mdi:palm-tree"),
    Desc("urlaub_jahr", "Urlaub Kalenderjahr", D, "mdi:beach"),
    Desc("urlaub_monat", "Urlaub Monat", D, "mdi:beach"),
    Desc("za_jahr", "Zeitausgleich Jahr", D, "mdi:briefcase-clock-outline"),
    Desc("pflegeurlaub_jahr", "Pflegeurlaub Jahr", D, "mdi:hospital-box-outline"),
    Desc("krankenstand_jahr", "Krankenstand Jahr", D, "mdi:emoticon-sick-outline"),
    Desc("za_ausbezahlt_jahr", "Ausbezahlter ZA / Überstunden (Jahr)", H, "mdi:cash-clock"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ArbeitszeitConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(ArbeitszeitSensor(coordinator, d) for d in SENSORS)


class ArbeitszeitSensor(ArbeitszeitEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, desc: Desc) -> None:
        super().__init__(coordinator, "sensor", desc.key, desc.name)
        self._attr_native_unit_of_measurement = desc.unit
        self._attr_icon = desc.icon

    @property
    def _data(self) -> dict:
        return (self.coordinator.data or {}).get(self._key, {})

    @property
    def native_value(self):
        return self._data.get("state")

    @property
    def extra_state_attributes(self):
        return self._data.get("attributes")
