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

    known: set[tuple[int, str]] = set()
    entities: list[BinarySensorEntity] = []

    for channel in channels.values():
        entities.append(HikvisionChannelOnline(coordinator, channel))
        for event_type in sorted(
            (channel.events & EVENT_CLASSES.keys()) or FALLBACK_EVENTS
        ):
            known.add((channel.id, event_type))
            entities.append(HikvisionBinarySensor(coordinator, channel, event_type))

    # Channel 0 is the NVR itself: disk, network, recording failures.
    for event_type in sorted(
        coordinator.api.event_capabilities.get(0, set()) & EVENT_CLASSES.keys()
    ):
        known.add((0, event_type))
        entities.append(HikvisionBinarySensor(coordinator, None, event_type))

    async_add_entities(entities)

    @callback
    def _on_event(event: Event) -> None:
        """Create an entity the first time an event type shows up."""
        key = ((event.channel or 0), event.type)
        if key in known or event.type not in EVENT_CLASSES:
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
            event_type in DEFAULT_ENABLED_EVENTS or channel is None
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
            async_call_later(
                self.hass,
                EVENT_AUTO_OFF.total_seconds() + 1,
                lambda _now: self.async_write_ha_state(),
            )


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
