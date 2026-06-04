"""Tests for FritzMeshTopology (fritz/mesh.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.fritz.models import Device
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr


async def test_wifi_client_connected_to_slave_ap(
    fritz_tools,
) -> None:
    """Non-meshed WiFi client gets connected_to, connection_type and rate from slave AP."""

    hosts = {"AA:BB:CC:DD:EE:04": MagicMock(wan_access=True)}
    topology = {
        "nodes": [
            {
                "is_meshed": True,
                "mesh_role": "master",
                "device_name": "fritz.box",
                "device_mac_address": "1CED6F123411",
                "node_interfaces": [
                    {
                        "uid": "master-intf",
                        "mac_address": fritz_tools.unique_id,
                        "op_mode": "",
                        "ssid": None,
                        "type": "LAN",
                        "name": "LAN:1",
                        "node_links": [],
                    }
                ],
            },
            {
                "is_meshed": True,
                "mesh_role": "slave",
                "device_name": "slave-ap",
                "device_mac_address": "AA:BB:CC:DD:EE:02",
                "node_interfaces": [
                    {
                        "uid": "slave-ap-2g",
                        "mac_address": "AA:BB:CC:DD:EE:02",
                        "op_mode": "AP",
                        "ssid": "TestSSID",
                        "type": "WLAN",
                        "name": "AP:2G:0",
                        "node_links": [],
                    }
                ],
            },
            {
                "is_meshed": False,
                "node_interfaces": [
                    {
                        "mac_address": "AA:BB:CC:DD:EE:04",
                        "node_links": [
                            {
                                "state": "CONNECTED",
                                "node_interface_1_uid": "slave-ap-2g",
                                "node_interface_2_uid": "client-intf",
                                "cur_data_rate_rx": 72000,
                                "cur_data_rate_tx": 36000,
                            }
                        ],
                    }
                ],
            },
        ]
    }

    with (
        patch.object(
            fritz_tools, "_async_update_hosts_info", AsyncMock(return_value=hosts)
        ),
        patch.object(
            fritz_tools.fritz_hosts,
            "get_mesh_topology",
            MagicMock(return_value=topology),
        ),
        patch.object(fritz_tools, "manage_device_info", return_value=False) as manage,
        patch.object(fritz_tools, "async_send_signal_device_update", AsyncMock()),
    ):
        await fritz_tools.async_scan_devices()

    dev_info = manage.call_args.args[0]
    assert dev_info.connected_to == "slave-ap"
    assert dev_info.connection_type == "WLAN"
    assert dev_info.cur_rx_rate == 72000


async def test_lan_backhaul_slave_no_connected_to_without_switch_node(
    fritz_tools,
) -> None:
    """LAN-backhaul slave leaves connected_to unset when no switch node is present.

    When a switch node with device_class='NETWORK_SWITCH' is present in the topology its
    interfaces are added to mesh_intf, allowing the fallback scan to resolve the
    slave's uplink.  This test covers the degenerate case where the topology
    contains only the raw switch-port link without a recognised switch node.
    """

    slave_dev = Device(
        connected=True,
        connected_to="",
        connection_type="",
        ip_address="192.168.1.5",
        name="slave-ap",
        ssid=None,
    )
    hosts = {"AA:BB:CC:DD:EE:01": slave_dev}
    topology = {
        "nodes": [
            {
                "is_meshed": True,
                "mesh_role": "master",
                "device_name": "fritz.box",
                "device_mac_address": "1CED6F123411",
                "node_interfaces": [
                    {
                        "uid": "master-intf",
                        "mac_address": fritz_tools.unique_id,
                        "op_mode": "",
                        "ssid": None,
                        "type": "LAN",
                        "name": "LAN:1",
                        "node_links": [],
                    }
                ],
            },
            {
                "is_meshed": True,
                "mesh_role": "slave",
                "device_name": "slave-ap",
                "device_mac_address": "AA:BB:CC:DD:EE:01",
                "node_interfaces": [
                    {
                        "uid": "slave-lan-intf",
                        "mac_address": "AA:BB:CC:DD:EE:01",
                        "op_mode": "",
                        "ssid": None,
                        "type": "LAN",
                        "name": "LAN:1",
                        "node_links": [
                            {
                                "state": "CONNECTED",
                                "node_interface_1_uid": "slave-lan-intf",
                                "node_interface_2_uid": "switch-port",
                                "cur_data_rate_rx": 1000000,
                                "cur_data_rate_tx": 1000000,
                            }
                        ],
                    },
                    {
                        "uid": "slave-ap-2g",
                        "mac_address": "AA:BB:CC:DD:EE:02",
                        "op_mode": "AP",
                        "ssid": "TestSSID",
                        "type": "WLAN",
                        "name": "AP:2G:0",
                        "node_links": [],
                    },
                ],
            },
        ]
    }

    with (
        patch.object(
            fritz_tools, "_async_update_hosts_info", AsyncMock(return_value=hosts)
        ),
        patch.object(
            fritz_tools.fritz_hosts,
            "get_mesh_topology",
            MagicMock(return_value=topology),
        ),
        patch.object(fritz_tools, "manage_device_info", return_value=False),
        patch.object(fritz_tools, "async_send_signal_device_update", AsyncMock()),
    ):
        await fritz_tools.async_scan_devices()

    assert slave_dev.connected_to == ""


async def test_multiple_slaves_registered_signal_fires_once(
    hass: HomeAssistant,
    fritz_tools,
) -> None:
    """Two new mesh slaves are both registered and the new-node signal fires exactly once."""

    topology = {
        "nodes": [
            {
                "is_meshed": True,
                "mesh_role": "master",
                "device_name": "fritz.box",
                "device_mac_address": "1CED6F123411",
                "node_interfaces": [
                    {
                        "uid": "master-intf",
                        "mac_address": fritz_tools.unique_id,
                        "op_mode": "",
                        "ssid": None,
                        "type": "LAN",
                        "name": "LAN:1",
                        "node_links": [],
                    }
                ],
            },
            {
                "is_meshed": True,
                "mesh_role": "slave",
                "device_name": "slave-ap-1",
                "device_mac_address": "AA:BB:CC:DD:EE:01",
                "node_interfaces": [
                    {
                        "uid": "slave-1-lan",
                        "mac_address": "AA:BB:CC:DD:EE:01",
                        "op_mode": "",
                        "ssid": None,
                        "type": "LAN",
                        "name": "LAN:1",
                        "node_links": [],
                    }
                ],
            },
            {
                "is_meshed": True,
                "mesh_role": "slave",
                "device_name": "slave-ap-2",
                "device_mac_address": "AA:BB:CC:DD:EE:11",
                "node_interfaces": [
                    {
                        "uid": "slave-2-lan",
                        "mac_address": "AA:BB:CC:DD:EE:11",
                        "op_mode": "",
                        "ssid": None,
                        "type": "LAN",
                        "name": "LAN:1",
                        "node_links": [],
                    }
                ],
            },
        ]
    }

    with (
        patch.object(
            fritz_tools, "_async_update_hosts_info", AsyncMock(return_value={})
        ),
        patch.object(
            fritz_tools.fritz_hosts,
            "get_mesh_topology",
            MagicMock(return_value=topology),
        ),
        patch.object(fritz_tools, "async_send_signal_device_update", AsyncMock()),
        patch(
            "homeassistant.components.fritz.mesh.async_dispatcher_send"
        ) as dispatch_mock,
    ):
        await fritz_tools.async_scan_devices()

    device_registry = dr.async_get(hass)
    assert (
        device_registry.async_get_device(
            connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:01")}
        )
        is not None
    )
    assert (
        device_registry.async_get_device(
            connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:11")}
        )
        is not None
    )
    dispatch_mock.assert_called_once_with(hass, fritz_tools.signal_mesh_node_new)


async def test_client_connected_to_switch(
    fritz_tools,
) -> None:
    """Non-meshed client behind a LAN switch gets connected_to set to the switch key."""

    hosts = {"AA:BB:CC:DD:EE:04": MagicMock(wan_access=True)}
    topology = {
        "nodes": [
            {
                "is_meshed": True,
                "mesh_role": "master",
                "device_name": "fritz.box",
                "device_mac_address": "1CED6F123411",
                "node_interfaces": [
                    {
                        "uid": "master-lan1",
                        "mac_address": fritz_tools.unique_id,
                        "op_mode": "",
                        "ssid": None,
                        "type": "LAN",
                        "name": "LAN:1",
                        "node_links": [
                            {
                                "state": "CONNECTED",
                                "node_interface_1_uid": "master-lan1",
                                "node_interface_2_uid": "switch-uplink",
                                "cur_data_rate_rx": 1000000,
                                "cur_data_rate_tx": 1000000,
                            }
                        ],
                    }
                ],
            },
            {
                "is_meshed": False,
                "device_class": "NETWORK_SWITCH",
                "device_name": "Switch",
                "device_mac_address": "FA:CE:00:14:D3:FC",
                "node_interfaces": [
                    {
                        "uid": "switch-uplink",
                        "mac_address": "FA:CE:00:14:D3:FA",
                        "op_mode": "",
                        "ssid": None,
                        "type": "LAN",
                        "name": "",
                        "node_links": [
                            {
                                "state": "CONNECTED",
                                "node_interface_1_uid": "master-lan1",
                                "node_interface_2_uid": "switch-uplink",
                                "cur_data_rate_rx": 1000000,
                                "cur_data_rate_tx": 1000000,
                            }
                        ],
                    },
                    {
                        "uid": "switch-port1",
                        "mac_address": "FA:CE:00:14:D3:FB",
                        "op_mode": "",
                        "ssid": None,
                        "type": "LAN",
                        "name": "",
                        "node_links": [],
                    },
                ],
            },
            {
                "is_meshed": False,
                "node_interfaces": [
                    {
                        "mac_address": "AA:BB:CC:DD:EE:04",
                        "node_links": [
                            {
                                # Real Fritz!Box direction: client as node_1, switch as node_2.
                                "state": "CONNECTED",
                                "node_interface_1_uid": "client-intf",
                                "node_interface_2_uid": "switch-port1",
                                "cur_data_rate_rx": 100000,
                                "cur_data_rate_tx": 100000,
                            }
                        ],
                    }
                ],
            },
        ]
    }

    with (
        patch.object(
            fritz_tools, "_async_update_hosts_info", AsyncMock(return_value=hosts)
        ),
        patch.object(
            fritz_tools.fritz_hosts,
            "get_mesh_topology",
            MagicMock(return_value=topology),
        ),
        patch.object(fritz_tools, "manage_device_info", return_value=False) as manage,
        patch.object(fritz_tools, "async_send_signal_device_update", AsyncMock()),
    ):
        await fritz_tools.async_scan_devices()

    # manage_device_info must be called exactly once — for the real client only.
    assert manage.call_count == 1
    dev_info = manage.call_args.args[0]
    assert dev_info.connected_to == "switch_d3826e0d"
    assert dev_info.connection_type == "LAN"
    assert dev_info.cur_rx_rate == 100000


async def test_switch_node_registered_by_stable_key(
    hass: HomeAssistant,
    fritz_tools,
) -> None:
    """LAN switch device is registered by SHA-256 key derived from interface MACs."""

    topology = {
        "nodes": [
            {
                "is_meshed": True,
                "mesh_role": "master",
                "device_name": "fritz.box",
                "device_mac_address": "1CED6F123411",
                "node_interfaces": [
                    {
                        "uid": "master-lan1",
                        "mac_address": fritz_tools.unique_id,
                        "op_mode": "",
                        "ssid": None,
                        "type": "LAN",
                        "name": "LAN:1",
                        "node_links": [
                            {
                                "state": "CONNECTED",
                                "node_interface_1_uid": "master-lan1",
                                "node_interface_2_uid": "switch-uplink",
                                "cur_data_rate_rx": 1000000,
                                "cur_data_rate_tx": 1000000,
                            }
                        ],
                    }
                ],
            },
            {
                "is_meshed": False,
                "device_class": "NETWORK_SWITCH",
                "device_name": "Switch",
                "device_mac_address": "FA:CE:00:14:D3:FC",
                "node_interfaces": [
                    {
                        "uid": "switch-uplink",
                        "mac_address": "FA:CE:00:14:D3:FA",
                        "op_mode": "",
                        "ssid": None,
                        "type": "LAN",
                        "name": "",
                        "node_links": [
                            {
                                "state": "CONNECTED",
                                "node_interface_1_uid": "master-lan1",
                                "node_interface_2_uid": "switch-uplink",
                                "cur_data_rate_rx": 1000000,
                                "cur_data_rate_tx": 1000000,
                            }
                        ],
                    },
                    {
                        "uid": "switch-port1",
                        "mac_address": "FA:CE:00:14:D3:FB",
                        "op_mode": "",
                        "ssid": None,
                        "type": "LAN",
                        "name": "",
                        "node_links": [],
                    },
                ],
            },
        ]
    }
    hosts = {
        "FA:CE:00:14:D3:FB": Device(
            connected=True,
            connected_to="",
            connection_type="",
            ip_address="",
            name="Switch",
            ssid=None,
        ),
    }

    with (
        patch.object(
            fritz_tools, "_async_update_hosts_info", AsyncMock(return_value=hosts)
        ),
        patch.object(
            fritz_tools.fritz_hosts,
            "get_mesh_topology",
            MagicMock(return_value=topology),
        ),
        patch.object(fritz_tools, "async_send_signal_device_update", AsyncMock()),
        patch(
            "homeassistant.components.fritz.mesh.async_dispatcher_send"
        ) as dispatch_mock,
    ):
        await fritz_tools.async_scan_devices()

    # sha256("FA:CE:00:14:D3:FA,FA:CE:00:14:D3:FB,FA:CE:00:14:D3:FC")[:8] == "d3826e0d"
    expected_key = "switch_d3826e0d"
    device_registry = dr.async_get(hass)
    assert (
        device_registry.async_get_device(identifiers={("fritz", expected_key)})
        is not None
    )
    assert expected_key in fritz_tools.switch_nodes
    dispatch_mock.assert_any_call(hass, fritz_tools.signal_switch_node_new)
