# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2026 Valory AG
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

"""Helper-level tests for the off-chain request module.

Coverage focuses on the pure functions whose byte-for-byte parity with the
mech server is the single highest-risk silent-failure mode: local CIDv1
computation, ``request_id`` derivation, structured 402 parsing, and the
in-flight state round-trip. Behaviour-level integration tests cover the
executor's failover decision tree.
"""

import json
from typing import Any, Dict

import pytest

from packages.valory.skills.mech_interact_abci.behaviours.offchain_request import (
    PaymentChallenge,
    PendingRequest,
    build_request_metadata,
    compute_cidv1_bytes,
    derive_request_id_bytes,
    parse_payment_challenge,
)


class TestComputeCidv1Bytes:
    """Pinned-fixture parity tests against the mech-server CID computation."""

    @pytest.mark.parametrize(
        "content, expected_hex",
        [
            # Empty content. UnixFS Data field is omitted; filesize is 0.
            (
                b"",
                "01701220bfccda787baba32b59c78450ac3d20b633360b43992c77289f9ed46d843561e6",
            ),
            # Single ASCII byte.
            (
                b"a",
                "01701220fad3b4b8270ea30f09c1364b990db3351b2f720115b774071f4cc4e2ba25dfc2",
            ),
            # Short ASCII line.
            (
                b"hello",
                "017012207bb129136cd5c391f6a2401e5cb7317575dcf79352249536bea3a937aef9bd9c",
            ),
        ],
    )
    def test_pinned_fixtures(self, content: bytes, expected_hex: str) -> None:
        """Each pair locks the byte layout against the mech's recomputation.

        Regenerate by running ``ipfs add --cid-version=1 --raw-leaves=false``
        on the same content and converting the resulting CID to raw bytes
        via the multibase / multicodec / multihash decomposition (or by
        calling ``mech-client``'s ``compute_cidv1_bytes`` on the same input).
        Any drift between this module and the mech server's local CID
        implementation breaks settlement silently; pinning these values is
        the regression guard.
        """
        actual = compute_cidv1_bytes(content)
        assert actual.hex() == expected_hex

    def test_oversize_content_raises(self) -> None:
        """The single-block bound is enforced.

        The mech and the client must agree on what they refuse to encode.
        """
        with pytest.raises(ValueError, match="single-block bound"):
            compute_cidv1_bytes(b"x" * (256 * 1024 + 1))

    def test_below_bound_is_accepted(self) -> None:
        """The bound is inclusive; 256 KiB is the largest accepted size."""
        out = compute_cidv1_bytes(b"y" * (256 * 1024))
        # Header is 4 bytes (ver + codec + mh code + mh len), digest 32.
        assert len(out) == 4 + 32


class TestBuildRequestMetadata:
    """Shape parity with ``mech-client`` 's ``fetch_ipfs_hash``."""

    def test_known_nonce_produces_stable_output(self) -> None:
        """Deterministic output for a fixed triple.

        For a fixed ``(prompt, tool, nonce)`` the body and hash are
        deterministic; required for any future cross-client parity
        regression check.
        """
        truncated, full, body = build_request_metadata(
            prompt="hello world",
            tool="prediction-request",
            nonce_str="fixed-nonce-1234",
        )
        # Body shape: JSON dict with default separators, insertion order.
        parsed = json.loads(body)
        assert parsed == {
            "prompt": "hello world",
            "tool": "prediction-request",
            "nonce": "fixed-nonce-1234",
        }
        # On-chain truncation: ``0x`` + 62 hex chars.
        assert truncated.startswith("0x")
        assert len(truncated) == 66
        # Full ``f01...`` hex form.
        assert full.startswith("f01701220")
        assert len(full) == 73

    def test_extra_attributes_are_merged(self) -> None:
        """Extra attrs land at the metadata top level, not nested."""
        _, _, body = build_request_metadata(
            prompt="p",
            tool="t",
            extra_attributes={"max_tokens": 256, "system": "you are…"},
            nonce_str="x",
        )
        parsed = json.loads(body)
        assert parsed["max_tokens"] == 256
        assert parsed["system"] == "you are…"

    def test_default_nonce_is_uuid(self) -> None:
        """When the caller does not pin a nonce, a UUID4 string fills in."""
        _, _, body = build_request_metadata(prompt="p", tool="t")
        parsed = json.loads(body)
        assert isinstance(parsed["nonce"], str)
        assert len(parsed["nonce"]) >= 32  # UUID4 hex length without dashes


