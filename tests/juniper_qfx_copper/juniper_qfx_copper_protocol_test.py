# Copyright 2015-2016 Internap.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest
from lxml import etree

from fake_switches.netconf import dict_2_etree, XML_TEXT, XML_ATTRIBUTES
from hamcrest import assert_that, has_length, equal_to, has_items, is_, is_not
from ncclient import manager
from ncclient.operations import RPCError
from tests import contains_regex
from tests.netconf.netconf_protocol_test import xml_equals_to

from tests.util.global_reactor import juniper_qfx_copper_switch_ip, \
    juniper_qfx_copper_switch_netconf_port


class JuniperQfxCopperProtocolTest(unittest.TestCase):

    def setUp(self):
        self.nc = self.create_client()

    def tearDown(self):
        try:
            self.nc.discard_changes()
        finally:
            self.nc.close_session()

    def create_client(self):
        return manager.connect(
            host=juniper_qfx_copper_switch_ip,
            port=juniper_qfx_copper_switch_netconf_port,
            username="root",
            password="root",
            hostkey_verify=False,
            device_params={'name': 'junos'}
        )

    def test_capabilities(self):
        assert_that(self.nc.server_capabilities, has_items(
                "urn:ietf:params:xml:ns:netconf:base:1.0",
                "urn:ietf:params:xml:ns:netconf:capability:candidate:1.0",
                "urn:ietf:params:xml:ns:netconf:capability:confirmed-commit:1.0",
                "urn:ietf:params:xml:ns:netconf:capability:validate:1.0",
                "urn:ietf:params:xml:ns:netconf:capability:url:1.0?protocol=http,ftp,file",
                "http://xml.juniper.net/netconf/junos/1.0",
                "http://xml.juniper.net/dmi/system/1.0",
        ))

    def test_get_running_config(self):
        result = self.nc.get_config(source="running")

        conf = result._NCElement__result.xml
        assert_that(conf, contains_regex(
                '<configuration xmlns="http://xml.juniper.net/xnm/1.1/xnm" junos:commit-localtime="[^"]*" junos:commit-seconds="[^"]*" junos:commit-user="[^"]*">'))

        assert_that(result.xpath("data/configuration/interfaces/interface/unit/family/ethernet-switching"),
                    has_length(4))
        assert_that(result.xpath("data/configuration/vlans/vlan"), has_length(0))

    def test_lock_edit_candidate_add_vlan_and_commit(self):
        with self.nc.locked(target='candidate'):
            result = self.nc.edit_config(target='candidate', config=dict_2_etree({
                "config": {
                    "configuration": {
                        "vlans": {
                            "vlan": {
                                "name": "VLAN2999",
                            }
                        }
                    }
                }}))
            assert_that(result.xpath("//rpc-reply/ok"), has_length(1))

            result = self.nc.commit()
            assert_that(result.xpath("//rpc-reply/ok"), has_length(1))

        result = self.nc.get_config(source="running")

        assert_that(result.xpath("data/configuration/vlans/vlan"), has_length(1))

        self.edit({
            "vlans": {
                "vlan": {
                    XML_ATTRIBUTES: {"operation": "delete"},
                    "name": "VLAN2999"
                }
            }
        })

        self.nc.commit()

        result = self.nc.get_config(source="running")
        assert_that(result.xpath("data/configuration/vlans/vlan"), has_length(0))

    def test_locking_fails_if_changes_are_being_made(self):
        nc2 = self.create_client()

        try:
            self.nc.edit_config(target='candidate', config=dict_2_etree({
                "config": {
                    "configuration": {
                        "vlans": {
                            "vlan": [
                                {"name": "VLAN2999"},
                                {"description": "WHAAT"}
                            ]
                        }
                    }
                }}))

            with self.assertRaises(RPCError):
                with nc2.locked(target='candidate'):
                    self.fail('Should not be able to lock an edited configuration')

        finally:
            self.nc.discard_changes()
            nc2.close_session()

    def test_double_locking_with_two_sessions(self):
        nc2 = self.create_client()

        try:
            with self.nc.locked(target='candidate'):
                with self.assertRaises(RPCError):
                    with nc2.locked(target='candidate'):
                        self.fail("The second lock should not have worked.")

        finally:
            nc2.close_session()

    def test_bad_configuration_element(self):
        with self.assertRaises(RPCError):
            self.nc.edit_config(target='candidate', config=dict_2_etree({
                "config": {
                    "configuration": {
                        "vbleh": "shizzle"
                    }
                }}))

    def test_create_vlan(self):
        self.nc.edit_config(target='candidate', config=dict_2_etree({"config": {"configuration": {
            "vlans": {
                "vlan": [
                    {"name": "VLAN2999"},
                    {"description": "WHAAT"},
                    {"vlan-id": "2995"}
                ]
            }
        }}}))

        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"vlans": {}}}
        }))

        assert_that(result.xpath("data/*"), has_length(1))
        assert_that(result.xpath("data/configuration/*"), has_length(1))
        assert_that(result.xpath("data/configuration/vlans/*"), has_length(1))
        assert_that(result.xpath("data/configuration/vlans/vlan/*"), has_length(3))

        vlan2995 = result.xpath("data/configuration/vlans/vlan")[0]

        assert_that(vlan2995.xpath("name")[0].text, equal_to("VLAN2999"))
        assert_that(vlan2995.xpath("description")[0].text, equal_to("WHAAT"))
        assert_that(vlan2995.xpath("vlan-id")[0].text, equal_to("2995"))

        self.cleanup(vlan("VLAN2999"))

    def test_vlan_configuration_merging(self):
        self.edit({
            "vlans": {
                "vlan": [
                    {"name": "VLAN2999"},
                    {"vlan-id": "2995"}
                ]}})
        self.edit({
            "vlans": {
                "vlan": [
                    {"name": "VLAN2999"},
                    {"description": "shizzle"}
                ]}})
        self.nc.commit()

        self.edit({
            "vlans": {
                "vlan": [
                    {"name": "VLAN2999"},
                    {"vlan-id": "2996"},
                    {"description": {XML_ATTRIBUTES: {"operation": "delete"}}}
                ]}})

        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"vlans": {}}}
        }))

        assert_that(result.xpath("data/configuration/vlans/vlan"), has_length(1))

        vlan2995 = result.xpath("data/configuration/vlans/vlan")[0]

        assert_that(vlan2995.xpath("name")[0].text, equal_to("VLAN2999"))
        assert_that(vlan2995.xpath("description"), has_length(0))
        assert_that(vlan2995.xpath("vlan-id")[0].text, equal_to("2996"))

        self.cleanup(vlan("VLAN2999"))

    def test_deletion_errors(self):
        self.edit({
            "vlans": {
                "vlan": [
                    {"name": "VLAN2999"},
                    {"vlan-id": "2995"}]}})

        with self.assertRaises(RPCError):
            self.edit({
                "vlans": {
                    "vlan": {
                        "name": "VLAN3000",
                        XML_ATTRIBUTES: {"operation": "delete"}}}})

        with self.assertRaises(RPCError):
            self.edit({
                "vlans": {
                    "vlan": [
                        {"name": "VLAN2999"},
                        {"description": {XML_ATTRIBUTES: {"operation": "delete"}}}
                    ]}})

        self.nc.commit()

        with self.assertRaises(RPCError):
            self.edit({
                "vlans": {
                    "vlan": {
                        "name": "VLAN3000",
                        XML_ATTRIBUTES: {"operation": "delete"}}}})

        with self.assertRaises(RPCError):
            self.edit({
                "vlans": {
                    "vlan": [
                        {"name": "VLAN2999"},
                        {"description": {XML_ATTRIBUTES: {"operation": "delete"}}}
                    ]}})

        self.cleanup(vlan("VLAN2999"))

    def test_access_mode(self):
        self.edit({
            "vlans": {
                "vlan": [
                    {"name": "VLAN2995"},
                    {"vlan-id": "2995"}]},
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "access",
                                "vlan": [
                                    {"members": "2995"},
                                ]}}}]}]}})
        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/3"}}}}
        }))

        assert_that(result.xpath("data/configuration/interfaces/interface"), has_length(1))

        int003 = result.xpath("data/configuration/interfaces/interface")[0]

        assert_that(int003.xpath("name")[0].text, equal_to("ge-0/0/3"))
        assert_that(int003.xpath("unit/family/ethernet-switching/*"), has_length(2))
        assert_that(int003.xpath("unit/family/ethernet-switching/interface-mode")[0].text,
                    equal_to("access"))
        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members"), has_length(1))
        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members")[0].text, equal_to("2995"))

        self.cleanup(vlan("VLAN2995"), interface("ge-0/0/3", ["interface-mode", "vlan"]))

    def test_assigning_unknown_vlan_in_a_range_raises(self):
        self.edit({
            "vlans": {
                "vlan": [
                    {"name": "VLAN2995"},
                    {"vlan-id": "2995"}]},
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "trunk",
                                "vlan": {"members": "2995-2996"}}}}]}]}})

        with self.assertRaises(RPCError):
            self.nc.commit()

    def test_assigning_unknown_vlan_raises(self):
        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "vlan": {"members": "2000"}}}}]}]}})

        with self.assertRaises(RPCError):
            self.nc.commit()

    def test_trunk_mode_does_not_allow_no_vlan_members(self):
        self.edit({
            "vlans": [
                {"vlan": [
                    {"name": "VLAN2995"},
                    {"vlan-id": "2995"}]},
                {"vlan": [
                    {"name": "VLAN2996"},
                    {"vlan-id": "2996"}]},
                {"vlan": [
                    {"name": "VLAN2997"},
                    {"vlan-id": "2997"}]},
            ],
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"native-vlan-id": "2996"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "trunk"
                                }}}]}]}})
        with self.assertRaises(RPCError) as context:
            self.nc.commit()

            assert_that(etree.tostring(context.exception._raw.xpath('/*/*')[0]), xml_equals_to(
        """<?xml version="1.0" encoding="UTF-8"?><commit-results xmlns="urn:ietf:params:xml:ns:netconf:base:1.0" xmlns:junos="http://xml.juniper.net/junos/11.4R1/junos">
<rpc-error>
  <error-tag>operation-failed</error-tag>
  <error-message>
For trunk interface, please ensure either vlan members is configured or inner-vlan-id-list is configured
</error-message>
  <error-severity>error</error-severity>
  <error-path>
[edit interfaces ge-0/0/3 unit 0 family]
</error-path>
  <error-type>protocol</error-type>
  <error-info>
    <bad-element>ethernet-switching</bad-element>
  </error-info>
</rpc-error>
<rpc-error>
  <error-severity>error</error-severity>
  <error-tag>operation-failed</error-tag>
  <error-type>protocol</error-type>
  <error-message>
configuration check-out failed
</error-message>
</rpc-error>
</commit-results>"""))

        self.cleanup(vlan("VLAN2995"), vlan("VLAN2996"), vlan("VLAN2997"),
                         interface("ge-0/0/3", ["interface-mode"]))
        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"vlans": {}}}
        }))
        assert_that(result.xpath("data/configuration/vlans/vlan"), has_length(0))

    def test_trunk_mode(self):
        self.edit({
            "vlans": [
                {"vlan": [
                    {"name": "VLAN2995"},
                    {"vlan-id": "2995"}]},
                {"vlan": [
                    {"name": "VLAN2996"},
                    {"vlan-id": "2996"}]},
                {"vlan": [
                    {"name": "VLAN2997"},
                    {"vlan-id": "2997"}]},
            ],
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"native-vlan-id": "2996"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "trunk",
                                "vlan": [
                                    {"members": "2995"},
                                    {"members": "2997"},
                                ]}}}]}]}})
        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/3"}}}}
        }))

        assert_that(result.xpath("data/configuration/interfaces/interface"), has_length(1))

        int003 = result.xpath("data/configuration/interfaces/interface")[0]

        assert_that(int003.xpath("name")[0].text, equal_to("ge-0/0/3"))
        assert_that(int003.xpath("native-vlan-id")[0].text, equal_to("2996"))
        assert_that(int003.xpath("unit/family/ethernet-switching/*"), has_length(2))
        assert_that(int003.xpath("unit/family/ethernet-switching/interface-mode")[0].text,
                    equal_to("trunk"))
        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members"), has_length(2))
        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members")[0].text, equal_to("2995"))
        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members")[1].text, equal_to("2997"))

        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "vlan": [
                                    {"members": {XML_TEXT: "2995", XML_ATTRIBUTES: {"operation": "delete"}}},
                                ]}}}]}]}})
        self.nc.commit()
        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/3"}}}}
        }))
        int003 = result.xpath("data/configuration/interfaces/interface")[0]
        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members"), has_length(1))
        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members")[0].text, equal_to("2997"))

        self.cleanup(vlan("VLAN2995"), vlan("VLAN2996"), vlan("VLAN2997"),
                     interface("ge-0/0/3", ["interface-mode", "vlan"]))
        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"vlans": {}}}
        }))
        assert_that(result.xpath("data/configuration/vlans/vlan"), has_length(0))

    def test_interface_trunk_native_vlan_merge(self):
        self.edit({
            "vlans": [
                {"vlan": [
                    {"name": "VLAN2995"},
                    {"vlan-id": "2995"}]},
                {"vlan": [
                    {"name": "VLAN2996"},
                    {"vlan-id": "2996"}]},
                {"vlan": [
                    {"name": "VLAN2997"},
                    {"vlan-id": "2997"}]},
            ],
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"native-vlan-id": "2995"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "trunk",
                                "vlan": [
                                    {"members": "2997"},
                                ]}}}]}]}})
        self.nc.commit()

        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"native-vlan-id": "2996"},
                    ]}})
        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/3"}}}}
        }))

        assert_that(result.xpath("data/configuration/interfaces/interface"), has_length(1))

        int003 = result.xpath("data/configuration/interfaces/interface")[0]
        assert_that(int003.xpath("native-vlan-id")[0].text, equal_to("2996"))

        self.cleanup(vlan("VLAN2995"), vlan("VLAN2996"), vlan("VLAN2997"),
                     interface("ge-0/0/3", ["interface-mode", "vlan"]))
        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"vlans": {}}}
        }))
        assert_that(result.xpath("data/configuration/vlans/vlan"), has_length(0))

    def test_interface_set_trunk_native_vlan_then_set_members_after(self):
        self.edit({
            "vlans": [
                {"vlan": [
                    {"name": "VLAN2995"},
                    {"vlan-id": "2995"}]},
                {"vlan": [
                    {"name": "VLAN2996"},
                    {"vlan-id": "2996"}]},
                {"vlan": [
                    {"name": "VLAN2997"},
                    {"vlan-id": "2997"}]},
            ],
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "trunk",
                                "vlan": [
                                    {"members": "2996"}
                                ]
                            }}}]}]}})
        self.nc.commit()

        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"native-vlan-id": "2995"}
                    ]}})
        self.nc.commit()

        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "vlan": [
                                    {"members": "2997"},
                                ]}}}]}]}})
        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/3"}}}}
        }))

        assert_that(result.xpath("data/configuration/interfaces/interface"), has_length(1))

        int003 = result.xpath("data/configuration/interfaces/interface")[0]
        assert_that(int003.xpath("native-vlan-id")[0].text, equal_to("2995"))
        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members"), has_length(2))
        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members")[0].text, equal_to("2996"))
        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members")[1].text, equal_to("2997"))

        self.cleanup(vlan("VLAN2995"), vlan("VLAN2996"), vlan("VLAN2997"),
                     interface("ge-0/0/3", ["interface-mode", "vlan"]))
        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"vlans": {}}}
        }))
        assert_that(result.xpath("data/configuration/vlans/vlan"), has_length(0))

    def test_passing_from_trunk_mode_to_access_gets_rid_of_stuff_in_trunk_mode(self):

        self.edit({
            "vlans": [
                {"vlan": [
                    {"name": "VLAN1100"},
                    {"vlan-id": "1100"}]},
                {"vlan": [
                    {"name": "VLAN1200"},
                    {"vlan-id": "1200"}]},
                {"vlan": [
                    {"name": "VLAN1300"},
                    {"vlan-id": "1300"}]},
                {"vlan": [
                    {"name": "VLAN1400"},
                    {"vlan-id": "1400"}]},
            ]})
        self.nc.commit()

        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"native-vlan-id": "1200"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "trunk",
                                "vlan": [
                                    {"members": "1100"},
                                    {"members": "1300"},
                                    {"members": "1400"},
                                ]}}}]}]}})
        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/3"}}}}
        }))

        assert_that(result.xpath("data/configuration/interfaces/interface"), has_length(1))

        int003 = result.xpath("data/configuration/interfaces/interface")[0]
        assert_that(int003.xpath("native-vlan-id")[0].text, equal_to("1200"))
        assert_that(int003.xpath("unit/family/ethernet-switching/interface-mode")[0].text, equal_to("trunk"))
        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members"), has_length(3))

        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "access"
                            }}}]}]}})
        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/3"}}}}
        }))

        int003 = result.xpath("data/configuration/interfaces/interface")[0]
        assert_that(int003.xpath("native-vlan-id"), has_length(0))
        assert_that(int003.xpath("unit/family/ethernet-switching/interface-mode")[0].text, equal_to("access"))
        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members"), has_length(0))

        self.cleanup(vlan("VLAN1100"), vlan("VLAN1200"), vlan("VLAN1300"), vlan("VLAN1400"),
                     interface("ge-0/0/3", ["interface-mode", "vlan"]))
        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"vlans": {}}}
        }))
        assert_that(result.xpath("data/configuration/vlans/vlan"), has_length(0))

    def test_display_interface_with_description_and_trunk_native_vlan(self):
        self.edit({
            "vlans": [
                {"vlan": [
                    {"name": "VLAN2995"},
                    {"vlan-id": "2995"}]},
                {"vlan": [
                    {"name": "VLAN2996"},
                    {"vlan-id": "2996"}]},
                {"vlan": [
                    {"name": "VLAN2997"},
                    {"vlan-id": "2997"}]},
            ],
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"description": "I see what you did there!"},
                    {"native-vlan-id": "2996"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "trunk",
                                "vlan": [
                                    {"members": "2995"},
                                    {"members": "2997"},
                                ]}}}]}]}})
        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/3"}}}}
        }))

        assert_that(result.xpath("data/configuration/interfaces/interface"), has_length(1))

        int003 = result.xpath("data/configuration/interfaces/interface")[0]
        assert_that(int003.xpath("name")[0].text, equal_to("ge-0/0/3"))
        assert_that(int003.xpath("native-vlan-id")[0].text, equal_to("2996"))
        assert_that(int003.xpath("description")[0].text, equal_to("I see what you did there!"))

        assert_that(int003.xpath("unit/family/ethernet-switching/vlan/members")), has_length(2)

        members = int003.xpath("unit/family/ethernet-switching/vlan/members")
        assert_that(members[0].text, equal_to("2995"))
        assert_that(members[1].text, equal_to("2997"))

        self.cleanup(vlan("VLAN2995"), vlan("VLAN2996"), vlan("VLAN2997"),
                     interface("ge-0/0/3", ["interface-mode", "vlan"]))
        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"vlans": {}}}
        }))
        assert_that(result.xpath("data/configuration/vlans/vlan"), has_length(0))

    def test_assigning_unknown_native_vlan_raises(self):
        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"native-vlan-id": "2000"}
                    ]}})

        with self.assertRaises(RPCError):
            self.nc.commit()

    def test_display_interface_trunk_native_vlan_and_no_ethernet_switching(self):
        self.edit({
            "vlans": [
                {"vlan": [
                    {"name": "VLAN2996"},
                    {"vlan-id": "2996"}]}
            ],
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/3"},
                    {"native-vlan-id": "2996"}
                    ]}})
        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/3"}}}}
        }))

        assert_that(result.xpath("data/configuration/interfaces/interface"), has_length(1))

        int003 = result.xpath("data/configuration/interfaces/interface")[0]
        assert_that(int003.xpath("name")[0].text, equal_to("ge-0/0/3"))
        assert_that(int003.xpath("native-vlan-id")[0].text, equal_to("2996"))

        self.cleanup(vlan("VLAN2996"), interface("ge-0/0/3"))
        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"vlans": {}}}
        }))
        assert_that(result.xpath("data/configuration/vlans/vlan"), has_length(0))

    def test_set_spanning_tree_options(self):
        self.edit({
            "protocols": {
                "rstp": {
                    "interface": [
                        {"name": "ge-0/0/3"},
                        {"edge": ""},
                        {"no-root-port": ""}]}}})

        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"protocols": {"rstp": {"interface": {"name": "ge-0/0/3"}}}}}
        }))

        assert_that(result.xpath("data/configuration/protocols/rstp/interface"), has_length(1))

        interface = result.xpath("data/configuration/protocols/rstp/interface")[0]

        assert_that(interface, has_length(3))
        assert_that(interface.xpath("name")[0].text, equal_to("ge-0/0/3"))
        assert_that(interface.xpath("edge"), has_length(1))
        assert_that(interface.xpath("no-root-port"), has_length(1))

        self.edit({
            "protocols": {
                "rstp": {
                    "interface": {
                        XML_ATTRIBUTES: {"operation": "delete"},
                        "name": "ge-0/0/3"}}}})

        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"protocols": ""}}
        }))

        assert_that(result.xpath("data/configuration/protocols"), has_length(1))

    def test_deleting_spanning_tree_options(self):
        self.edit({
            "protocols": {
                "rstp": {
                    "interface": [
                        {"name": "ge-0/0/3"},
                        {"edge": ""},
                        {"no-root-port": ""}]}}})

        self.nc.commit()

        self.edit({
            "protocols": {
                "rstp": {
                    "interface": [
                        {"name": "ge-0/0/3"},
                        {"edge": {XML_ATTRIBUTES: {"operation": "delete"}}},
                        {"no-root-port": {XML_ATTRIBUTES: {"operation": "delete"}}}]}}})

        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"protocols": {"rstp": {"interface": {"name": "ge-0/0/3"}}}}}
        }))

        assert_that(result.xpath("data/configuration/protocols/rstp/interface"), has_length(0))

    def test_set_lldp(self):
        self.edit({
            "protocols": {
                "lldp": {
                    "interface": [
                        {"name": "ge-0/0/3"},
                        {"disable": ""}]}}})

        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"protocols": {"lldp": {"interface": {"name": "ge-0/0/3"}}}}}
        }))

        assert_that(result.xpath("data/configuration/protocols/lldp/interface"), has_length(1))

        interface = result.xpath("data/configuration/protocols/lldp/interface")[0]

        assert_that(interface, has_length(2))
        assert_that(interface.xpath("name")[0].text, equal_to("ge-0/0/3"))
        assert_that(len(interface.xpath("disable")), equal_to(1))

        self.edit({
            "protocols": {
                "lldp": {
                    "interface": [
                        {"name": "ge-0/0/3"},
                        {"disable": {XML_ATTRIBUTES: {"operation": "delete"}}}]}}})

        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"protocols": {"lldp": {"interface": {"name": "ge-0/0/3"}}}}}
        }))
        assert_that(result.xpath("data/configuration/protocols/lldp/interface")[0], has_length(1))

        self.edit({
            "protocols": {
                "lldp": {
                    "interface": {
                        XML_ATTRIBUTES: {"operation": "delete"},
                        "name": "ge-0/0/3"}}}})

        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"protocols": ""}}
        }))

        assert_that(result.xpath("data/configuration/protocols"), has_length(1))

    def test_set_interface_description(self):
        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/2"},
                    {"description": "Hey there beautiful"}]}})

        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/2"}}}}
        }))

        assert_that(result.xpath("data/configuration/interfaces/interface"), has_length(1))

        int002 = result.xpath("data/configuration/interfaces/interface")[0]

        assert_that(int002.xpath("name")[0].text, equal_to("ge-0/0/2"))
        assert_that(int002.xpath("description")[0].text, equal_to("Hey there beautiful"))

        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/2"},
                    {"description": {XML_ATTRIBUTES: {"operation": "delete"}}}]}})

        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/2"}}}}
        }))

        assert_that(result.xpath("data/configuration/interfaces/interface"), has_length(1))

        int002 = result.xpath("data/configuration/interfaces/interface")[0]

        assert_that(int002.xpath("description"), has_length(0))

    def test_set_interface_disabling(self):
        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/2"}}}}}))

        int002 = result.xpath("data/configuration/interfaces/interface")[0]
        assert_that(int002.xpath("disable"), has_length(0))

        self.edit({"interfaces": {"interface": [{"name": "ge-0/0/2"}, {"disable": ""}]}})
        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/2"}}}}}))

        int002 = result.xpath("data/configuration/interfaces/interface")[0]
        assert_that(int002.xpath("disable"), has_length(1))

        self.edit({"interfaces": {
            "interface": [{"name": "ge-0/0/2"}, {"disable": {XML_ATTRIBUTES: {"operation": "delete"}}}]}})
        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/2"}}}}}))

        int002 = result.xpath("data/configuration/interfaces/interface")[0]
        assert_that(int002.xpath("disable"), has_length(0))

    def test_set_interface_trunk_native_vlan_id(self):
        self.edit({
            "vlans": [
                {"vlan": [
                    {"name": "VLAN2996"},
                    {"vlan-id": "2996"}]}
            ],
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/2"},
                    {"native-vlan-id": "2996"}]}})

        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/2"}}}}
        }))

        assert_that(result.xpath("data/configuration/interfaces/interface"), has_length(1))

        int002 = result.xpath("data/configuration/interfaces/interface")[0]

        assert_that(int002.xpath("name")[0].text, equal_to("ge-0/0/2"))
        assert_that(int002.xpath("native-vlan-id")[0].text, equal_to("2996"))

        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ge-0/0/2"},
                    {"native-vlan-id": {XML_ATTRIBUTES: {"operation": "delete"}}}]}})

        self.nc.commit()

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ge-0/0/2"}}}}
        }))

        assert_that(result.xpath("data/configuration/interfaces/interface"), has_length(1))

        int002 = result.xpath("data/configuration/interfaces/interface")[0]

        assert_that(int002.xpath("native-vlan-id"), has_length(0))

    def test_create_aggregated_port(self):
        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ae1"},
                    {"description": "This is a Greg hated"}]}})
        self.nc.commit()

        ae1 = self.get_interface("ae1")
        assert_that(ae1.xpath("*"), has_length(2))
        assert_that(ae1.xpath("description")[0].text, is_("This is a Greg hated"))

        self.edit({
            "interfaces": {
                "interface": [
                    {"name": "ae1"},
                    {"description": {XML_ATTRIBUTES: {"operation": "delete"}}},
                    {"aggregated-ether-options": {
                        "link-speed": "10g",
                        "auto-negotiation": {},
                        "lacp": {
                            "active": {},
                            "periodic": "slow"}}}]}})
        self.nc.commit()

        ae1 = self.get_interface("ae1")
        assert_that(ae1.xpath("*"), has_length(2))
        assert_that(ae1.xpath("aggregated-ether-options/*"), has_length(3))
        assert_that(ae1.xpath("aggregated-ether-options/link-speed")[0].text, is_("10g"))
        assert_that(ae1.xpath("aggregated-ether-options/auto-negotiation"), has_length(1))
        assert_that(ae1.xpath("aggregated-ether-options/lacp/*"), has_length(2))
        assert_that(ae1.xpath("aggregated-ether-options/lacp/active"), has_length(1))
        assert_that(ae1.xpath("aggregated-ether-options/lacp/periodic")[0].text, is_("slow"))

        self.edit({
            "vlans": [
                {"vlan": [
                    {"name": "VLAN2995"},
                    {"vlan-id": "2995"}]},
                {"vlan": [
                    {"name": "VLAN2997"},
                    {"vlan-id": "2997"}]},
            ],
            "interfaces": {
                "interface": [
                    {"name": "ae1"},
                    {"aggregated-ether-options": {
                        "link-speed": {XML_ATTRIBUTES: {"operation": "delete"}},
                        "auto-negotiation": {XML_ATTRIBUTES: {"operation": "delete"}},
                        "lacp": {
                            "active": {XML_ATTRIBUTES: {"operation": "delete"}},
                            "periodic": "slow"}}},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "trunk",
                                "vlan": [
                                    {"members": "2995"},
                                    {"members": "2997"}]}}}]}]}})
        self.nc.commit()

        ae1 = self.get_interface("ae1")
        assert_that(ae1.xpath("*"), has_length(3))
        assert_that(ae1.xpath("aggregated-ether-options/*"), has_length(1))
        assert_that(ae1.xpath("aggregated-ether-options/lacp/periodic")[0].text, is_("slow"))
        assert_that(ae1.xpath("unit/family/ethernet-switching/vlan/members"), has_length(2))

        self.cleanup(vlan("VLAN2995"), vlan("VLAN2997"), interface("ae1"))

        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": "ae1"}}}}}))

        assert_that(result.xpath("configuration/interfaces"), has_length(0))

    def test_assign_port_to_aggregated_interface(self):
        self.edit({
            "vlans": [
                {"vlan": [
                    {"name": "VLAN2995"},
                    {"vlan-id": "2995"}]},
            ],
            "interfaces": [
                {"interface": [
                    {"name": "ge-0/0/1"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "access"}}}]}]},
                {"interface": [
                    {"name": "ge-0/0/2"},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "access"}}}]}]},
            ]})
        self.nc.commit()

        self.edit({
            "vlans": [
                {"vlan": [
                    {"name": "VLAN2995"},
                    {"vlan-id": "2995"}]},
            ],
            "interfaces": [
                {"interface": [
                    {"name": "ae1"},
                    {"aggregated-ether-options": {
                        "link-speed": "10g",
                        "lacp": {
                            "active": {},
                            "periodic": "slow"}}},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {
                                "interface-mode": "trunk",
                                "vlan": [
                                    {"members": "2995"}]}}}]}]},
                {"interface": [
                    {"name": "ge-0/0/1"},
                    {"ether-options": {
                        "auto-negotiation": {},
                        "speed": {"ethernet-10g": {}},
                        "ieee-802.3ad": {"bundle": "ae1"}}},
                    {"unit": {XML_ATTRIBUTES: {"operation": "delete"}}}]},
                {"interface": [{XML_ATTRIBUTES: {"operation": "replace"}},
                               {"name": "ge-0/0/2"},
                               {"ether-options": {
                                   "speed": {"ethernet-10g": {}},
                                   "ieee-802.3ad": {"bundle": "ae1"}}}]},
            ]})
        self.nc.commit()

        ge001 = self.get_interface("ge-0/0/1")
        assert_that(ge001.xpath("*"), has_length(2))
        assert_that(ge001.xpath("unit"), has_length(0))
        assert_that(ge001.xpath("ether-options/*"), has_length(3))
        assert_that(ge001.xpath("ether-options/auto-negotiation"), has_length(1))
        assert_that(ge001.xpath("ether-options/speed/ethernet-10g"), has_length(1))
        assert_that(ge001.xpath("ether-options/ieee-802.3ad/bundle")[0].text, is_("ae1"))

        ge002 = self.get_interface("ge-0/0/2")
        assert_that(ge002.xpath("*"), has_length(2))
        assert_that(ge002.xpath("unit"), has_length(0))
        assert_that(ge002.xpath("ether-options/*"), has_length(2))
        assert_that(ge002.xpath("ether-options/speed/ethernet-10g"), has_length(1))
        assert_that(ge002.xpath("ether-options/ieee-802.3ad/bundle")[0].text, is_("ae1"))

        self.edit({
            "interfaces": [
                {"interface": [
                    {"name": "ge-0/0/1"},
                    {"ether-options": {
                        "auto-negotiation": {XML_ATTRIBUTES: {"operation": "delete"}},
                        "speed": "10g",
                        "ieee-802.3ad": {XML_ATTRIBUTES: {"operation": "delete"}}}}]},
                {"interface": [
                    {"name": "ge-0/0/2"},
                    {"ether-options": {XML_ATTRIBUTES: {"operation": "delete"}}}]},
            ]})
        self.nc.commit()

        ge001 = self.get_interface("ge-0/0/1")
        assert_that(ge001.xpath("unit"), has_length(0))
        assert_that(ge001.xpath("ether-options/*"), has_length(1))
        assert_that(ge001.xpath("ether-options/speed/ethernet-10g"), has_length(1))

        ge002 = self.get_interface("ge-0/0/2")
        assert_that(ge002.xpath("*"), has_length(1))
        assert_that(ge002.xpath("unit"), has_length(0))
        assert_that(ge002.xpath("ether-options"), has_length(0))

        self.cleanup(vlan("VLAN2995"), interface("ae1"), reset_interface("ge-0/0/1"), reset_interface("ge-0/0/2"))

    def test_compare_configuration(self):

        result = self.nc.compare_configuration()

        output = result.xpath("configuration-information/configuration-output")[0]
        assert_that(output.text.strip(), is_(""))

        self.edit({
            "vlans": [
                {"vlan": [
                    {"name": "VLAN2995"},
                    {"vlan-id": "2995"}]},
            ]})

        result = self.nc.compare_configuration()

        output = result.xpath("configuration-information/configuration-output")[0]
        assert_that(output.text.strip(), is_not(""))

        self.nc.commit()

        result = self.nc.compare_configuration()

        output = result.xpath("configuration-information/configuration-output")[0]
        assert_that(output.text.strip(), is_(""))

    def edit(self, config):
        result = self.nc.edit_config(target="candidate", config=dict_2_etree({
            "config": {
                "configuration": config
            }
        }))
        assert_that(result.xpath("//rpc-reply/ok"), has_length(1))

    def cleanup(self, *args):
        for cleanup in args:
            cleanup(self.edit)
        self.nc.commit()

    def get_interface(self, name):
        result = self.nc.get_config(source="running", filter=dict_2_etree({"filter": {
            "configuration": {"interfaces": {"interface": {"name": name}}}}}))

        return result.xpath("data/configuration/interfaces/interface")[0]


