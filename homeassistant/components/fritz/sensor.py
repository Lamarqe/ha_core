"""AVM FRITZ!Box binary sensors."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from fritzconnection.core.exceptions import FritzConnectionException
from fritzconnection.lib.fritzstatus import FritzStatus
from requests.exceptions import RequestException

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util.dt import utcnow

from .const import DSL_CONNECTION, MeshRoles
from .coordinator import FRITZ_DATA_KEY, AvmWrapper, FritzConfigEntry
from .entity import (
    FritzBoxBaseCoordinatorEntity,
    FritzEntityDescription,
    FritzMeshNodeEntity,
)
from .models import ConnectionInfo

_LOGGER = logging.getLogger(__name__)

# Coordinator is used to centralize the data updates
PARALLEL_UPDATES = 0


def _retrieve_device_uptime_state(
    status: FritzStatus, last_value: datetime | None
) -> datetime:
    """Return uptime from device."""
    return utcnow() - timedelta(seconds=status.device_uptime)


def _retrieve_connection_uptime_state(
    status: FritzStatus, last_value: datetime | None
) -> datetime:
    """Return uptime from connection."""
    return utcnow() - timedelta(seconds=status.connection_uptime)


def _retrieve_external_ip_state(status: FritzStatus, last_value: str) -> str:
    """Return external ip from device."""
    return status.external_ip  # type: ignore[no-any-return]


def _retrieve_external_ipv6_state(status: FritzStatus, last_value: str) -> str:
    """Return external ipv6 from device."""
    return str(status.external_ipv6)


def _retrieve_kb_s_sent_state(status: FritzStatus, last_value: str) -> float:
    """Return upload transmission rate."""
    return round(status.transmission_rate[0] / 1000, 1)  # type: ignore[no-any-return]


def _retrieve_kb_s_received_state(status: FritzStatus, last_value: str) -> float:
    """Return download transmission rate."""
    return round(status.transmission_rate[1] / 1000, 1)  # type: ignore[no-any-return]


def _retrieve_max_kb_s_sent_state(status: FritzStatus, last_value: str) -> float:
    """Return upload max transmission rate."""
    return round(status.max_bit_rate[0] / 1000, 1)  # type: ignore[no-any-return]


def _retrieve_max_kb_s_received_state(status: FritzStatus, last_value: str) -> float:
    """Return download max transmission rate."""
    return round(status.max_bit_rate[1] / 1000, 1)  # type: ignore[no-any-return]


def _retrieve_gb_sent_state(status: FritzStatus, last_value: str) -> float:
    """Return upload total data."""
    return round(status.bytes_sent / 1000 / 1000 / 1000, 1)  # type: ignore[no-any-return]


def _retrieve_gb_received_state(status: FritzStatus, last_value: str) -> float:
    """Return download total data."""
    return round(status.bytes_received / 1000 / 1000 / 1000, 1)  # type: ignore[no-any-return]


def _retrieve_link_kb_s_sent_state(status: FritzStatus, last_value: str) -> float:
    """Return upload link rate."""
    return round(status.max_linked_bit_rate[0] / 1000, 1)  # type: ignore[no-any-return]


def _retrieve_link_kb_s_received_state(status: FritzStatus, last_value: str) -> float:
    """Return download link rate."""
    return round(status.max_linked_bit_rate[1] / 1000, 1)  # type: ignore[no-any-return]


def _retrieve_link_noise_margin_sent_state(
    status: FritzStatus, last_value: str
) -> float:
    """Return upload noise margin."""
    return status.noise_margin[0] / 10  # type: ignore[no-any-return]


def _retrieve_link_noise_margin_received_state(
    status: FritzStatus, last_value: str
) -> float:
    """Return download noise margin."""
    return status.noise_margin[1] / 10  # type: ignore[no-any-return]


def _retrieve_link_attenuation_sent_state(
    status: FritzStatus, last_value: str
) -> float:
    """Return upload line attenuation."""
    return status.attenuation[0] / 10  # type: ignore[no-any-return]


def _retrieve_link_attenuation_received_state(
    status: FritzStatus, last_value: str
) -> float:
    """Return download line attenuation."""
    return status.attenuation[1] / 10  # type: ignore[no-any-return]


def _retrieve_cpu_temperature_state(
    status: FritzStatus, last_value: float | None
) -> float | None:
    """Return the first CPU temperature value."""
    try:
        return status.get_cpu_temperatures()[0]  # type: ignore[no-any-return]
    except RequestException:
        return None


def _is_suitable_cpu_temperature(status: FritzStatus) -> bool:
    """Return whether the CPU temperature sensor is suitable."""
    try:
        cpu_temp = status.get_cpu_temperatures()[0]
    except RequestException, IndexError, FritzConnectionException:
        _LOGGER.debug("CPU temperature not supported by the device")
        return False
    if cpu_temp == 0:
        _LOGGER.debug("CPU temperature returns 0°C, treating as not supported")
        return False
    return True


@dataclass(frozen=True, kw_only=True)
class FritzConnectionSensorEntityDescription(
    SensorEntityDescription, FritzEntityDescription
):
    """Describes Fritz connection sensor entity."""

    is_suitable: Callable[[ConnectionInfo], bool] = lambda info: info.wan_enabled


@dataclass(frozen=True, kw_only=True)
class FritzDeviceSensorEntityDescription(
    SensorEntityDescription, FritzEntityDescription
):
    """Describes Fritz device sensor entity."""

    is_suitable: Callable[[FritzStatus], bool] = lambda status: True


CONNECTION_SENSOR_TYPES: tuple[FritzConnectionSensorEntityDescription, ...] = (
    FritzConnectionSensorEntityDescription(
        key="external_ip",
        translation_key="external_ip",
        value_fn=_retrieve_external_ip_state,
    ),
    FritzConnectionSensorEntityDescription(
        key="external_ipv6",
        translation_key="external_ipv6",
        value_fn=_retrieve_external_ipv6_state,
        is_suitable=lambda info: info.ipv6_active,
    ),
    FritzConnectionSensorEntityDescription(
        key="connection_uptime",
        translation_key="connection_uptime",
        device_class=SensorDeviceClass.UPTIME,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_retrieve_connection_uptime_state,
    ),
    FritzConnectionSensorEntityDescription(
        key="kb_s_sent",
        translation_key="kb_s_sent",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.KILOBYTES_PER_SECOND,
        device_class=SensorDeviceClass.DATA_RATE,
        value_fn=_retrieve_kb_s_sent_state,
    ),
    FritzConnectionSensorEntityDescription(
        key="kb_s_received",
        translation_key="kb_s_received",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.KILOBYTES_PER_SECOND,
        device_class=SensorDeviceClass.DATA_RATE,
        value_fn=_retrieve_kb_s_received_state,
    ),
    FritzConnectionSensorEntityDescription(
        key="max_kb_s_sent",
        translation_key="max_kb_s_sent",
        native_unit_of_measurement=UnitOfDataRate.KILOBITS_PER_SECOND,
        device_class=SensorDeviceClass.DATA_RATE,
        value_fn=_retrieve_max_kb_s_sent_state,
    ),
    FritzConnectionSensorEntityDescription(
        key="max_kb_s_received",
        translation_key="max_kb_s_received",
        native_unit_of_measurement=UnitOfDataRate.KILOBITS_PER_SECOND,
        device_class=SensorDeviceClass.DATA_RATE,
        value_fn=_retrieve_max_kb_s_received_state,
    ),
    FritzConnectionSensorEntityDescription(
        key="gb_sent",
        translation_key="gb_sent",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        value_fn=_retrieve_gb_sent_state,
    ),
    FritzConnectionSensorEntityDescription(
        key="gb_received",
        translation_key="gb_received",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        value_fn=_retrieve_gb_received_state,
    ),
    FritzConnectionSensorEntityDescription(
        key="link_kb_s_sent",
        translation_key="link_kb_s_sent",
        native_unit_of_measurement=UnitOfDataRate.KILOBITS_PER_SECOND,
        device_class=SensorDeviceClass.DATA_RATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_retrieve_link_kb_s_sent_state,
    ),
    FritzConnectionSensorEntityDescription(
        key="link_kb_s_received",
        translation_key="link_kb_s_received",
        native_unit_of_measurement=UnitOfDataRate.KILOBITS_PER_SECOND,
        device_class=SensorDeviceClass.DATA_RATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_retrieve_link_kb_s_received_state,
    ),
    FritzConnectionSensorEntityDescription(
        key="link_noise_margin_sent",
        translation_key="link_noise_margin_sent",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_retrieve_link_noise_margin_sent_state,
        is_suitable=lambda info: info.wan_enabled and info.connection == DSL_CONNECTION,
    ),
    FritzConnectionSensorEntityDescription(
        key="link_noise_margin_received",
        translation_key="link_noise_margin_received",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_retrieve_link_noise_margin_received_state,
        is_suitable=lambda info: info.wan_enabled and info.connection == DSL_CONNECTION,
    ),
    FritzConnectionSensorEntityDescription(
        key="link_attenuation_sent",
        translation_key="link_attenuation_sent",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_retrieve_link_attenuation_sent_state,
        is_suitable=lambda info: info.wan_enabled and info.connection == DSL_CONNECTION,
    ),
    FritzConnectionSensorEntityDescription(
        key="link_attenuation_received",
        translation_key="link_attenuation_received",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_retrieve_link_attenuation_received_state,
        is_suitable=lambda info: info.wan_enabled and info.connection == DSL_CONNECTION,
    ),
)

DEVICE_SENSOR_TYPES: tuple[FritzDeviceSensorEntityDescription, ...] = (
    FritzDeviceSensorEntityDescription(
        key="device_uptime",
        device_class=SensorDeviceClass.UPTIME,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_retrieve_device_uptime_state,
    ),
    FritzDeviceSensorEntityDescription(
        key="cpu_temperature",
        translation_key="cpu_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_retrieve_cpu_temperature_state,
        is_suitable=_is_suitable_cpu_temperature,
    ),
)


@dataclass(frozen=True, kw_only=True)
class FritzMeshNodeSensorEntityDescription(SensorEntityDescription):
    """Describes a Fritz mesh node sensor entity."""

    value_fn: Callable[..., StateType]


def _mesh_connected_devices(wrapper: FritzMeshNodeSensor, node_name: str) -> int:
    return sum(
        1
        for d in wrapper.coordinator.devices.values()
        if d.connected_to == node_name and d.is_connected
    )


MESH_NODE_SENSOR_TYPES: tuple[FritzMeshNodeSensorEntityDescription, ...] = (
    FritzMeshNodeSensorEntityDescription(
        key="mesh_connected_devices",
        translation_key="mesh_connected_devices",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_mesh_connected_devices,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FritzConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up entry."""
    _LOGGER.debug("Setting up FRITZ!Box sensors")
    avm_wrapper = entry.runtime_data

    connection_info = await avm_wrapper.async_get_connection_info()
    entities: list[FritzBoxSensor | FritzMeshNodeSensor] = [
        FritzBoxSensor(avm_wrapper, entry.title, description)
        for description in CONNECTION_SENSOR_TYPES
        if description.is_suitable(connection_info)
    ]

    fritz_status = avm_wrapper.fritz_status

    def _generate_device_sensors() -> list[FritzBoxSensor]:
        return [
            FritzBoxSensor(avm_wrapper, entry.title, description)
            for description in DEVICE_SENSOR_TYPES
            if description.is_suitable(fritz_status)
        ]

    entities += await hass.async_add_executor_job(_generate_device_sensors)

    fritz_data = hass.data[FRITZ_DATA_KEY]

    if (
        fritz_status.device_has_mesh_support
        and connection_info.mesh_role == MeshRoles.MASTER
    ):
        fritz_data.mesh_node_sensors.setdefault(entry.entry_id, set())

        for node_name, node_mac in avm_wrapper.mesh_nodes.items():
            for node_desc in MESH_NODE_SENSOR_TYPES:
                sensor_key = f"{node_mac}_{node_desc.key}"
                if sensor_key not in fritz_data.mesh_node_sensors[entry.entry_id]:
                    fritz_data.mesh_node_sensors[entry.entry_id].add(sensor_key)
                    entities.append(
                        FritzMeshNodeSensor(avm_wrapper, node_name, node_mac, node_desc)
                    )

        @callback
        def add_mesh_node_sensors() -> None:
            new_entities: list[FritzMeshNodeSensor] = []
            for node_name, node_mac in avm_wrapper.mesh_nodes.items():
                for node_desc in MESH_NODE_SENSOR_TYPES:
                    sensor_key = f"{node_mac}_{node_desc.key}"
                    if sensor_key not in fritz_data.mesh_node_sensors[entry.entry_id]:
                        fritz_data.mesh_node_sensors[entry.entry_id].add(sensor_key)
                        new_entities.append(
                            FritzMeshNodeSensor(
                                avm_wrapper, node_name, node_mac, node_desc
                            )
                        )
            async_add_entities(new_entities)

        entry.async_on_unload(
            async_dispatcher_connect(
                hass, avm_wrapper.signal_mesh_node_new, add_mesh_node_sensors
            )
        )

    async_add_entities(entities)


