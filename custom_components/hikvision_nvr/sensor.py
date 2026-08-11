"""Storage sensors for the NVR."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import HikvisionConfigEntry, HikvisionCoordinator
from .entity import HikvisionEntity

# Boot time is derived from an uptime counter and a local clock, so it wanders
# by a second or two. Anything smaller than this is noise, not a reboot.
UPTIME_DRIFT_TOLERANCE = 120


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

    # Only offered when the firmware actually reports them.
    system = coordinator.data.get("system", {})
    if "uptime_seconds" in system:
        entities.append(HikvisionUptime(coordinator))
    if "cpu_percent" in system:
        entities.append(HikvisionCpuUsage(coordinator))
    if "memory_used_mb" in system:
        entities.append(HikvisionMemoryUsed(coordinator))

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


class _SystemSensor(HikvisionEntity, SensorEntity):
    """A value from /ISAPI/System/status, reported against the NVR device."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def _system(self) -> dict:
        return self.coordinator.data.get("system", {})

    @property
    def available(self) -> bool:
        return super().available and bool(self._system)


class HikvisionUptime(_SystemSensor):
    """When the NVR last started.

    Reported as the boot time rather than a rising counter: a timestamp is
    stable, so it does not write a new state every poll, and Home Assistant
    renders it as "3 days ago" on its own.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "uptime"

    def __init__(self, coordinator: HikvisionCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_uptime"
        self._started_at: datetime | None = None

    @property
    def native_value(self) -> datetime | None:
        seconds = self._system.get("uptime_seconds")
        if seconds is None:
            return self._started_at
        started = dt_util.utcnow() - timedelta(seconds=seconds)
        # Only move the reported boot time when it really moved. Recomputing it
        # from a live clock otherwise jitters by a second on every poll and
        # fills the recorder with meaningless state changes.
        if self._started_at is None or abs(
            (started - self._started_at).total_seconds()
        ) > UPTIME_DRIFT_TOLERANCE:
            self._started_at = started
        return self._started_at


class HikvisionCpuUsage(_SystemSensor):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "cpu_usage"

    def __init__(self, coordinator: HikvisionCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_cpu_usage"

    @property
    def native_value(self) -> float | None:
        return self._system.get("cpu_percent")


class HikvisionMemoryUsed(_SystemSensor):
    _attr_native_unit_of_measurement = UnitOfInformation.MEGABYTES
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "memory_used"

    def __init__(self, coordinator: HikvisionCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_memory_used"

    @property
    def native_value(self) -> float | None:
        return self._system.get("memory_used_mb")

    @property
    def extra_state_attributes(self) -> dict:
        return {"available_mb": self._system.get("memory_available_mb")}