def vlan(vlan_name):
    def m(edit):
        edit({"vlans": {
            "vlan": {"name": vlan_name, XML_ATTRIBUTES: {"operation": "delete"}}
        }})

    return m


def interface(interface_name, fields=None, native_vlan_id=None):
    if fields is not None:
        def m(edit):
            edit({"interfaces": {
                "interface": [
                    {"name": interface_name},
                    {"native-vlan-id": {XML_ATTRIBUTES: {"operation": "delete"}}},
                    {"unit": [
                        {"name": "0"},
                        {"family": {
                            "ethernet-switching": {field: {XML_ATTRIBUTES: {"operation": "delete"}} for field in fields}
                        }}]}]}})
    else:
        def m(edit):
            edit({"interfaces": {
                "interface": [{
                    "name": interface_name,
                    XML_ATTRIBUTES: {"operation": "delete"}},
                    {"native-vlan-id": {XML_ATTRIBUTES: {"operation": "delete"}}}
                ]}})

    return m


def reset_interface(interface_name):
    def m(edit):
        edit({"interfaces": {
            "interface": [{XML_ATTRIBUTES: {"operation": "replace"}},
                          {"name": interface_name},
                          {"native-vlan-id": ""},
                          {"unit": [
                              {"name": "0"},
                              {"family": {
                                  "ethernet-switching": {}}}]}]}})

    return m
