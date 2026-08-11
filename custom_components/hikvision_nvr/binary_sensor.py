"""Binary sensors driven by the NVR's push event stream."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_CHANNELS,
    CONF_EVENTS,
    DEFAULT_ENABLED_EVENTS,
    EVENT_AUTO_OFF,
    EVENT_CLASSES,
    SIGNAL_EVENT,
)
from .coordinator import HikvisionConfigEntry, HikvisionCoordinator
from .entity import HikvisionEntity
from .isapi import Channel, Event

# Used only when the device does not expose /ISAPI/Event/triggers, so that
# motion still works on firmwares that declare nothing.
FALLBACK_EVENTS = frozenset({"VMD"})

# A disk the NVR is happy with reports one of these. Anything else -- "error",
# "unformatted", "sleeping", a mismatch -- is worth telling the user about.
HEALTHY_DISK_STATUS = frozenset({"ok", "idle"})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    enabled = entry.options.get(CONF_CHANNELS)
    channels = {
        c.id: c
        for c in coordinator.api.channels
        if enabled is None or str(c.id) in enabled
    }

    # Which event types the user wants at all. Unset means "the sensible
    # default set"; an explicit empty list means "none, thank you".
    chosen = entry.options.get(CONF_EVENTS)
    wanted = set(chosen) if chosen is not None else set(EVENT_CLASSES)

    known: set[tuple[int, str]] = set()
    entities: list[BinarySensorEntity] = []

    for channel in channels.values():
        entities.append(HikvisionChannelOnline(coordinator, channel))
        for event_type in sorted(
            ((channel.events & EVENT_CLASSES.keys()) or FALLBACK_EVENTS) & wanted
        ):
            known.add((channel.id, event_type))
            entities.append(
                HikvisionBinarySensor(
                    coordinator, channel, event_type, enabled_by_default=chosen is not None
                )
            )

    # Disk health, straight from the NVR's own assessment.
    for disk in coordinator.data.get("storage", []):
        entities.append(HikvisionDiskProblem(coordinator, disk["id"], disk["name"]))

    # Channel 0 is the NVR itself: disk, network, recording failures.
    for event_type in sorted(
        coordinator.api.event_capabilities.get(0, set()) & EVENT_CLASSES.keys() & wanted
    ):
        known.add((0, event_type))
        entities.append(
            HikvisionBinarySensor(
                coordinator, None, event_type, enabled_by_default=chosen is not None
            )
        )

    async_add_entities(entities)

    @callback
    def _on_event(event: Event) -> None:
        """Create an entity the first time an event type shows up."""
        key = ((event.channel or 0), event.type)
        if key in known or event.type not in EVENT_CLASSES or event.type not in wanted:
            return
        channel = channels.get(event.channel or 0)
        if event.channel and channel is None:
            return  # event for a channel the user chose not to expose
        known.add(key)
        async_add_entities(
            [HikvisionBinarySensor(coordinator, channel, event.type)]
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_EVENT}_{coordinator.device_id}", _on_event
        )
    )


class HikvisionBinarySensor(HikvisionEntity, BinarySensorEntity):
    """One event type on one channel (or on the NVR itself)."""

    def __init__(
        self,
        coordinator: HikvisionCoordinator,
        channel: Channel | None,
        event_type: str,
        enabled_by_default: bool = False,
    ) -> None:
        super().__init__(coordinator, channel)
        self._event_type = event_type
        self._channel_id = channel.id if channel else 0
        self._attr_unique_id = (
            f"{coordinator.device_id}_{self._channel_id}_{event_type}"
        )
        # Naming comes from translations, so the entity reads "Line crossing"
        # rather than "Linedetection".
        self._attr_translation_key = event_type.lower()
        device_class = EVENT_CLASSES.get(event_type)
        if device_class:
            self._attr_device_class = BinarySensorDeviceClass(device_class)
        # A capable device declares a lot. Create everything it can do, but only
        # switch on the ones most people want, so the entity list stays usable.
        self._attr_entity_registry_enabled_default = (
            enabled_by_default or event_type in DEFAULT_ENABLED_EVENTS or channel is None
        )
        if channel is None:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_event_active(self._channel_id, self._event_type)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_EVENT}_{self.coordinator.device_id}",
                self._handle_event,
            )
        )

    @callback
    def _handle_event(self, event: Event) -> None:
        if event.type != self._event_type or (event.channel or 0) != self._channel_id:
            return
        self.async_write_ha_state()
        if event.active:
            # Nothing else would clear us if the device never sends "inactive".
            # The callback must be decorated: an undecorated one is treated as a
            # blocking job and run in an executor thread, and writing state off
            # the event loop is not safe.
            async_call_later(
                self.hass, EVENT_AUTO_OFF.total_seconds() + 1, self._refresh_state
            )

    @callback
    def _refresh_state(self, _now) -> None:
        """Re-evaluate is_on once the auto-off window has passed."""
        self.async_write_ha_state()


class HikvisionChannelOnline(HikvisionEntity, BinarySensorEntity):
    """Is this camera actually reachable?

    The NVR probes every channel itself and reports the result, which is a
    truer answer than an ICMP ping from Home Assistant: a camera can answer
    ping while its stream is dead, and it may sit on a PoE subnet Home
    Assistant cannot reach at all.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connectivity"

    def __init__(self, coordinator: HikvisionCoordinator, channel: Channel) -> None:
        super().__init__(coordinator, channel)
        self._attr_unique_id = f"{coordinator.device_id}_{channel.id}_online"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data["channels"].get(self.channel.id, False))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "channel": self.channel.id,
            "camera_ip": self.channel.ip_address,
            "streams": self.channel.streams,
        }



class HikvisionDiskProblem(HikvisionEntity, BinarySensorEntity):
    """Whether a disk needs attention, per the NVR's own status field."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "disk_problem"

    def __init__(
        self, coordinator: HikvisionCoordinator, disk_id: int, disk_name: str
    ) -> None:
        super().__init__(coordinator)
        self._disk_id = disk_id
        self._attr_unique_id = f"{coordinator.device_id}_disk{disk_id}_problem"
        self._attr_translation_placeholders = {"disk": disk_name or f"hdd{disk_id}"}

    @property
    def _disk(self) -> dict | None:
        return next(
            (
                d
                for d in self.coordinator.data.get("storage", [])
                if d["id"] == self._disk_id
            ),
            None,
        )

    @property
    def available(self) -> bool:
        return super().available and self._disk is not None

    @property
    def is_on(self) -> bool:
        disk = self._disk
        return bool(disk) and disk.get("status") not in HEALTHY_DISK_STATUS

    @property
    def extra_state_attributes(self) -> dict:
        disk = self._disk or {}
        return {
            "status": disk.get("status"),
            "property": disk.get("property"),
            "used_percent": disk.get("used_percent"),
        }