class TestDeriveRequestIdBytes:
    """``request_id`` parity with ``MechMarketplace.getRequestId``."""

    def test_basic_derivation_is_32_bytes(self) -> None:
        """Sanity: the EIP-712 hash is 32 bytes regardless of inputs."""
        out = derive_request_id_bytes(
            marketplace_address="0x" + "11" * 20,
            mech_address="0x" + "22" * 20,
            requester="0x" + "33" * 20,
            data=b"{}",
            delivery_rate=10**16,
            payment_type=bytes.fromhex(
                "ba699a34be8fe0e7725e93dcbce1701b0211a8ca61330aaeb8a05bf2ec7abed1"
            ),
            nonce=42,
            chain_id=100,
        )
        assert len(out) == 32

    def test_different_mechs_produce_different_request_ids(self) -> None:
        """Per-mech ``request_id`` divergence underpins failover safety.

        A racing mech cannot replay the request against the contract's
        monotonic ``mapNonces`` because the hash differs.
        """
        common: Dict[str, Any] = dict(
            marketplace_address="0x" + "11" * 20,
            requester="0x" + "33" * 20,
            data=b"{}",
            delivery_rate=10**16,
            payment_type=bytes.fromhex("ba" * 32),
            nonce=42,
            chain_id=100,
        )
        a = derive_request_id_bytes(mech_address="0x" + "aa" * 20, **common)
        b = derive_request_id_bytes(mech_address="0x" + "bb" * 20, **common)
        assert a != b

    def test_payment_type_must_be_32_bytes(self) -> None:
        """Short ``paymentType`` is refused explicitly.

        Otherwise the derivation would silently produce a wrong hash.
        """
        with pytest.raises(ValueError, match="payment_type must be 32 bytes"):
            derive_request_id_bytes(
                marketplace_address="0x" + "11" * 20,
                mech_address="0x" + "22" * 20,
                requester="0x" + "33" * 20,
                data=b"{}",
                delivery_rate=1,
                payment_type=b"\x00",
                nonce=0,
                chain_id=100,
            )

    def test_different_nonces_produce_different_request_ids(self) -> None:
        """Two adjacent nonces must hash to different request_ids.

        Proves the EIP-712 inner-hash inputs include the nonce.
        """
        common: Dict[str, Any] = dict(
            marketplace_address="0x" + "11" * 20,
            mech_address="0x" + "22" * 20,
            requester="0x" + "33" * 20,
            data=b"{}",
            delivery_rate=10**16,
            payment_type=bytes.fromhex("ba" * 32),
            chain_id=100,
        )
        a = derive_request_id_bytes(nonce=10, **common)
        b = derive_request_id_bytes(nonce=11, **common)
        assert a != b

    def test_different_data_blobs_produce_different_request_ids(self) -> None:
        """Client must hash ``data`` before the inner hash like the contract.

        Otherwise the request_id diverges from settlement.
        """
        common: Dict[str, Any] = dict(
            marketplace_address="0x" + "11" * 20,
            mech_address="0x" + "22" * 20,
            requester="0x" + "33" * 20,
            delivery_rate=10**16,
            payment_type=bytes.fromhex("ba" * 32),
            nonce=5,
            chain_id=100,
        )
        a = derive_request_id_bytes(data=b'{"prompt":"a"}', **common)
        b = derive_request_id_bytes(data=b'{"prompt":"b"}', **common)
        assert a != b