class FritzBoxSensor(FritzBoxBaseCoordinatorEntity, SensorEntity):
    """Define FRITZ!Box connectivity class."""

    entity_description: (
        FritzConnectionSensorEntityDescription | FritzDeviceSensorEntityDescription
    )

    @property
    def native_value(self) -> StateType:
        """Return the value reported by the sensor."""
        return self.coordinator.data["entity_states"].get(self.entity_description.key)


class FritzMeshNodeSensor(FritzMeshNodeEntity, SensorEntity):
    """Sensor for aggregate metrics of a FRITZ!Box mesh node."""

    entity_description: FritzMeshNodeSensorEntityDescription

    def __init__(
        self,
        avm_wrapper: AvmWrapper,
        node_name: str,
        node_mac: str,
        description: FritzMeshNodeSensorEntityDescription,
    ) -> None:
        """Initialize mesh node sensor."""
        super().__init__(avm_wrapper, node_name, node_mac)
        self.entity_description = description
        self._attr_unique_id = f"{node_mac}_{description.key}"
        self._is_master = node_mac == avm_wrapper.mac

    @property
    def native_value(self) -> StateType:
        """Return the value reported by the sensor."""
        return self.entity_description.value_fn(self, self._node_name)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes for the connected-devices sensor."""
        devices = self._avm_wrapper.devices
        node_devices = [
            d
            for d in devices.values()
            if d.connected_to == self._node_name and d.is_connected
        ]
        rx_kbps: int | None = (
            sum(d.cur_rx_rate for d in node_devices if d.cur_rx_rate is not None)
            or None
        )
        tx_kbps: int | None = (
            sum(d.cur_tx_rate for d in node_devices if d.cur_tx_rate is not None)
            or None
        )
        wifi_count = sum(1 for d in node_devices if d.connection_type.upper() == "WLAN")
        lan_count = sum(1 for d in node_devices if d.connection_type.upper() == "LAN")
        return {
            "node_name": self._node_name,
            "is_master": self._is_master,
            "fritz_unique_id": self._avm_wrapper.unique_id,
            "fritz_host": self._avm_wrapper.host,
            "node_mac": self._node_mac,
            "wifi_devices": wifi_count,
            "lan_devices": lan_count,
            "rx_rate_kbps": rx_kbps,
            "tx_rate_kbps": tx_kbps,
        }
