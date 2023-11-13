#!/usr/bin/env python3
#
# Copyright (C) 2022-2023 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import re
import unittest

from collections import OrderedDict
from base_accel_ppp_test import BasicAccelPPPTest
from vyos.configsession import ConfigSessionError
from vyos.utils.process import cmd
from configparser import ConfigParser
from configparser import RawConfigParser

ac_name = "ACN"
interface = "eth0"


class MultiOrderedDict(OrderedDict):
    # Accel-ppp has duplicate keys in config file (gw-ip-address)
    # This class is used to define dictionary which can contain multiple values
    # in one key.
    def __setitem__(self, key, value):
        if isinstance(value, list) and key in self:
            self[key].extend(value)
        else:
            super(OrderedDict, self).__setitem__(key, value)


class TestServiceIPoEServer(BasicAccelPPPTest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._base_path = ["service", "ipoe-server"]
        cls._config_file = "/run/accel-pppd/ipoe.conf"
        cls._chap_secrets = "/run/accel-pppd/ipoe.chap-secrets"
        cls._protocol_section = "ipoe"

        # call base-classes classmethod
        super(TestServiceIPoEServer, cls).setUpClass()

    def verify(self, conf):
        super().verify(conf)

        # Validate configuration values
        accel_modules = list(conf["modules"].keys())
        self.assertIn("log_syslog", accel_modules)
        self.assertIn("ipoe", accel_modules)
        self.assertIn("shaper", accel_modules)
        self.assertIn("ipv6pool", accel_modules)
        self.assertIn("ipv6_nd", accel_modules)
        self.assertIn("ipv6_dhcp", accel_modules)
        self.assertIn("ippool", accel_modules)

    def initial_gateway_config(self):
        self._gateway = "192.0.2.1/24"
        super().initial_gateway_config()

    def initial_auth_config(self):
        self.set(["authentication", "mode", "noauth"])

    def basic_protocol_specific_config(self):
        self.set(["interface", interface, "client-subnet", "192.168.0.0/24"])

    def test_accel_local_authentication(self):
        mac_address = "08:00:27:2f:d8:06"
        self.set(["authentication", "interface", interface, "mac", mac_address])
        self.set(["authentication", "mode", "local"])

        # No IPoE interface configured
        with self.assertRaises(ConfigSessionError):
            self.cli_commit()

        # Test configuration of local authentication for PPPoE server
        self.basic_config()
        # Rewrite authentication from basic_config
        self.set(["authentication", "interface", interface, "mac", mac_address])
        self.set(["authentication", "mode", "local"])
        # commit changes
        self.cli_commit()

        # Validate configuration values
        conf = ConfigParser(allow_no_value=True, delimiters="=", strict=False)
        conf.read(self._config_file)

        # check proper path to chap-secrets file
        self.assertEqual(conf["chap-secrets"]["chap-secrets"], self._chap_secrets)

        accel_modules = list(conf["modules"].keys())
        self.assertIn("chap-secrets", accel_modules)

        # basic verification
        self.verify(conf)

        # check local users
        tmp = cmd(f"sudo cat {self._chap_secrets}")
        regex = f"{interface}\s+\*\s+{mac_address}\s+\*"
        tmp = re.findall(regex, tmp)
        self.assertTrue(tmp)

    def test_accel_ipv4_pool(self):
        self.basic_config(is_gateway=False, is_client_pool=False)

        gateway = ["172.16.0.1/25", "192.0.2.1/24"]
        subnet = "172.16.0.0/24"
        first_pool = "POOL1"
        second_pool = "POOL2"
        range = "192.0.2.10-192.0.2.20"

        for gw in gateway:
            self.set(["gateway-address", gw])

        self.set(["client-ip-pool", first_pool, "range", subnet])
        self.set(["client-ip-pool", first_pool, "next-pool", second_pool])
        self.set(["client-ip-pool", second_pool, "range", range])
        self.set(["default-pool", first_pool])
        # commit changes

        self.cli_commit()

        # Validate configuration values
        conf = RawConfigParser(
            allow_no_value=True,
            delimiters="=",
            strict=False,
            dict_type=MultiOrderedDict,
        )
        conf.read(self._config_file)

        self.assertIn(
            f"{first_pool},next={second_pool}", conf["ip-pool"][f"{subnet},name"]
        )
        self.assertIn(second_pool, conf["ip-pool"][f"{range},name"])

        gw_pool_config_list = conf.get("ip-pool", "gw-ip-address")
        gw_ipoe_config_list = conf.get(self._protocol_section, "gw-ip-address")
        for gw in gateway:
            self.assertIn(gw.split("/")[0], gw_pool_config_list)
            self.assertIn(gw, gw_ipoe_config_list)

        self.assertIn(first_pool, conf[self._protocol_section]["ip-pool"])

    def test_accel_next_pool(self):
        self.basic_config(is_gateway=False, is_client_pool=False)

        first_pool = "VyOS-pool1"
        first_subnet = "192.0.2.0/25"
        first_gateway = "192.0.2.1/24"
        second_pool = "Vyos-pool2"
        second_subnet = "203.0.113.0/25"
        second_gateway = "203.0.113.1/24"
        third_pool = "Vyos-pool3"
        third_subnet = "198.51.100.0/24"
        third_gateway = "198.51.100.1/24"

        self.set(["gateway-address", f"{first_gateway}"])
        self.set(["gateway-address", f"{second_gateway}"])
        self.set(["gateway-address", f"{third_gateway}"])

        self.set(["client-ip-pool", first_pool, "range", first_subnet])
        self.set(["client-ip-pool", first_pool, "next-pool", second_pool])
        self.set(["client-ip-pool", second_pool, "range", second_subnet])
        self.set(["client-ip-pool", second_pool, "next-pool", third_pool])
        self.set(["client-ip-pool", third_pool, "range", third_subnet])

        # commit changes
        self.cli_commit()

        config = self.getConfig("ip-pool")
        # T5099 required specific order
        pool_config = f"""gw-ip-address={first_gateway.split('/')[0]}
gw-ip-address={second_gateway.split('/')[0]}
gw-ip-address={third_gateway.split('/')[0]}
{third_subnet},name={third_pool}
{second_subnet},name={second_pool},next={third_pool}
{first_subnet},name={first_pool},next={second_pool}"""
        self.assertIn(pool_config, config)


if __name__ == "__main__":
    unittest.main(verbosity=2)
