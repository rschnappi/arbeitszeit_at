"""Gemeinsame Basis."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ArbeitszeitCoordinator


class ArbeitszeitEntity(CoordinatorEntity[ArbeitszeitCoordinator]):
    _attr_has_entity_name = False

    def __init__(self, coordinator: ArbeitszeitCoordinator, domain: str, key: str, name: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._key = key
        # feste entity_ids -> bestehende Dashboards funktionieren weiter
        self.entity_id = f"{domain}.{key}"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Arbeitszeit",
            manufacturer="arbeitszeit_at",
            model=entry.title,
            entry_type=DeviceEntryType.SERVICE,
        )
