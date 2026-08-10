"""Storage sensors for the NVR."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HikvisionConfigEntry, HikvisionCoordinator
from .entity import HikvisionEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    disks = coordinator.data.get("storage", [])
    entities: list[SensorEntity] = []
    for disk in disks:
        entities.append(HikvisionDiskUsage(coordinator, disk["id"], disk["name"]))
        entities.append(HikvisionDiskFree(coordinator, disk["id"], disk["name"]))
    entities.append(HikvisionOnlineChannels(coordinator))
    async_add_entities(entities)


class _DiskSensor(HikvisionEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: HikvisionCoordinator, disk_id: int, disk_name: str
    ) -> None:
        super().__init__(coordinator)
        self._disk_id = disk_id
        self._disk_name = disk_name or f"hdd{disk_id}"

    @property
    def _disk(self) -> dict | None:
        return next(
            (d for d in self.coordinator.data.get("storage", []) if d["id"] == self._disk_id),
            None,
        )

    @property
    def available(self) -> bool:
        return super().available and self._disk is not None


class HikvisionDiskUsage(_DiskSensor):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, disk_id, disk_name) -> None:
        super().__init__(coordinator, disk_id, disk_name)
        self._attr_unique_id = f"{coordinator.device_id}_disk{disk_id}_usage"
        self._attr_name = f"{self._disk_name} usage"

    @property
    def native_value(self) -> float | None:
        disk = self._disk
        return disk["used_percent"] if disk else None

    @property
    def extra_state_attributes(self) -> dict:
        disk = self._disk or {}
        return {"status": disk.get("status"), "property": disk.get("property")}


class HikvisionDiskFree(_DiskSensor):
    _attr_native_unit_of_measurement = UnitOfInformation.MEGABYTES
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_unit_of_measurement = UnitOfInformation.GIGABYTES

    def __init__(self, coordinator, disk_id, disk_name) -> None:
        super().__init__(coordinator, disk_id, disk_name)
        self._attr_unique_id = f"{coordinator.device_id}_disk{disk_id}_free"
        self._attr_name = f"{self._disk_name} free space"

    @property
    def native_value(self) -> int | None:
        disk = self._disk
        return disk["free_mb"] if disk else None


class HikvisionOnlineChannels(HikvisionEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Online channels"

    def __init__(self, coordinator: HikvisionCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_online_channels"

    @property
    def native_value(self) -> int:
        return sum(1 for online in self.coordinator.data["channels"].values() if online)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "offline": [
                cid for cid, online in self.coordinator.data["channels"].items() if not online
            ]
        }
