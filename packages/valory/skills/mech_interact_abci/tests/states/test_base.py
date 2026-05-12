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

import json
import time
from dataclasses import asdict
from typing import Any, Dict, List

import pytest

from packages.valory.skills.abstract_round_abci.base import AbciAppDB
from packages.valory.skills.mech_interact_abci.states.base import (
    COLD_START_LIVENESS,
    Event,
    HALF_LIFE_SECONDS,
    MechInfo,
    MechInfoEncoder,
    MechInteractionResponse,
    MechMetadata,
    MechRequest,
    Service,
    SynchronizedData,
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

    def test_init_with_snake_case_fields(self) -> None:
        """Test init when snake_case fields are provided directly (non-zero)."""
        instance = MechInfo(
            id="mech_snake",
            address="0x1",
            service=Service(
                metadata=[{"metadata": "metadata"}],
                deliveries=[{"blockTimestamp": 100}],
            ),
            karma=5,
            received_requests=10,
            self_delivered=8,
            max_delivery_rate=100,
        )
        assert instance.received_requests == 10
        assert instance.self_delivered == 8
        assert instance.max_delivery_rate == 100

    def test_init_with_list_relevant_tools(self) -> None:
        """Test init when relevant_tools is a list."""
        # MechInfo.__post_init__ converts list/tuple to set; pass list deliberately.
        instance = MechInfo(
            id="mech_tools",
            address="0x1",
            service=Service(metadata=[{"metadata": "m"}], deliveries=[]),
            karma=1,
            receivedRequests=1,
            selfDeliveredFromReceived=1,
            maxDeliveryRate=1,
            relevant_tools=["tool1", "tool2"],  # type: ignore[arg-type]
        )
        assert instance.relevant_tools == {"tool1", "tool2"}

    def test_init_invalid_karma(self) -> None:
        """Test init raises ValueError for invalid karma."""
        with pytest.raises(ValueError, match="non-int"):
            # Deliberately pass non-int values to exercise error path.
            MechInfo(
                id="bad",
                address="0x1",
                service=Service(metadata=[], deliveries=[]),
                karma="not_a_number",  # type: ignore[arg-type]
                receivedRequests="1",  # type: ignore[arg-type]
                selfDeliveredFromReceived="1",  # type: ignore[arg-type]
                maxDeliveryRate="1",  # type: ignore[arg-type]
            )

    def test_init_invalid_received_requests(self) -> None:
        """Test init raises ValueError for invalid receivedRequests."""
        with pytest.raises(ValueError, match="non-int"):
            # Deliberately pass non-int values to exercise error path.
            MechInfo(
                id="bad",
                address="0x1",
                service=Service(metadata=[], deliveries=[]),
                karma="1",  # type: ignore[arg-type]
                receivedRequests="invalid",  # type: ignore[arg-type]
                selfDeliveredFromReceived="1",  # type: ignore[arg-type]
                maxDeliveryRate="1",  # type: ignore[arg-type]
            )

    def test_empty_metadata(self) -> None:
        """Test empty_metadata property."""
        instance = MechInfo(
            id="empty",
            address="0x1",
            service=Service(metadata=[], deliveries=[]),
            karma=1,
            receivedRequests=1,
            selfDeliveredFromReceived=1,
            maxDeliveryRate=1,
        )
        assert instance.empty_metadata is True

    def test_non_empty_metadata(self) -> None:
        """Test empty_metadata property when metadata exists."""
        instance = MechInfo(
            id="non_empty",
            address="0x1",
            service=Service(metadata=[{"metadata": "0xabc"}], deliveries=[]),
            karma=1,
            receivedRequests=1,
            selfDeliveredFromReceived=1,
            maxDeliveryRate=1,
        )
        assert instance.empty_metadata is False

    def test_liveness_zero_requests(self) -> None:
        """Test liveness returns cold start value when no requests."""
        instance = MechInfo(
            id="cold",
            address="0x1",
            service=Service(metadata=[{"metadata": "m"}], deliveries=[]),
            karma=1,
            receivedRequests=0,
            selfDeliveredFromReceived=0,
            maxDeliveryRate=1,
        )
        assert instance.liveness == COLD_START_LIVENESS

    def test_delivered_ratio_zero_requests(self) -> None:
        """Test delivered_ratio returns 0 when no requests."""
        instance = MechInfo(
            id="zero",
            address="0x1",
            service=Service(metadata=[{"metadata": "m"}], deliveries=[]),
            karma=1,
            receivedRequests=0,
            selfDeliveredFromReceived=0,
            maxDeliveryRate=1,
        )
        assert instance.delivered_ratio == 0.0

    def test_delivered_ratio_smoothed(self) -> None:
        """Test delivered_ratio_smoothed with Laplace smoothing."""
        instance = MechInfo(
            id="smooth",
            address="0x1",
            service=Service(metadata=[{"metadata": "m"}], deliveries=[]),
            karma=1,
            receivedRequests=10,
            selfDeliveredFromReceived=8,
            maxDeliveryRate=1,
        )
        assert instance.delivered_ratio_smoothed > 0
        assert instance.delivered_ratio_smoothed < 1

    def test_lt_tiebreak_by_karma(self) -> None:
        """Test __lt__ tiebreak uses karma when scores are equal."""
        common: Dict[str, Any] = dict(
            address="0x1",
            service=Service(metadata=[{"metadata": "m"}], deliveries=[]),
            receivedRequests=10,
            selfDeliveredFromReceived=8,
            maxDeliveryRate=1,
        )
        low_karma = MechInfo(id="low", karma=5, **common)
        high_karma = MechInfo(id="high", karma=50, **common)
        # same score, so tiebreak by karma: low_karma < high_karma
        assert low_karma < high_karma

    def test_delivery_rate_metric(self) -> None:
        """Test delivery_rate_metric property."""
        instance = MechInfo(
            id="rate",
            address="0x1",
            service=Service(metadata=[{"metadata": "m"}], deliveries=[]),
            karma=1,
            receivedRequests=1,
            selfDeliveredFromReceived=1,
            maxDeliveryRate=1,
        )
        assert instance.delivery_rate_metric > 0


class TestService:
    """Test the Service class."""

    def test_metadata_str_with_data(self) -> None:
        """Test metadata_str when metadata exists."""
        service = Service(
            metadata=[{"metadata": "0xabc123"}],
            deliveries=[],
        )
        assert service.metadata_str == "abc123"

    def test_metadata_str_none(self) -> None:
        """Test metadata_str when metadata is empty list."""
        service = Service(metadata=[], deliveries=[])
        assert service.metadata_str is None

    def test_metadata_str_missing_field(self) -> None:
        """Test metadata_str when field is missing from dict."""
        service = Service(metadata=[{"other": "value"}], deliveries=[])
        assert service.metadata_str is None

    def test_last_delivered_with_value(self) -> None:
        """Test last_delivered when deliveries exist."""
        service = Service(
            metadata=[],
            deliveries=[{"blockTimestamp": "12345"}],
        )
        assert service.last_delivered == 12345

    def test_last_delivered_none(self) -> None:
        """Test last_delivered when no deliveries."""
        service = Service(metadata=[], deliveries=[])
        assert service.last_delivered is None

    def test_last_delivered_invalid_value(self) -> None:
        """Test last_delivered when timestamp is invalid."""
        service = Service(metadata=[], deliveries=[{"blockTimestamp": "not_a_number"}])
        assert service.last_delivered is None

    def test_liveness_no_deliveries(self) -> None:
        """Test liveness when no deliveries."""
        service = Service(metadata=[], deliveries=[])
        assert service.liveness == 0

    def test_liveness_recent_delivery(self) -> None:
        """Test liveness for a recent delivery."""
        service = Service(
            metadata=[],
            deliveries=[{"blockTimestamp": str(int(time.time()) - 60)}],
        )
        assert service.liveness > 0.9

    def test_liveness_old_delivery(self) -> None:
        """Test liveness for an old delivery."""
        service = Service(
            metadata=[],
            deliveries=[
                {"blockTimestamp": str(int(time.time()) - HALF_LIFE_SECONDS * 10)}
            ],
        )
        assert service.liveness < 0.01


class TestMechMetadata:
    """Test the MechMetadata class."""

    def test_init(self) -> None:
        """Test init."""
        metadata = MechMetadata(prompt="test prompt", tool="test_tool", nonce="abc123")
        assert metadata.prompt == "test prompt"
        assert metadata.tool == "test_tool"
        assert metadata.nonce == "abc123"

    def test_default_schema_version(self) -> None:
        """Test that schema_version defaults to '2.0'."""
        meta = MechMetadata(prompt="q?", tool="t1", nonce="n1")
        assert meta.schema_version == "2.0"

    def test_default_request_context_is_none(self) -> None:
        """Test that request_context defaults to None."""
        meta = MechMetadata(prompt="q?", tool="t1", nonce="n1")
        assert meta.request_context is None

    def test_explicit_request_context(self) -> None:
        """Test constructing with an explicit request_context."""
        ctx = {"market_id": "0xabc", "type": "omen", "market_prob": 0.4}
        meta = MechMetadata(prompt="q?", tool="t1", nonce="n1", request_context=ctx)
        assert meta.request_context == ctx

    def test_explicit_schema_version_override(self) -> None:
        """Test that schema_version can be explicitly overridden."""
        meta = MechMetadata(prompt="q?", tool="t1", nonce="n1", schema_version="3.0")
        assert meta.schema_version == "3.0"

    def test_asdict_includes_new_fields(self) -> None:
        """Test that asdict() output includes schema_version and request_context."""
        ctx = {"market_id": "0xabc", "type": "omen"}
        meta = MechMetadata(prompt="q?", tool="t1", nonce="n1", request_context=ctx)
        d = asdict(meta)
        assert d["schema_version"] == "2.0"
        assert d["request_context"] == ctx

    def test_asdict_none_request_context(self) -> None:
        """Test that asdict() with no request_context produces null."""
        meta = MechMetadata(prompt="q?", tool="t1", nonce="n1")
        d = asdict(meta)
        assert d["schema_version"] == "2.0"
        assert d["request_context"] is None

    def test_serialization_round_trip(self) -> None:
        """Test that MechMetadata survives JSON serialization round-trip."""
        ctx = {"market_id": "0xabc", "type": "omen", "market_prob": 0.65}
        meta = MechMetadata(prompt="q?", tool="t1", nonce="n1", request_context=ctx)
        serialized = json.dumps(asdict(meta), sort_keys=True)
        deserialized = json.loads(serialized)
        restored = MechMetadata(**deserialized)
        assert restored.prompt == meta.prompt
        assert restored.tool == meta.tool
        assert restored.nonce == meta.nonce
        assert restored.schema_version == meta.schema_version
        assert restored.request_context == meta.request_context

    def test_backward_compatible_deserialization(self) -> None:
        """Test that old payloads without schema_version/request_context still deserialize."""
        old_payload: Dict[str, Any] = {"prompt": "q?", "tool": "t1", "nonce": "n1"}
        meta = MechMetadata(**old_payload)
        assert meta.schema_version == "2.0"
        assert meta.request_context is None

    def test_positional_construction_still_works(self) -> None:
        """Test that positional construction (prompt, tool, nonce) is backward compatible."""
        meta = MechMetadata("q?", "t1", "n1")
        assert meta.prompt == "q?"
        assert meta.tool == "t1"
        assert meta.nonce == "n1"
        assert meta.schema_version == "2.0"


class TestMechRequest:
    """Test the MechRequest class."""

    def test_defaults(self) -> None:
        """Test default values."""
        request = MechRequest()
        assert request.data == ""
        assert request.requestId == 0
        assert request.requestIds == []
        assert request.numRequests == 0


class TestMechInteractionResponse:
    """Test the MechInteractionResponse class."""

    def test_defaults(self) -> None:
        """Test default values."""
        response = MechInteractionResponse()
        assert response.nonce == ""
        assert response.result is None
        assert response.error == "Unknown"
        assert response.response_data is None
        assert response.sender_address is None

    def test_retries_exceeded(self) -> None:
        """Test retries_exceeded method."""
        response = MechInteractionResponse()
        response.retries_exceeded()
        assert "Retries were exceeded" in response.error

    def test_incorrect_format(self) -> None:
        """Test incorrect_format method."""
        response = MechInteractionResponse()
        response.incorrect_format("bad data")
        assert "unexpected" in response.error.lower()
        assert "bad data" in response.error


class TestMechInfoEncoder:
    """Test the MechInfoEncoder class."""

    def test_encode_mech_info(self) -> None:
        """Test encoding a MechInfo object."""
        info = MechInfo(
            id="1",
            address="0x1",
            service=Service(metadata=[{"metadata": "m"}], deliveries=[]),
            karma=1,
            receivedRequests=1,
            selfDeliveredFromReceived=1,
            maxDeliveryRate=1,
            relevant_tools={"tool1", "tool2"},
        )
        result = json.dumps(info, cls=MechInfoEncoder)
        parsed = json.loads(result)
        assert parsed["id"] == "1"
        assert parsed["address"] == "0x1"
        assert set(parsed["relevant_tools"]) == {"tool1", "tool2"}

    def test_encode_set(self) -> None:
        """Test encoding a plain set."""
        encoder = MechInfoEncoder()
        result = encoder.default({"a", "b"})
        assert set(result) == {"a", "b"}

    def test_encode_unknown_type(self) -> None:
        """Test encoding an unknown type raises TypeError."""
        encoder = MechInfoEncoder()
        with pytest.raises(TypeError):
            encoder.default(object())


class TestEvent:
    """Test the Event enum."""

    def test_all_events(self) -> None:
        """Test all events exist."""
        assert Event.DONE.value == "done"
        assert Event.NONE.value == "none"
        assert Event.V1.value == "v1"
        assert Event.V2.value == "v2"
        assert Event.NO_MARKETPLACE.value == "no_marketplace"
        assert Event.NO_MAJORITY.value == "no_majority"
        assert Event.ROUND_TIMEOUT.value == "round_timeout"
        assert Event.SKIP_REQUEST.value == "skip_request"
        assert Event.BUY_SUBSCRIPTION.value == "buy_subscription"


def _make_synced_data(**db_data: Any) -> SynchronizedData:
    """Create a SynchronizedData with given db values."""
    return SynchronizedData(db=AbciAppDB(setup_data=AbciAppDB.data_to_lists(db_data)))


class TestSynchronizedData:
    """Test SynchronizedData properties."""

    def test_mechs_info_from_serialized(self) -> None:
        """Test mechs_info when data is a JSON string."""
        info_data = [
            {
                "id": "1",
                "address": "0x1",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "1",
                "receivedRequests": "1",
                "selfDeliveredFromReceived": "1",
                "maxDeliveryRate": "1",
            }
        ]
        sd = _make_synced_data(mechs_info=json.dumps(info_data))
        result = sd.mechs_info
        assert len(result) == 1
        assert result[0].id == "1"

    def test_mechs_info_empty(self) -> None:
        """Test mechs_info when no data in db."""
        sd = _make_synced_data()
        result = sd.mechs_info
        assert result == []

    def test_mech_tool(self) -> None:
        """Test mech_tool property."""
        sd = _make_synced_data(mech_tool="openai-gpt-4")
        assert sd.mech_tool == "openai-gpt-4"

    def test_relevant_mechs_info(self) -> None:
        """Test relevant_mechs_info filters by mech_tool."""
        info_data = [
            {
                "id": "1",
                "address": "0x1",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "1",
                "receivedRequests": "1",
                "selfDeliveredFromReceived": "1",
                "maxDeliveryRate": "1",
                "relevant_tools": ["tool_a", "tool_b"],
            },
            {
                "id": "2",
                "address": "0x2",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "1",
                "receivedRequests": "1",
                "selfDeliveredFromReceived": "1",
                "maxDeliveryRate": "1",
                "relevant_tools": ["tool_c"],
            },
        ]
        sd = _make_synced_data(mechs_info=json.dumps(info_data), mech_tool="tool_a")
        result = sd.relevant_mechs_info
        assert len(result) == 1
        assert result[0].id == "1"

    def test_selected_mechs_empty_when_unset(self) -> None:
        """selected_mechs defaults to empty when the key is absent."""
        sd = _make_synced_data()
        assert sd.selected_mechs == []

    def test_selected_mechs_lowercases_addresses(self) -> None:
        """Consumers may send mixed-case addresses; selected_mechs returns lowercase."""
        sd = _make_synced_data(selected_mechs=json.dumps(["0xAbC", "0xDEF"]))
        assert sd.selected_mechs == ["0xabc", "0xdef"]

    def test_selected_mechs_falls_back_on_malformed_json(self) -> None:
        """A bad write (not-json or wrong shape) is tolerated, not raised."""
        sd = _make_synced_data(selected_mechs="not-json")
        assert sd.selected_mechs == []

    def test_selected_mechs_falls_back_on_wrong_shape(self) -> None:
        """JSON that decodes to a non-iterable yields an empty list."""
        sd = _make_synced_data(selected_mechs=json.dumps(42))
        assert sd.selected_mechs == []

    def test_relevant_mechs_info_filters_by_selected_mechs(self) -> None:
        """Mechs not in `selected_mechs` are dropped from relevant_mechs_info."""
        info_data = [
            {
                "id": "1",
                "address": "0x1",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "1",
                "receivedRequests": "1",
                "selfDeliveredFromReceived": "1",
                "maxDeliveryRate": "1",
                "relevant_tools": ["tool_a"],
            },
            {
                "id": "2",
                "address": "0x2",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "1",
                "receivedRequests": "1",
                "selfDeliveredFromReceived": "1",
                "maxDeliveryRate": "1",
                "relevant_tools": ["tool_a"],
            },
        ]
        sd = _make_synced_data(
            mechs_info=json.dumps(info_data),
            mech_tool="tool_a",
            selected_mechs=json.dumps(["0x2"]),
        )
        result = sd.relevant_mechs_info
        assert len(result) == 1
        assert result[0].id == "2"

    def test_relevant_mechs_info_empty_pin_is_no_op(self) -> None:
        """Empty `selected_mechs` does not restrict beyond `mech_tool`."""
        info_data = [
            {
                "id": "1",
                "address": "0x1",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "1",
                "receivedRequests": "1",
                "selfDeliveredFromReceived": "1",
                "maxDeliveryRate": "1",
                "relevant_tools": ["tool_a"],
            },
            {
                "id": "2",
                "address": "0x2",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "1",
                "receivedRequests": "1",
                "selfDeliveredFromReceived": "1",
                "maxDeliveryRate": "1",
                "relevant_tools": ["tool_a"],
            },
        ]
        sd = _make_synced_data(
            mechs_info=json.dumps(info_data),
            mech_tool="tool_a",
            selected_mechs=json.dumps([]),
        )
        assert len(sd.relevant_mechs_info) == 2

    def test_relevant_mechs_info_pin_lookup_is_case_insensitive(self) -> None:
        """Pinning with mixed-case address still matches lowercase mech addresses."""
        info_data = [
            {
                "id": "1",
                "address": "0xabc",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "1",
                "receivedRequests": "1",
                "selfDeliveredFromReceived": "1",
                "maxDeliveryRate": "1",
                "relevant_tools": ["tool_a"],
            },
        ]
        sd = _make_synced_data(
            mechs_info=json.dumps(info_data),
            mech_tool="tool_a",
            selected_mechs=json.dumps(["0xABC"]),
        )
        assert len(sd.relevant_mechs_info) == 1

    def test_mech_tools(self) -> None:
        """Test mech_tools aggregates all tools."""
        info_data = [
            {
                "id": "1",
                "address": "0x1",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "1",
                "receivedRequests": "1",
                "selfDeliveredFromReceived": "1",
                "maxDeliveryRate": "1",
                "relevant_tools": ["tool_a", "tool_b"],
            },
            {
                "id": "2",
                "address": "0x2",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "1",
                "receivedRequests": "1",
                "selfDeliveredFromReceived": "1",
                "maxDeliveryRate": "1",
                "relevant_tools": ["tool_b", "tool_c"],
            },
        ]
        sd = _make_synced_data(mechs_info=json.dumps(info_data))
        assert sd.mech_tools == {"tool_a", "tool_b", "tool_c"}

    def test_priority_mech_returns_best(self) -> None:
        """Test priority_mech returns the mech with highest ranking."""
        info_data = [
            {
                "id": "1",
                "address": "0x1",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "10",
                "receivedRequests": "100",
                "selfDeliveredFromReceived": "50",
                "maxDeliveryRate": "1",
                "relevant_tools": ["tool_a"],
            },
            {
                "id": "2",
                "address": "0x2",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "100",
                "receivedRequests": "100",
                "selfDeliveredFromReceived": "99",
                "maxDeliveryRate": "1",
                "relevant_tools": ["tool_a"],
            },
        ]
        sd = _make_synced_data(mechs_info=json.dumps(info_data), mech_tool="tool_a")
        priority = sd.priority_mech
        assert priority is not None
        assert priority.id == "2"

    def test_priority_mech_none_when_empty(self) -> None:
        """Test priority_mech returns None when no relevant mechs."""
        sd = _make_synced_data(mech_tool="nonexistent")
        assert sd.priority_mech is None

    def test_priority_mech_address(self) -> None:
        """Test priority_mech_address property."""
        info_data = [
            {
                "id": "1",
                "address": "0xbest",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "10",
                "receivedRequests": "10",
                "selfDeliveredFromReceived": "9",
                "maxDeliveryRate": "1",
                "relevant_tools": ["tool_a"],
            },
        ]
        sd = _make_synced_data(mechs_info=json.dumps(info_data), mech_tool="tool_a")
        assert sd.priority_mech_address == "0xbest"

    def test_priority_mech_address_none(self) -> None:
        """Test priority_mech_address returns None when no relevant mechs."""
        sd = _make_synced_data(mech_tool="none")
        assert sd.priority_mech_address is None

    def test_ranked_mechs(self) -> None:
        """Test ranked_mechs returns sorted list."""
        info_data = [
            {
                "id": "1",
                "address": "0x1",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "10",
                "receivedRequests": "100",
                "selfDeliveredFromReceived": "50",
                "maxDeliveryRate": "1",
                "relevant_tools": ["t"],
            },
            {
                "id": "2",
                "address": "0x2",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "100",
                "receivedRequests": "100",
                "selfDeliveredFromReceived": "99",
                "maxDeliveryRate": "1",
                "relevant_tools": ["t"],
            },
        ]
        sd = _make_synced_data(mechs_info=json.dumps(info_data), mech_tool="t")
        ranked = sd.ranked_mechs
        assert len(ranked) == 2
        assert ranked[0].id == "2"  # higher karma/delivery ratio

    def test_ranked_mechs_empty(self) -> None:
        """Test ranked_mechs returns empty list when no relevant mechs."""
        sd = _make_synced_data(mech_tool="none")
        assert sd.ranked_mechs == []

    def test_ranked_mechs_addresses(self) -> None:
        """Test ranked_mechs_addresses returns addresses in order."""
        info_data = [
            {
                "id": "1",
                "address": "0xworse",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "10",
                "receivedRequests": "100",
                "selfDeliveredFromReceived": "50",
                "maxDeliveryRate": "1",
                "relevant_tools": ["t"],
            },
            {
                "id": "2",
                "address": "0xbetter",
                "service": {"metadata": [{"metadata": "m"}], "deliveries": []},
                "karma": "100",
                "receivedRequests": "100",
                "selfDeliveredFromReceived": "99",
                "maxDeliveryRate": "1",
                "relevant_tools": ["t"],
            },
        ]
        sd = _make_synced_data(mechs_info=json.dumps(info_data), mech_tool="t")
        addresses = sd.ranked_mechs_addresses
        assert addresses[0] == "0xbetter"

    def test_ranked_mechs_addresses_empty(self) -> None:
        """Test ranked_mechs_addresses returns empty list."""
        sd = _make_synced_data(mech_tool="none")
        assert sd.ranked_mechs_addresses == []

    def test_mech_price(self) -> None:
        """Test mech_price property."""
        sd = _make_synced_data(mech_price=12345)
        assert sd.mech_price == 12345

    def test_mech_requests(self) -> None:
        """Test mech_requests property."""
        requests = [{"prompt": "test", "tool": "t1", "nonce": "n1"}]
        sd = _make_synced_data(mech_requests=json.dumps(requests))
        result = sd.mech_requests
        assert len(result) == 1
        assert result[0].prompt == "test"

    def test_mech_requests_empty(self) -> None:
        """Test mech_requests when no data."""
        sd = _make_synced_data()
        assert sd.mech_requests == []

    def test_mech_responses(self) -> None:
        """Test mech_responses property."""
        responses = [{"nonce": "n1", "result": "r1"}]
        sd = _make_synced_data(mech_responses=json.dumps(responses))
        result = sd.mech_responses
        assert len(result) == 1
        assert result[0].nonce == "n1"

    def test_mech_responses_empty(self) -> None:
        """Test mech_responses when no data."""
        sd = _make_synced_data()
        assert sd.mech_responses == []

    def test_final_tx_hash(self) -> None:
        """Test final_tx_hash property."""
        sd = _make_synced_data(final_tx_hash="0xhash")
        assert sd.final_tx_hash == "0xhash"

    def test_final_tx_hash_none(self) -> None:
        """Test final_tx_hash when not set."""
        sd = _make_synced_data()
        assert sd.final_tx_hash is None

    def test_chain_id(self) -> None:
        """Test chain_id property."""
        sd = _make_synced_data(chain_id="gnosis")
        assert sd.chain_id == "gnosis"

    def test_chain_id_none(self) -> None:
        """Test chain_id when not set."""
        sd = _make_synced_data()
        assert sd.chain_id is None

    def test_tx_submitter(self) -> None:
        """Test tx_submitter property."""
        sd = _make_synced_data(tx_submitter="MechRequestRound")
        assert sd.tx_submitter == "MechRequestRound"

    def test_versioning_check_performed_true(self) -> None:
        """Test versioning_check_performed when is_marketplace_v2 is set."""
        sd = _make_synced_data(is_marketplace_v2=True)
        assert sd.versioning_check_performed is True

    def test_versioning_check_performed_false(self) -> None:
        """Test versioning_check_performed when is_marketplace_v2 is not set."""
        sd = _make_synced_data()
        assert sd.versioning_check_performed is False

    def test_is_marketplace_v2(self) -> None:
        """Test is_marketplace_v2 property."""
        sd = _make_synced_data(is_marketplace_v2=True)
        assert sd.is_marketplace_v2 is True

    def _make_collection_data(
        self, payload_cls: type, **payload_kwargs: Any
    ) -> Dict[str, Any]:
        """Create a serialized collection with one agent."""
        payload = payload_cls(sender="agent1", **payload_kwargs)
        return {"agent1": payload.json}

    def test_participant_to_info(self) -> None:
        """Test participant_to_info property."""
        from packages.valory.skills.mech_interact_abci.payloads import JSONPayload

        collection = self._make_collection_data(
            JSONPayload, information='{"key": "val"}'
        )
        sd = _make_synced_data(participant_to_info=collection)
        result = sd.participant_to_info
        assert "agent1" in result

    def test_participant_to_requests(self) -> None:
        """Test participant_to_requests property."""
        from packages.valory.skills.mech_interact_abci.payloads import (
            MechRequestPayload,
        )

        collection = self._make_collection_data(
            MechRequestPayload,
            tx_submitter="test",
            tx_hash="0xhash",
            price=100,
            chain_id="gnosis",
            safe_contract_address="0xsafe",
            mech_requests="[]",
            mech_responses="[]",
        )
        sd = _make_synced_data(participant_to_requests=collection)
        result = sd.participant_to_requests
        assert "agent1" in result

    def test_participant_to_responses(self) -> None:
        """Test participant_to_responses property."""
        from packages.valory.skills.mech_interact_abci.payloads import JSONPayload

        collection = self._make_collection_data(
            JSONPayload, information='{"resp": true}'
        )
        sd = _make_synced_data(participant_to_responses=collection)
        result = sd.participant_to_responses
        assert "agent1" in result

    def test_participant_to_purchase(self) -> None:
        """Test participant_to_purchase property."""
        from packages.valory.skills.mech_interact_abci.payloads import PrepareTxPayload

        collection = self._make_collection_data(
            PrepareTxPayload, tx_submitter="test", tx_hash="0x123"
        )
        sd = _make_synced_data(participant_to_purchase=collection)
        result = sd.participant_to_purchase
        assert "agent1" in result
