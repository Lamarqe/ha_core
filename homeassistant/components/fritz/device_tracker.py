"""Support for FRITZ!Box devices."""

import datetime
import logging
from typing import Any

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DEFAULT_DEVICE_NAME
from .coordinator import FRITZ_DATA_KEY, AvmWrapper, FritzConfigEntry, FritzData
from .entity import FritzDeviceBase
from .helpers import device_filter_out_from_trackers
from .models import FritzDevice

_LOGGER = logging.getLogger(__name__)

# Coordinator is used to centralize the data updates
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FritzConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up device tracker for FRITZ!Box component."""
    _LOGGER.debug("Starting FRITZ!Box device tracker")
    avm_wrapper = entry.runtime_data
    data_fritz = hass.data[FRITZ_DATA_KEY]

    @callback
    def update_avm_device() -> None:
        """Update the values of AVM device."""
        _async_add_entities(avm_wrapper, async_add_entities, data_fritz)

    entry.async_on_unload(
        async_dispatcher_connect(hass, avm_wrapper.signal_device_new, update_avm_device)
    )

    update_avm_device()


@callback
def _async_add_entities(
    avm_wrapper: AvmWrapper,
    async_add_entities: AddConfigEntryEntitiesCallback,
    data_fritz: FritzData,
) -> None:
    """Add new tracker entities from the AVM device."""

    new_tracked = []
    for mac, device in avm_wrapper.devices.items():
        if device_filter_out_from_trackers(mac, device, data_fritz.tracked.values()):
            continue

        new_tracked.append(FritzBoxTracker(avm_wrapper, device))
        data_fritz.tracked[avm_wrapper.unique_id].add(mac)

    async_add_entities(new_tracked)


class FritzBoxTracker(FritzDeviceBase, ScannerEntity):
    """Class which queries a FRITZ!Box device."""

    _attr_translation_key = "device_tracker"

    def __init__(self, avm_wrapper: AvmWrapper, device: FritzDevice) -> None:
        """Initialize a FRITZ!Box device."""
        super().__init__(avm_wrapper, device)
        self._attr_name: str = device.hostname or DEFAULT_DEVICE_NAME
        self._last_activity: datetime.datetime | None = device.last_activity

    @property
    def is_connected(self) -> bool:
        """Return device status."""
        return self._avm_wrapper.devices[self._mac].is_connected

    @property
    def unique_id(self) -> str:
        """Return device unique id."""
        return f"{self._mac}_tracker"

    @property
    def mac_address(self) -> str:
        """Return mac_address."""
        return self._mac

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the attributes."""
        attrs: dict[str, Any] = {}
        device = self._avm_wrapper.devices[self._mac]
        self._last_activity = device.last_activity
        if self._last_activity is not None:
            attrs["last_time_reachable"] = self._last_activity.isoformat(
                timespec="seconds"
            )
        if device.connected_to:
            attrs["connected_to"] = device.connected_to
        if device.connection_type:
            attrs["connection_type"] = device.connection_type
        if device.ssid:
            attrs["ssid"] = device.ssid
        attrs["ip"] = device.ip_address
        attrs["mac"] = self._mac
        if device.cur_rx_rate is not None:
            attrs["cur_rx_kbps"] = device.cur_rx_rate
        if device.cur_tx_rate is not None:
            attrs["cur_tx_kbps"] = device.cur_tx_rate

        formatted_mac = dr.format_mac(self._mac)
        node_name = next(
            (
                name
                for name, mac in self._avm_wrapper.mesh_nodes.items()
                if mac == formatted_mac
            ),
            None,
        )
        if node_name is not None and formatted_mac != self._avm_wrapper.mac:
            attrs["is_master"] = False
            attrs["fritz_unique_id"] = self._avm_wrapper.unique_id
            attrs["node_name"] = node_name
            attrs["node_mac"] = formatted_mac

        return attrs