class TestParsePaymentChallenge:
    """Structured 402 body parsing."""

    def _body(self, **overrides: Any) -> bytes:
        body: Dict[str, Any] = {
            "request_id": "abc",
            "status": "rejected",
            "reason": "insufficient balance",
            "scheme": "olas-prepay",
            "payTo": "0x" + "11" * 20,
            "asset": "0x" + "22" * 20,
            "chainId": 100,
            "currentBalance": "100",
            "required": "1000",
            "depositInstructions": {
                "contract": "0x" + "11" * 20,
                "abi": "depositFor(address requester, uint256 amount)",
            },
            "error": "insufficient balance",
        }
        body.update(overrides)
        return json.dumps(body).encode("utf-8")

    def test_full_body_parses(self) -> None:
        """Happy path: every field surfaces and ``shortfall`` derives correctly."""
        challenge = parse_payment_challenge(self._body())
        assert isinstance(challenge, PaymentChallenge)
        assert challenge.pay_to == "0x" + "11" * 20
        assert challenge.asset == "0x" + "22" * 20
        assert challenge.chain_id == 100
        assert challenge.current_balance == 100
        assert challenge.required == 1000
        assert challenge.shortfall == 900

    def test_missing_pay_to_yields_none(self) -> None:
        """Without ``payTo`` the body is unusable for a deposit retry."""
        body = self._body()
        parsed = json.loads(body)
        del parsed["payTo"]
        assert parse_payment_challenge(json.dumps(parsed).encode()) is None

    def test_empty_body_yields_none(self) -> None:
        """An empty body cannot be a structured 402."""
        assert parse_payment_challenge(b"") is None

    def test_non_json_body_yields_none(self) -> None:
        """A misbehaving mech returning non-JSON does not crash the FSM."""
        assert parse_payment_challenge(b"plain text") is None

    def test_non_numeric_required_falls_back_to_zero(self) -> None:
        """Non-numeric ``required`` is tolerated.

        Downstream cap check still sees a finite value rather than an
        exception.
        """
        challenge = parse_payment_challenge(self._body(required="not-a-number"))
        assert challenge is not None
        assert challenge.required == 0
        assert challenge.shortfall == 0

    def test_balance_above_required_yields_zero_shortfall(self) -> None:
        """A top-up race yields zero shortfall, not a negative number.

        Triggered when the balance moves above ``required`` between the
        mech-side reads that build the 402 body.
        """
        challenge = parse_payment_challenge(
            self._body(currentBalance="2000", required="1000")
        )
        assert challenge is not None
        assert challenge.shortfall == 0

    def test_default_asset_when_missing(self) -> None:
        """No ``asset`` field defaults to the zero address (native model)."""
        body = self._body()
        parsed = json.loads(body)
        del parsed["asset"]
        challenge = parse_payment_challenge(json.dumps(parsed).encode())
        assert challenge is not None
        assert challenge.asset == "0x" + "0" * 40


class TestPendingRequest:
    """Round-trip of the in-flight state across the deposit retry."""

    def _raw(self, **overrides: Any) -> Dict[str, Any]:
        raw: Dict[str, Any] = {
            "request_id": "abc123" * 5 + "ab",
            "nonce": 42,
            "mech_address": "0x" + "aa" * 20,
            "mech_url": "https://mech.example/",
            "sender": "0x" + "bb" * 20,
            "delivery_rate": 1000,
            "ipfs_hash": "0x" + "cc" * 31,
            "ipfs_data": '{"prompt":"x"}',
            "signature": "0x" + "dd" * 65,
            "attempted_mechs": ["0x" + "AA" * 20],
        }
        raw.update(overrides)
        return raw

    def test_round_trip(self) -> None:
        """Serialise then deserialise: every field survives intact."""
        pending = PendingRequest.from_dict(self._raw())
        assert pending is not None
        re_parsed = PendingRequest.from_dict(json.loads(pending.to_json()))
        assert re_parsed == pending

    def test_addresses_are_lowercased(self) -> None:
        """Accessor lower-cases ``mech_address`` and the attempted list.

        Lets failover compare apples to apples regardless of input casing.
        """
        pending = PendingRequest.from_dict(self._raw())
        assert pending is not None
        assert pending.mech_address == "0x" + "aa" * 20
        assert pending.attempted_mechs == ["0x" + "aa" * 20]

    def test_missing_field_yields_none(self) -> None:
        """A partial blob returns ``None`` instead of raising.

        Callers (e.g. when the persistence shape changes) can treat the
        result as 'no pending request' and proceed.
        """
        raw = self._raw()
        del raw["request_id"]
        assert PendingRequest.from_dict(raw) is None
