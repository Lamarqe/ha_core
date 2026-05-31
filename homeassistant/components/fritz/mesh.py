"""FRITZ!Box mesh topology processing."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, SWITCH_DEVICE_CLASS, MeshRoles
from .models import Device, Interface

_LOGGER = logging.getLogger(__name__)


class FritzMeshTopology:
    """Processes and tracks the FRITZ!Box mesh topology.

    Owns all mesh-related state (mesh nodes, switch nodes, uplink rates,
    slave parent nodes) and the processing pipeline that builds it from the
    raw topology dict returned by get_mesh_topology().

    The coordinator creates one instance, injects the HA dependencies it needs,
    and delegates every mesh-specific operation here.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry_id: str,
        unique_id: str,
        master_mac: str,
        manage_device_info: Callable[[Device, str, float], bool],
    ) -> None:
        """Initialise the mesh topology processor."""
        self._hass = hass
        self._config_entry_id = config_entry_id
        self._unique_id = unique_id
        self._master_mac = master_mac
        self._manage_device_info = manage_device_info

        self._mesh_nodes: dict[str, str] = {}
        self._switch_nodes: dict[str, str] = {}  # switch_key → friendly_name
        self._node_uplink_rates: dict[str, tuple[int | None, int | None]] = {}
        self._slave_parent_nodes: dict[str, str] = {}

        self.mesh_role: MeshRoles = MeshRoles.NONE
        self.mesh_wifi_uplink: bool = False

    # ── Public properties ─────────────────────────────────────────────────────

    @property
    def mesh_nodes(self) -> dict[str, str]:
        """Return mesh nodes mapping device_name to formatted MAC address."""
        return self._mesh_nodes

    @property
    def switch_nodes(self) -> dict[str, str]:
        """Return switch nodes mapping switch_key to friendly display name."""
        return self._switch_nodes

    @property
    def node_uplink_rates(self) -> dict[str, tuple[int | None, int | None]]:
        """Return node uplink rates mapping node MAC to (rx_kbps, tx_kbps)."""
        return self._node_uplink_rates

    @property
    def slave_parent_nodes(self) -> dict[str, str]:
        """Return slave parent node mapping sensor MAC to parent node name."""
        return self._slave_parent_nodes

    @property
    def signal_mesh_node_new(self) -> str:
        """Event specific per FRITZ!Box entry to signal new mesh node."""
        return f"{DOMAIN}-mesh-node-new-{self._unique_id}"

    @property
    def signal_switch_node_new(self) -> str:
        """Event specific per FRITZ!Box entry to signal new LAN switch node."""
        return f"{DOMAIN}-switch-node-new-{self._unique_id}"

    # ── Main entry point ──────────────────────────────────────────────────────

    def process_topology(
        self,
        topology: dict[str, Any],
        hosts: dict[str, Device],
        consider_home: float,
    ) -> bool:
        """Process mesh topology and update device info.

        Returns True if at least one new device was found.
        """
        mesh_intf: dict[str, Interface] = {}
        uplink_by_child_intf: dict[str, tuple[str, dict[str, Any]]] = {}
        new_mesh_nodes: dict[str, str] = {}

        # Pass 1: index all meshed interfaces; detect master/slaves.
        for node in topology.get("nodes", []):
            if not node["is_meshed"]:
                continue

            node_name = node["device_name"]
            for interf in node["node_interfaces"]:
                int_mac = interf["mac_address"]
                mesh_intf[interf["uid"]] = Interface(
                    device=node_name,
                    mac=int_mac,
                    op_mode=interf.get("op_mode", ""),
                    ssid=interf.get("ssid", ""),
                    type=interf["type"],
                    band=self._wifi_band_from_iface_name(interf.get("name", "")),
                )

                if interf["type"].lower() == "wlan" and interf[
                    "name"
                ].lower().startswith("uplink"):
                    self.mesh_wifi_uplink = True

                for link in interf["node_links"]:
                    if link.get("state") == "CONNECTED":
                        uplink_by_child_intf[link["node_interface_2_uid"]] = (
                            link["node_interface_1_uid"],
                            link,
                        )

                formatted_mac = dr.format_mac(int_mac)
                if formatted_mac == self._master_mac:
                    self.mesh_role = MeshRoles(node["mesh_role"])
                    new_mesh_nodes[node_name] = self._master_mac
                elif node_name not in new_mesh_nodes:
                    new_mesh_nodes[node_name] = formatted_mac

        self._update_mesh_nodes(new_mesh_nodes)
        self._register_switch_nodes(topology, hosts, mesh_intf, uplink_by_child_intf)
        self._populate_slave_uplink_rates(topology, mesh_intf, uplink_by_child_intf)

        new_device = False

        # Pass 2: update tracked device info for meshed slaves.
        for node in topology.get("nodes", []):
            if not node["is_meshed"] or node["mesh_role"] == "master":
                continue

            node_mac_raw = node["device_mac_address"]
            if node_mac_raw not in hosts:
                continue

            dev_info = hosts[node_mac_raw]
            parent_intf, link = self._find_slave_uplink(
                node, mesh_intf, uplink_by_child_intf
            )
            if parent_intf is not None and link is not None:
                dev_info.connected_to = parent_intf["device"]
                dev_info.connection_type = parent_intf["type"]
                dev_info.cur_rx_rate = link.get("cur_data_rate_rx")
                dev_info.cur_tx_rate = link.get("cur_data_rate_tx")

            if self._manage_device_info(dev_info, node_mac_raw, consider_home):
                new_device = True

        # Pass 3: update tracked device info for non-meshed clients.
        for node in topology.get("nodes", []):
            if node["is_meshed"] or self._is_switch_node(node):
                continue

            for interf in node["node_interfaces"]:
                dev_mac = interf["mac_address"]

                if dev_mac not in hosts:
                    continue

                dev_info = hosts[dev_mac]

                for link in interf["node_links"]:
                    if link.get("state") != "CONNECTED":
                        continue  # ignore orphan node links

                    # For master/slave-connected devices, the parent interface is in
                    # node_interface_1_uid (infrastructure as node_1). For switch-connected
                    # devices the link is reversed (device as node_1, switch as node_2), so
                    # the switch port is in node_interface_2_uid instead.
                    intf = mesh_intf.get(link["node_interface_1_uid"]) or mesh_intf.get(
                        link["node_interface_2_uid"]
                    )
                    if intf is not None:
                        if intf["op_mode"] == "AP_GUEST":
                            dev_info.wan_access = None

                        dev_info.connected_to = intf["device"]
                        dev_info.connection_type = intf["type"]
                        dev_info.ssid = intf.get("ssid")
                        dev_info.wifi_band = intf["band"]
                        dev_info.cur_rx_rate = link.get("cur_data_rate_rx")
                        dev_info.cur_tx_rate = link.get("cur_data_rate_tx")

                if self._manage_device_info(dev_info, dev_mac, consider_home):
                    new_device = True

        return new_device

    # ── Device-registry + dispatcher helpers ──────────────────────────────────

    def _update_mesh_nodes(self, new_mesh_nodes: dict[str, str]) -> None:
        """Update tracked mesh nodes, register new slave device entries, and signal changes."""
        new_nodes = set(new_mesh_nodes) - set(self._mesh_nodes)
        self._mesh_nodes = new_mesh_nodes

        for node_name in new_nodes:
            node_mac = new_mesh_nodes[node_name]
            if node_mac != self._master_mac:
                dr.async_get(self._hass).async_get_or_create(
                    config_entry_id=self._config_entry_id,
                    connections={(CONNECTION_NETWORK_MAC, node_mac)},
                    manufacturer="FRITZ!",
                    name=node_name,
                    via_device=(DOMAIN, self._unique_id),
                )

        if new_nodes:
            async_dispatcher_send(self._hass, self.signal_mesh_node_new)

    def _update_switch_nodes(
        self,
        new_switch_nodes: dict[str, str],
        new_switch_uplink_rates: dict[str, tuple[int | None, int | None]],
    ) -> None:
        """Update tracked LAN switch nodes, register device entries, and signal changes."""
        added_keys = set(new_switch_nodes) - set(self._switch_nodes)
        self._switch_nodes = new_switch_nodes
        for switch_key in new_switch_nodes:
            self._node_uplink_rates[switch_key] = new_switch_uplink_rates.get(
                switch_key, (None, None)
            )

        for switch_key in added_keys:
            dr.async_get(self._hass).async_get_or_create(
                config_entry_id=self._config_entry_id,
                identifiers={(DOMAIN, switch_key)},
                manufacturer="Generic",
                name=switch_key,
                via_device=(DOMAIN, self._unique_id),
            )

        if added_keys:
            async_dispatcher_send(self._hass, self.signal_switch_node_new)

    # ── Topology processing helpers ───────────────────────────────────────────

    def _register_switch_nodes(
        self,
        topology: dict[str, Any],
        hosts: dict[str, Device],
        mesh_intf: dict[str, Interface],
        uplink_by_child_intf: dict[str, tuple[str, dict[str, Any]]],
    ) -> None:
        """Register non-meshed LAN switch interfaces into mesh_intf."""
        new_switch_nodes: dict[str, str] = {}
        new_switch_uplink_rates: dict[str, tuple[int | None, int | None]] = {}
        for node in topology.get("nodes", []):
            if node["is_meshed"] or not self._is_switch_node(node):
                continue

            switch_key = self._switch_key_from_node(node)

            # Prefer FriendlyName from the active host entry; fall back to switch_key.
            friendly_name: str = switch_key
            for interf in node["node_interfaces"]:
                host = hosts.get(interf["mac_address"])
                if host and isinstance(host.name, str) and host.name and host.connected:
                    friendly_name = host.name
                    break

            # Detect uplink link speed before adding switch interfaces to mesh_intf
            # so only pre-existing meshed interfaces are matched.
            uplink_rx: int | None = None
            uplink_tx: int | None = None
            for interf in node["node_interfaces"]:
                for link in interf["node_links"]:
                    if link.get("state") == "CONNECTED" and mesh_intf.get(
                        link["node_interface_1_uid"]
                    ):
                        uplink_rx = link.get("cur_data_rate_rx") or None
                        uplink_tx = link.get("cur_data_rate_tx") or None
                        break
                if uplink_rx is not None:
                    break

            if uplink_rx is None:
                _LOGGER.warning(
                    "LAN switch node %s has no connected uplink to a mesh node; "
                    "skipping switch registration",
                    node.get("uid"),
                )
                continue

            for interf in node["node_interfaces"]:
                int_mac = interf["mac_address"]
                mesh_intf[interf["uid"]] = Interface(
                    device=switch_key,
                    mac=int_mac,
                    op_mode=interf.get("op_mode", ""),
                    ssid=interf.get("ssid", ""),
                    type=interf["type"],
                    band="",
                )
                for link in interf["node_links"]:
                    if link.get("state") == "CONNECTED":
                        uplink_by_child_intf[link["node_interface_2_uid"]] = (
                            link["node_interface_1_uid"],
                            link,
                        )

            new_switch_nodes[switch_key] = friendly_name
            new_switch_uplink_rates[switch_key] = (uplink_rx, uplink_tx)

        self._update_switch_nodes(new_switch_nodes, new_switch_uplink_rates)

    def _populate_slave_uplink_rates(
        self,
        topology: dict[str, Any],
        mesh_intf: dict[str, Interface],
        uplink_by_child_intf: dict[str, tuple[str, dict[str, Any]]],
    ) -> None:
        """Populate _node_uplink_rates for all non-master meshed nodes from topology.

        This is independent of the hosts dict so it runs even when a slave's
        device_mac_address differs from (or is absent in) the hosts list.
        """
        for node in topology.get("nodes", []):
            if not node["is_meshed"] or node["mesh_role"] == "master":
                continue
            sensor_mac = self._mesh_nodes.get(node["device_name"])
            if sensor_mac is None:
                continue
            parent_intf, link = self._find_slave_uplink(
                node, mesh_intf, uplink_by_child_intf
            )
            if parent_intf is not None and link is not None:
                self._node_uplink_rates[sensor_mac] = (
                    link.get("cur_data_rate_rx") or None,
                    link.get("cur_data_rate_tx") or None,
                )
                self._slave_parent_nodes[sensor_mac] = parent_intf["device"]

    # ── Pure static helpers ───────────────────────────────────────────────────

    @staticmethod
    def _switch_key_from_node(node: dict[str, Any]) -> str:
        """Return a stable unique key for a LAN switch node.

        The key is "switch_" followed by the first 8 hex characters of the SHA-256
        hash of all interface MAC addresses (sorted, comma-separated).
        """
        macs = sorted(
            interf["mac_address"]
            for interf in node.get("node_interfaces", [])
            if interf.get("mac_address")
        )
        digest = hashlib.sha256(",".join(macs).encode()).hexdigest()[:8]
        return f"switch_{digest}"

    @staticmethod
    def _is_switch_node(node: dict[str, Any]) -> bool:
        """Return True if this topology node is a LAN switch."""
        return node.get("device_class") == SWITCH_DEVICE_CLASS

    @staticmethod
    def _wifi_band_from_iface_name(name: str) -> str:
        """Return human-readable WiFi band from an interface name (e.g. 'AP:5G:0' → '5 GHz')."""
        if "6G" in name:
            return "6 GHz"
        if "5G" in name:
            return "5 GHz"
        if "2G" in name:
            return "2.4 GHz"
        return ""

    @staticmethod
    def _find_slave_uplink(
        node: dict[str, Any],
        mesh_intf: dict[str, Interface],
        uplink_by_child_intf: dict[str, tuple[str, dict[str, Any]]],
    ) -> tuple[Interface | None, dict[str, Any] | None]:
        """Return (parent_interface, link) for a mesh slave node, or (None, None).

        First tries uplink_by_child_intf (covers WiFi-backhaul and direct-LAN slaves).
        Falls back to scanning the slave's own interface links against mesh_intf, which
        covers LAN-backhaul slaves whose uplink passes through a switch.
        """
        for interf in node["node_interfaces"]:
            if interf["uid"] not in uplink_by_child_intf:
                continue
            parent_intf_uid, link = uplink_by_child_intf[interf["uid"]]
            parent_intf = mesh_intf.get(parent_intf_uid)
            if parent_intf is not None:
                return parent_intf, link

        # Fallback for LAN-backhaul slaves: the slave is node_1 in its uplink link,
        # so its own interface is ni1 and the upstream device (switch) is ni2.
        # Only check ni2 to avoid mistakenly matching the slave's own interfaces.
        for interf in node["node_interfaces"]:
            for link in interf.get("node_links", []):
                if link.get("state") != "CONNECTED":
                    continue
                parent_intf = mesh_intf.get(link["node_interface_2_uid"])
                if parent_intf is not None:
                    return parent_intf, link

        return None, None
