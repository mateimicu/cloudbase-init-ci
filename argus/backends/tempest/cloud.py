# Copyright 2015 Cloudbase Solutions Srl
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from argus.backends.tempest import tempest_backend
from argus import config as argus_config
from argus import exceptions
from argus import log as argus_log
from argus import util

with util.restore_excepthook():
    from tempest.common import dynamic_creds
    from tempest.common import waiters

CONFIG = argus_config.CONFIG
LOG = argus_log.LOG

SUBNET6_CIDR = "::ffff:a00:0/120"
DNSES6 = ["::ffff:808:808", "::ffff:808:404"]


class NetworkWindowsBackend(tempest_backend.BaseWindowsTempestBackend):
    """Back-end for providing static network configuration.

    Creates an additional internal network which will be
    bound explicitly with the new created instance.
    """

    def _get_isolated_network(self):
        """Returns the network itself from the isolated network resources.

        This works only with the isolated credentials and
        this step is achieved by allowing/forcing tenant isolation.
        """
        # Extract the just created private network.
        return self._manager.primary_credentials().network

    def _get_networks(self):
        """Explicitly gather and return the private networks.

        All these networks will be attached to the newly created
        instance without letting nova to handle this part.
        """
        tenant_id = self._manager.primary_credentials().tenant_id
        _networks = self._manager.networks_client.list_networks()
        LOG.debug("Tenant id %s", tenant_id)
        LOG.debug("Networks %r", _networks)
        try:
            _networks = _networks["networks"]
        except KeyError:
            raise exceptions.ArgusError('Networks not found.')
        # Skip external/private networks.
        networks = [net["id"] for net in _networks
                    if not net["router:external"] and
                    net[u'tenant_id'] == tenant_id]

        LOG.debug("ID networks %r", networks)

        # Put in front the main private network.
        head = self._get_isolated_network()["id"]
        networks.remove(head)
        networks.insert(0, head)
        LOG.debug("Final ID networks %r", networks)

        # Adapt the list to a format accepted by the API.
        return [{"uuid": net} for net in networks]

    def _create_private_network(self):
        """Create an extra private network to be attached.

        This network is the one with disabled DHCP and
        ready for static configuration by Cloudbase-Init.
        """
        tenant_id = self._manager.primary_credentials().tenant_id
        # pylint: disable=protected-access
        net_resources = self._manager.isolated_creds._create_network_resources(
            tenant_id)

        # Store the network for later cleanup.
        key = "fake"
        fake_net_creds = util.get_namedtuple(
            "FakeCreds",
            ("network", "subnet", "router",
             "user_id", "tenant_id", "username", "tenant_name"),
            net_resources + (None,) * 4)
        self._manager.isolated_creds._creds[key] = fake_net_creds

        # Disable DHCP for this network to test static configuration and
        # also add default DNS name servers.
        subnet_id = fake_net_creds.subnet["id"]
        subnets_client = self._manager.subnets_client
        subnets_client.update_subnet(
            subnet_id, enable_dhcp=False,
            dns_nameservers=CONFIG.argus.dns_nameservers)

        # Change the allocation pool to configure any IP,
        # other the one used already with dynamic settings.
        allocation_pools = subnets_client.show_subnet(subnet_id)["subnet"][
            "allocation_pools"]
        allocation_pools[0]["start"] = util.next_ip(
            allocation_pools[0]["start"], step=2)
        subnets_client.update_subnet(subnet_id,
                                     allocation_pools=allocation_pools)

        # Create and attach an IPv6 subnet for this network. Also, register
        # it for later cleanup.
        subnet6_name = util.rand_name(self.__class__.__name__) + "-subnet6"
        network_id = fake_net_creds.network["id"]
        subnets_client.create_subnet(
            network_id=network_id,
            cidr=SUBNET6_CIDR,
            name=subnet6_name,
            dns_nameservers=DNSES6,
            tenant_id=tenant_id,
            enable_dhcp=False,
            ip_version=6)

    def setup_instance(self):
        # Just like a normal preparer, but this time
        # with explicitly specified attached networks.

        if not isinstance(self._manager.isolated_creds,
                          dynamic_creds.DynamicCredentialProvider):
            raise exceptions.ArgusError(
                "Network resources are not available."
            )

        self._create_private_network()
        self._networks = self._get_networks()
        LOG.debug("Final networks for instante %r", self._networks)

        super(NetworkWindowsBackend, self).setup_instance()

    @staticmethod
    def _find_ip_address(port, subnet_id):
        for fixed_ip in port["fixed_ips"]:
            if fixed_ip["subnet_id"] == subnet_id:
                return fixed_ip["ip_address"]

    def get_network_interfaces(self):
        """Retrieve and parse network details from the compute node."""
        ports_client = self._manager.ports_client
        networks_client = self._manager.networks_client
        subnets_client = self._manager.subnets_client
        guest_nics = []
        for network in self._networks or []:
            network_id = network["uuid"]
            net_details = networks_client.show_network(network_id)["network"]
            nic = dict.fromkeys(util.NETWORK_KEYS)
            for subnet_id in net_details["subnets"]:
                details = subnets_client.show_subnet(subnet_id)["subnet"]

                # The network interface should follow the format found under
                # `windows.InstanceIntrospection.get_network_interfaces`
                # method or `argus.util.NETWORK_KEYS` model.
                v6switch = details["ip_version"] == 6
                v6suffix = "6" if v6switch else ""
                nic["dhcp"] = details["enable_dhcp"]
                nic["dns" + v6suffix] = details["dns_nameservers"]
                nic["gateway" + v6suffix] = details["gateway_ip"]
                nic["netmask" + v6suffix] = (
                    details["cidr"].split("/")[1] if v6switch
                    else util.cidr2netmask(details["cidr"]))

                # Find rest of the details under the ports using this subnet.
                # There should be no conflicts because on the current
                # architecture every instance is using its own router,
                # subnet and network accessible only to it.
                ports = ports_client.list_ports()["ports"]
                for port in ports:
                    # Select instance related ports only, with the
                    # corresponding subnet ID.
                    if "compute" not in port["device_owner"]:
                        continue
                    ip_address = self._find_ip_address(port, subnet_id)
                    if not ip_address:
                        continue
                    nic["mac"] = port["mac_address"].upper()
                    nic["address" + v6suffix] = ip_address
                    break

            guest_nics.append(nic)
        return guest_nics


class RescueWindowsBackend(tempest_backend.BaseWindowsTempestBackend):
    """Instance rescue Windows-based back-end."""

    def rescue_server(self):
        """Rescue the underlying instance."""
        admin_pass = CONFIG.openstack.image_password
        self._manager.servers_client.rescue_server(
            self.internal_instance_id(),
            adminPass=admin_pass)

        waiters.wait_for_server_status(
            self._manager.servers_client,
            self.internal_instance_id(), 'RESCUE')

    def unrescue_server(self):
        """Unrescue the underlying instance."""
        self._manager.servers_client.unrescue_server(
            self.internal_instance_id())
        waiters.wait_for_server_status(
            self._manager.servers_client,
            self.internal_instance_id(), 'ACTIVE')
