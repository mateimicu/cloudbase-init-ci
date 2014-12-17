# Copyright 2014 Cloudbase-init
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

import abc
import os
import unittest

import six

from argus import config
from argus import scenario
from argus import util

CONF = config.CONF
DNSMASQ_NEUTRON = '/etc/neutron/dnsmasq-neutron.conf'
DHCP_AGENT = '/etc/neutron/dhcp_agent.ini'


@util.run_once
def _dnsmasq_configured():
    """Verify that the dnsmasq_config_file was set and it exists.

    Without it, tests for MTU or NTP will fail, since their plugins
    are relying on DHCP to provide this information.
    """
    if not os.path.exists(DHCP_AGENT):
        return False
    with open(DHCP_AGENT) as stream:
        for line in stream:
            if not line.startswith('dnsmasq_config_file'):
                continue
            _, _, dnsmasq_file = line.partition("=")
            if dnsmasq_file.strip() == DNSMASQ_NEUTRON:
                return True
    return False

def skip_unless_dnsmasq_configured(func):
    msg = (
        "Test will fail if the `dhcp-option-force` option "
        "was not configured by the `dnsmasq_config_file` "
        "from neutron/dhcp-agent.ini.")
    return unittest.skipUnless(_dnsmasq_configured(), msg)(func)

def _get_dhcp_value(key):
    """Get the value of an override from the dnsmasq-config file.

    An override will be have the format 'dhcp-option-force=key,value'.
    """
    lookup = "dhcp-option-force={}".format(key)
    with open(DNSMASQ_NEUTRON) as stream:
        for line in stream:
            if not line.startswith(lookup):
                continue
            _, _, option_value = line.strip().partition("=")
            _, _, value = option_value.partition(",")
            return value.strip()


@six.add_metaclass(abc.ABCMeta)
class GenericInstanceUtils(object):
    """A generic utility class, which provides methods for interrogating an instance."""

    def __init__(self, test_case):
        self.remote_client = test_case
        self.run_command_verbose = test_case.remote_client.run_command_verbose
        self.server = test_case.server

    @abc.abstractmethod
    def get_plugins_count(self):
        """Return the plugins count from the instance."""

    @abc.abstractmethod
    def get_disk_size(self):
        """Return the disk size from the instance."""

    @abc.abstractmethod
    def username_exists(self, username):
        """Check if the given username exists in the instance."""

    @abc.abstractmethod
    def get_instance_hostname(self):
        """Get the hostname of the instance."""

    @abc.abstractmethod
    def get_instance_ntp_peers(self):
        """Get the NTP peers from the instance."""

    @abc.abstractmethod
    def get_instance_keys_path(self):
        """Return the authorized_keys file path from the instance."""

    @abc.abstractmethod
    def get_instance_file_content(self, filepath):
        """Return the content of the given file from the instance."""

    @abc.abstractmethod
    def get_userdata_executed_plugins(self):
        """Get the count of userdata executed plugins."""

    @abc.abstractmethod
    def get_instance_mtu(self):
        """Get the mtu value from the instance."""

    @abc.abstractmethod
    def get_cloudbaseinit_traceback(self):
        """Return the traceback, if any, from the cloudbaseinit's logs."""

    @abc.abstractmethod
    def instance_shell_script_executed(self):
        """Check if the shell script executed in the instance.

        The script was added when we prepared the instance.
        """

    @abc.abstractmethod
    def get_group_members(self, group):
        """Get the members of the local group given."""

    # The actual mandatory tests for each supported OS


# pylint: disable=abstract-method
class GenericTests(scenario.BaseArgusScenario):
    """Various common generic tests for testing cloudbaseinit.

    They are generic because they don't depend on a particular OS version.
    Each OS test version must implement the abstract methods provided here,
    the methods will be called by each required test.
    The tests provided here are testing that basic behaviour of
    cloudbaseinit is fulfilled. OS specific tests should go in the
    specific subclass.
    """
    instance_utils_class = GenericInstanceUtils

    def setUp(self):
        super(GenericTests, self).setUp()
        self.instance_utils = self.instance_utils_class(self)

    def test_plugins_count(self):
        # Test that we have the expected numbers of plugins.
        plugins_count = self.instance_utils.get_plugins_count()
        self.assertEqual(CONF.argus.expected_plugins_count,
                         plugins_count)

    def test_disk_expanded(self):
        # Test the disk expanded properly.
        image = self.get_image_ref()
        datastore_size = image[1]['OS-EXT-IMG-SIZE:size']
        disk_size = self.instance_utils.get_disk_size()
        self.assertGreater(disk_size, datastore_size)

    def test_username_created(self):
        # Verify that the expected created user exists.
        exists = self.instance_utils.username_exists(CONF.argus.created_user)
        self.assertTrue(exists)

    def test_hostname_set(self):
        # Test that the hostname was properly set.
        instance_hostname = self.instance_utils.get_instance_hostname()
        server = self.instance_server()[1]

        self.assertEqual(instance_hostname,
                         str(server['name'][:15]).lower())

    @skip_unless_dnsmasq_configured
    def test_ntp_properly_configured(self):
        # Verify that the expected NTP peers are active.
        peers = self.instance_utils.get_instance_ntp_peers()
        expected_peer = _get_dhcp_value('42')
        if expected_peer is None:
            self.fail('DHCP NTP option was not configured.')

        self.assertEqual([expected_peer], peers)

    def test_password_set(self):
        # Test that the proper password was set.
        remote_client = self.get_remote_client(CONF.argus.created_user,
                                               self.password())
        stdout = remote_client.run_command_verbose("echo 1")
        self.assertEqual('1', stdout)


    def test_sshpublickeys_set(self):
        # Verify that we set the expected ssh keys.
        authorized_keys = self.instance_utils.get_instance_keys_path()
        public_key = self.instance_utils.get_instance_file_content(
            authorized_keys).replace('\r\n', '\n')
        self.assertEqual(self.keypair['public_key'], public_key)

    def test_userdata(self):
        # Verify that we executed the expected number of
        # user data plugins.
        userdata_executed_plugins = (
            self.instance_utils.get_userdata_executed_plugins())
        self.assertEqual(4, userdata_executed_plugins)

    @skip_unless_dnsmasq_configured
    def test_mtu(self):
        # Verify that we have the expected MTU in the instance.
        mtu = self.instance_utils.get_instance_mtu()
        expected_mtu = _get_dhcp_value('26')
        self.assertEqual(expected_mtu, mtu)

    def test_any_exception_occurred(self):
        # Verify that any exception occurred in the instance
        # for cloudbaseinit.
        instance_traceback = self.instance_utils.get_cloudbaseinit_traceback()
        self.assertEqual('', instance_traceback)

    def test_local_scripts_executed(self):
        # Verify that the shell script we provided as local script
        # was executed.
        self.assertTrue(self.instance_utils.instance_shell_script_executed())

    def test_user_belongs_to_group(self):
        # Check that the created user belongs to the specified local groups
        members = self.instance_utils.get_group_members(CONF.argus.group)
        self.assertIn(CONF.argus.created_user, members)

