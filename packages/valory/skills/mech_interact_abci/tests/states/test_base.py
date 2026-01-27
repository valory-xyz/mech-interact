# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2025-2026 Valory AG
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
# ------------------------------------------------------------------------------

"""This package contains tests for the base module of the states."""

import time
from typing import Any, Dict, List

import pytest

from packages.valory.skills.mech_interact_abci.states.base import (
    HALF_LIFE_SECONDS,
    MechInfo,
    Service,
)


TWO_MIN_IN_SEC = 2 * 60


class TestMechInfo:
    """Test the `MechInfo` class."""

    @pytest.mark.parametrize(
        ("kwargs",),
        (
            (
                dict(
                    id="mech_1",
                    address="0x1",
                    service={
                        "metadata": [{"metadata": "metadata"}],
                        "deliveries": [{"blockTimestamp": 98}],
                    },
                    karma="1",
                    receivedRequests="1",
                    selfDeliveredFromReceived="1",
                    maxDeliveryRate="1",
                ),
            ),
            (
                dict(
                    id="mech_2",
                    address="0x2",
                    service={
                        "metadata": [{"metadata": "0xmetadata"}],
                        "deliveries": [{"blockTimestamp": 325}],
                    },
                    karma="-123",
                    receivedRequests="100",
                    selfDeliveredFromReceived="13",
                    maxDeliveryRate="1000",
                ),
            ),
        ),
    )
    def test_init(self, kwargs: Dict[str, Any]) -> None:
        """Test the `__init__` method."""
        instance = MechInfo(**kwargs)
        assert instance.id == kwargs["id"]
        assert instance.address == kwargs["address"]
        assert instance.service == Service(**kwargs["service"])
        assert instance.karma == int(kwargs["karma"])
        assert instance.received_requests == int(kwargs["receivedRequests"])
        assert instance.self_delivered == int(kwargs["selfDeliveredFromReceived"])
        assert instance.max_delivery_rate == int(kwargs["maxDeliveryRate"])
        assert (
            instance.delivered_ratio
            == instance.self_delivered / instance.received_requests
        )
        assert instance.relevant_tools == set()

    @pytest.mark.parametrize(
        ("ranked_kwargs",),
        (
            (
                # simple example
                [
                    dict(
                        id="mech_0",
                        address="0x0",
                        service={
                            "metadata": [{"metadata": "metadata"}],
                            "deliveries": [
                                {"blockTimestamp": int(time.time()) - TWO_MIN_IN_SEC}
                            ],
                        },
                        karma="1",
                        receivedRequests="1",
                        selfDeliveredFromReceived="1",
                        maxDeliveryRate="1",
                    ),
                    dict(
                        id="mech_1",
                        address="0x1",
                        service={
                            "metadata": [{"metadata": "0xmetadata"}],
                            "deliveries": [
                                {"blockTimestamp": int(time.time()) - TWO_MIN_IN_SEC}
                            ],
                        },
                        karma="-123",
                        receivedRequests="100",
                        selfDeliveredFromReceived="85",
                        maxDeliveryRate="1000",
                    ),
                    dict(
                        id="mech_2",
                        address="0x2",
                        service={
                            "metadata": [{"metadata": "metadata"}],
                            "deliveries": [
                                {"blockTimestamp": int(time.time()) - TWO_MIN_IN_SEC}
                            ],
                        },
                        karma="-123",
                        receivedRequests="0",
                        selfDeliveredFromReceived="0",
                        maxDeliveryRate="1000",
                    ),
                    dict(
                        id="mech_1b",
                        address="0x1b",
                        service={
                            "metadata": [{"metadata": "0xmetadata"}],
                            "deliveries": [
                                {"blockTimestamp": int(time.time()) - TWO_MIN_IN_SEC}
                            ],
                        },
                        karma="-123",
                        receivedRequests="100",
                        selfDeliveredFromReceived="70",
                        maxDeliveryRate="1000",
                    ),
                    dict(
                        id="mech_2b",
                        address="0x2b",
                        service={
                            "metadata": [{"metadata": "metadata"}],
                            "deliveries": [
                                {"blockTimestamp": int(time.time()) - TWO_MIN_IN_SEC}
                            ],
                        },
                        karma="-123",
                        receivedRequests="3",
                        selfDeliveredFromReceived="0",
                        maxDeliveryRate="1000",
                    ),
                    dict(
                        id="mech_1c",
                        address="0x1c",
                        service={
                            "metadata": [{"metadata": "0xmetadata"}],
                            "deliveries": [
                                {"blockTimestamp": int(time.time()) - TWO_MIN_IN_SEC}
                            ],
                        },
                        karma="-123",
                        receivedRequests="100",
                        selfDeliveredFromReceived="50",
                        maxDeliveryRate="1000",
                    ),
                    # better karma, perfect delivery rate, but terrible liveness -> should end up last in the ranking
                    dict(
                        id="mech_3",
                        address="0x3",
                        service={
                            "metadata": [{"metadata": ""}],
                            "deliveries": [
                                {
                                    "blockTimestamp": int(time.time())
                                    - HALF_LIFE_SECONDS * 2
                                }
                            ],
                        },
                        karma="123",
                        receivedRequests="100",
                        selfDeliveredFromReceived="100",
                        maxDeliveryRate="1",
                    ),
                ],
            ),
            (
                # current mechs example
                [
                    dict(
                        id="2010",
                        address="0x61b962bf1cf91224b0967c7e726c8ce597569983",
                        maxDeliveryRate="1",
                        karma="232",
                        receivedRequests="235",
                        selfDeliveredFromReceived="232",
                        service={
                            "metadata": [],
                            "deliveries": [{"blockTimestamp": "1742995420"}],
                        },
                    ),
                    dict(
                        id="1966",
                        address="0x895c50590a516b451668a620a9ef9b8286b9e72d",
                        maxDeliveryRate="1",
                        karma="93",
                        receivedRequests="94",
                        selfDeliveredFromReceived="93",
                        service={
                            "metadata": [],
                            "deliveries": [{"blockTimestamp": "1741341490"}],
                        },
                    ),
                    dict(
                        id="2095",
                        address="0x55426a0b38e05fd4ff82a92c276cdc4f0f58bc36",
                        maxDeliveryRate="1",
                        karma="1",
                        receivedRequests="2",
                        selfDeliveredFromReceived="1",
                        service={
                            "metadata": [],
                            "deliveries": [{"blockTimestamp": "1745417305"}],
                        },
                    ),
                    dict(
                        id="1841",
                        address="0x15719caecfafb1b1356255cb167cd2a73bd1555d",
                        maxDeliveryRate="10000000000000000",
                        karma="253",
                        receivedRequests="253",
                        selfDeliveredFromReceived="253",
                        service={
                            "metadata": [],
                            "deliveries": [{"blockTimestamp": "1755087440"}],
                        },
                    ),
                    dict(
                        id="1983",
                        address="0xce90357349f87b72dbca6078a0ebf39fddd417fa",
                        maxDeliveryRate="100",
                        karma="65",
                        receivedRequests="70",
                        selfDeliveredFromReceived="65",
                        service={
                            "metadata": [],
                            "deliveries": [{"blockTimestamp": "1745919030"}],
                        },
                    ),
                    dict(
                        id="2267",
                        address="0xe43a68c509886b6eb1147c7cfb20cacec1cea32b",
                        maxDeliveryRate="1",
                        karma="174",
                        receivedRequests="187",
                        selfDeliveredFromReceived="135",
                        service={
                            "metadata": [
                                {
                                    "metadata": "0x86c1006a54ef9c100279b72eeb480682d407f120e4659ac0e4afe1d4e92da336"
                                }
                            ],
                            "deliveries": [{"blockTimestamp": "1763388685"}],
                        },
                    ),
                    dict(
                        id="2135",
                        address="0xbead38e4c4777341bb3fd44e8cd4d1ba1a7ad9d7",
                        maxDeliveryRate="10000000000000000",
                        karma="385",
                        receivedRequests="409",
                        selfDeliveredFromReceived="387",
                        service={
                            "metadata": [
                                {
                                    "metadata": "0x157d3b106831e2713b86af1b52af76a3ef28c52ae0853e9638180902ebee41d4"
                                }
                            ],
                            "deliveries": [{"blockTimestamp": "1755177585"}],
                        },
                    ),
                    dict(
                        id="2340",
                        address="0xdb78159e9246ec738f51c2c9cb1169b5c0e45fee",
                        maxDeliveryRate="10000000000000000",
                        karma="12546",
                        receivedRequests="7",
                        selfDeliveredFromReceived="7",
                        service={
                            "metadata": [
                                {
                                    "metadata": "0x4d82a931d803e2b46b0dcd53f558f8de8305fd44b36288b42287ef1450a6611f"
                                }
                            ],
                            "deliveries": [{"blockTimestamp": "1764061690"}],
                        },
                    ),
                    dict(
                        id="2093",
                        address="0x7771674030b1fac454a292a3ecad0537c798769f",
                        maxDeliveryRate="10000000000000000",
                        karma="48",
                        receivedRequests="51",
                        selfDeliveredFromReceived="48",
                        service={
                            "metadata": [],
                            "deliveries": [{"blockTimestamp": "1754470330"}],
                        },
                    ),
                    dict(
                        id="1999",
                        address="0xa61026515b701c9a123b0587fd601857f368127a",
                        maxDeliveryRate="150000000000000000",
                        karma="17",
                        receivedRequests="18",
                        selfDeliveredFromReceived="17",
                        service={
                            "metadata": [],
                            "deliveries": [{"blockTimestamp": "1742909160"}],
                        },
                    ),
                    dict(
                        id="2360",
                        address="0x11c4389bf449991d69f89f941c3e79d5d828f1bc",
                        maxDeliveryRate="10000000000000000",
                        karma="8592",
                        receivedRequests="1771",
                        selfDeliveredFromReceived="1565",
                        service={
                            "metadata": [
                                {
                                    "metadata": "0x4d82a931d803e2b46b0dcd53f558f8de8305fd44b36288b42287ef1450a6611f"
                                }
                            ],
                            "deliveries": [{"blockTimestamp": "1764066885"}],
                        },
                    ),
                    dict(
                        id="2266",
                        address="0xd2949b547c4f226d2e9e6e2351a6dfd2e4c1dea0",
                        maxDeliveryRate="10000000000000000",
                        karma="12",
                        receivedRequests="16",
                        selfDeliveredFromReceived="14",
                        service={
                            "metadata": [
                                {
                                    "metadata": "0x9f0aebb77a103b8c58e6205d797ddc4e607c3d8dda8a343a323e2fd7618afae3"
                                }
                            ],
                            "deliveries": [{"blockTimestamp": "1756215090"}],
                        },
                    ),
                    dict(
                        id="2359",
                        address="0x818df8dcd43d716a7263798c99a2fc8e27010711",
                        maxDeliveryRate="10000000000000000",
                        karma="8463",
                        receivedRequests="6",
                        selfDeliveredFromReceived="5",
                        service={
                            "metadata": [
                                {
                                    "metadata": "0x4d82a931d803e2b46b0dcd53f558f8de8305fd44b36288b42287ef1450a6611f"
                                }
                            ],
                            "deliveries": [{"blockTimestamp": "1764059340"}],
                        },
                    ),
                    dict(
                        id="2198",
                        address="0x601024e27f1c67b28209e24272ced8a31fc8151f",
                        maxDeliveryRate="10000000000000000",
                        karma="66970",
                        receivedRequests="63725",
                        selfDeliveredFromReceived="52977",
                        service={
                            "metadata": [
                                {
                                    "metadata": "0x4d82a931d803e2b46b0dcd53f558f8de8305fd44b36288b42287ef1450a6611f"
                                }
                            ],
                            "deliveries": [{"blockTimestamp": "1764079180"}],
                        },
                    ),
                    dict(
                        id="2150",
                        address="0xfacaa9dd513af6b5a79b73353daff041925d0101",
                        maxDeliveryRate="10000000000000000",
                        karma="200",
                        receivedRequests="228",
                        selfDeliveredFromReceived="155",
                        service={
                            "metadata": [
                                {
                                    "metadata": "0x157d3b106831e2713b86af1b52af76a3ef28c52ae0853e9638180902ebee41d4"
                                }
                            ],
                            "deliveries": [{"blockTimestamp": "1763122415"}],
                        },
                    ),
                    dict(
                        id="2235",
                        address="0xb3c6319962484602b00d5587e965946890b82101",
                        maxDeliveryRate="10000000000000000",
                        karma="75122",
                        receivedRequests="147898",
                        selfDeliveredFromReceived="70951",
                        service={
                            "metadata": [
                                {
                                    "metadata": "0x4d82a931d803e2b46b0dcd53f558f8de8305fd44b36288b42287ef1450a6611f"
                                }
                            ],
                            "deliveries": [{"blockTimestamp": "1764079385"}],
                        },
                    ),
                    dict(
                        id="2182",
                        address="0xc05e7412439bd7e91730a6880e18d5d5873f632c",
                        maxDeliveryRate="10000000000000000",
                        karma="321277",
                        receivedRequests="815985",
                        selfDeliveredFromReceived="364056",
                        service={
                            "metadata": [
                                {
                                    "metadata": "0x4d82a931d803e2b46b0dcd53f558f8de8305fd44b36288b42287ef1450a6611f"
                                }
                            ],
                            "deliveries": [{"blockTimestamp": "1764079305"}],
                        },
                    ),
                    dict(
                        id="1722",
                        address="0x26928d3cf08ec37456d428c0cadb498968570e53",
                        maxDeliveryRate="100",
                        karma="-2006",
                        receivedRequests="4122",
                        selfDeliveredFromReceived="35",
                        service={
                            "metadata": [
                                {
                                    "metadata": "0x157d3b106831e2713b86af1b52af76a3ef28c52ae0853e9638180902ebee41d4"
                                }
                            ],
                            "deliveries": [{"blockTimestamp": "1764050385"}],
                        },
                    ),
                ],
            ),
        ),
    )
    def test_lt(self, ranked_kwargs: List[Dict[str, Any]]) -> None:
        """Test the `__lt__` method. Assumes kwargs are defined in such a way that they are ranked in given order."""
        # test parametrization checks
        ids = {kwargs["id"] for kwargs in ranked_kwargs}
        assert len(ids) == len(
            ranked_kwargs
        ), "All kwargs must have unique ids. Please check the test."

        # actual test
        instances = [MechInfo(**kwargs) for kwargs in ranked_kwargs]
        assert [i.id for i in instances] == [
            i.id for i in sorted(instances, reverse=True)
        ]
