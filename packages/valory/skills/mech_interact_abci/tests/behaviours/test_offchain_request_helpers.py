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
import logging
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from packages.valory.skills.mech_interact_abci.behaviours.offchain_request import (
    OffchainAttemptOutcome,
    OffchainAttemptResult,
    OffchainCycleResult,
    OffchainRequestExecutor,
    PaymentChallenge,
    PendingRequest,
    _BALANCE_READ_FAILED,
    _BALANCE_SHORT,
    _PAYMENT_TYPE_HASH_NATIVE,
    _PAYMENT_TYPE_HASH_NVM_NATIVE,
    _PAYMENT_TYPE_HASH_NVM_TOKEN,
    _PAYMENT_TYPE_HASH_TOKEN_OLAS,
    _PAYMENT_TYPE_HASH_TOKEN_USDC,
    build_request_metadata,
    compute_cidv1_bytes,
    compute_safe_message_hash,
    derive_request_id_bytes,
    parse_payment_challenge,
)
from packages.valory.skills.mech_interact_abci.behaviours.request import PaymentType
from packages.valory.skills.mech_interact_abci.states.base import (
    Event,
    MechMetadata,
    OFFCHAIN_402_INSUFFICIENT,
    OFFCHAIN_TIMEOUT_ALL_MECHS,
    merge_extra_attributes,
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
    """Shape parity with the on-chain ``_send_metadata_to_ipfs`` payload."""

    def test_known_nonce_produces_stable_output(self) -> None:
        """Deterministic output for a fixed triple.

        For a fixed ``(prompt, tool, nonce)`` the body and hash are
        deterministic; required for any future cross-client parity
        regression check. ``schema_version`` and ``request_context``
        appear unconditionally so the analytics lake sees one shape.
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
            "schema_version": "2.0",
            "request_context": None,
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

    def test_request_context_present_in_body(self) -> None:
        """A populated ``request_context`` is carried verbatim.

        The analytics ETL reads ``raw_content->request_context`` for
        ``market_id`` and ``market_prob``; the off-chain path must not
        drop these fields (previously it did).
        """
        ctx = {
            "market_id": "0xabc",
            "type": "omen",
            "market_prob": 0.42,
        }
        _, _, body = build_request_metadata(
            prompt="p",
            tool="t",
            nonce_str="n",
            request_context=ctx,
        )
        parsed = json.loads(body)
        assert parsed["request_context"] == ctx
        assert parsed["schema_version"] == "2.0"

    def test_schema_version_override(self) -> None:
        """``schema_version`` is passed through verbatim."""
        _, _, body = build_request_metadata(
            prompt="p",
            tool="t",
            nonce_str="n",
            schema_version="3.0",
        )
        parsed = json.loads(body)
        assert parsed["schema_version"] == "3.0"

    def test_extras_reserved_key_rejection_routes_through_caller_logger(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When the caller passes a logger, the rejection error fires on it.

        The executor passes ``self._logger`` at the call site so the
        rejection is surfaced through the AEA logging pipeline the
        agent's operators configured, not through Python's root logger
        (which the deployed agent may not attach handlers to).
        """
        caller_logger = logging.getLogger("test.caller.logger")
        with caplog.at_level(logging.ERROR, logger=caller_logger.name):
            with pytest.raises(ValueError, match="reserved request keys"):
                build_request_metadata(
                    prompt="p",
                    tool="t",
                    nonce_str="n",
                    extra_attributes={"prompt": "override"},
                    logger=caller_logger,
                )
        emitting = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert emitting, "expected an error log on the caller-supplied logger"
        assert all(
            r.name == caller_logger.name for r in emitting
        ), "error must be emitted on the caller's logger, not the module logger"
        assert any(
            "extra_attributes contains reserved" in r.getMessage() for r in emitting
        )

    def test_extras_reserved_keys_hard_rejected(self) -> None:
        """Extras colliding with reserved keys raise instead of silently overwriting.

        Under the previous warn-and-clobber behaviour, a caller stuffing
        ``tool`` into ``extra_attributes`` would silently substitute the
        paid-for tool with whatever they passed, and the mech would settle
        the request against the wrong tool identifier. Hard-reject is
        safer than a warn log the operator can miss.
        """
        with pytest.raises(ValueError, match="reserved request keys"):
            build_request_metadata(
                prompt="p",
                tool="t",
                nonce_str="n",
                extra_attributes={"prompt": "override", "request_context": {"x": 1}},
            )

    @pytest.mark.parametrize(
        "reserved_key",
        ["prompt", "tool", "nonce", "schema_version", "request_context"],
    )
    def test_each_reserved_key_is_rejected(self, reserved_key: str) -> None:
        """Every reserved payload key is protected.

        Not just the ones the executor happens to populate first.
        """
        with pytest.raises(ValueError, match=f".*{reserved_key}.*"):
            build_request_metadata(
                prompt="p",
                tool="t",
                nonce_str="n",
                extra_attributes={reserved_key: "attempted-substitution"},
            )

    def test_benign_extras_still_merge_top_level(self) -> None:
        """Extras that don't collide with reserved keys still merge normally."""
        _, _, body = build_request_metadata(
            prompt="p",
            tool="t",
            nonce_str="n",
            extra_attributes={"max_tokens": 256, "system": "you are…"},
        )
        parsed = json.loads(body)
        assert parsed["max_tokens"] == 256
        assert parsed["system"] == "you are…"
        # Reserved fields still present with the expected values.
        assert parsed["prompt"] == "p"
        assert parsed["tool"] == "t"
        assert parsed["nonce"] == "n"

    def test_offchain_matches_onchain_payload_shape(self) -> None:
        """The off-chain ``ipfs_data`` dict equals the on-chain payload.

        Drift guard for the exact bug this change fixes: both builders
        must project the same ``MechMetadata`` into the same dict shape
        so the analytics lake sees one schema regardless of transport.
        Both paths go through :func:`merge_extra_attributes`, so any
        future change to the merge/clobber logic is picked up by this
        test automatically — the off-chain builder must be updated to
        keep parity, or the assertion fails.
        """
        meta = MechMetadata(
            prompt="q?",
            tool="prediction-request",
            nonce="fixed-nonce-1234",
            request_context={
                "market_id": "0xabc",
                "type": "omen",
                "market_prob": 0.42,
            },
            extra_attributes={"max_tokens": 256, "system": "you are…"},
        )
        # On-chain path: asdict, pop wrapper, share the merge helper with
        # `_send_metadata_to_ipfs` (request.py).
        onchain_payload = asdict(meta)
        onchain_extras = onchain_payload.pop("extra_attributes", None)
        merge_extra_attributes(onchain_payload, onchain_extras)
        # Off-chain path: build_request_metadata forwards every field.
        _, _, body = build_request_metadata(
            prompt=meta.prompt,
            tool=meta.tool,
            nonce_str=meta.nonce,
            extra_attributes=meta.extra_attributes,
            request_context=meta.request_context,
            schema_version=meta.schema_version,
        )
        assert json.loads(body) == onchain_payload

    def test_offchain_matches_onchain_payload_shape_defaults(self) -> None:
        """Parity also holds for a minimal ``MechMetadata`` (defaults)."""
        meta = MechMetadata(prompt="q?", tool="t1", nonce="n1")
        onchain_payload = asdict(meta)
        onchain_extras = onchain_payload.pop("extra_attributes", None)
        merge_extra_attributes(onchain_payload, onchain_extras)
        _, _, body = build_request_metadata(
            prompt=meta.prompt,
            tool=meta.tool,
            nonce_str=meta.nonce,
            extra_attributes=meta.extra_attributes,
            request_context=meta.request_context,
            schema_version=meta.schema_version,
        )
        assert json.loads(body) == onchain_payload


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

    def test_golden_vector_matches_eip712_primitives(self) -> None:
        """Pin ``derive_request_id_bytes`` against an EIP-712 reference.

        Length-only and divergence-only checks accept any function that
        produces 32 bytes and varies with its inputs, including ones that
        diverge from ``MechMarketplace.getRequestId`` in subtle ways. The
        domain separator in particular uses asymmetric hashing — ``name``
        is raw ``keccak256(bytes(s))`` while ``version`` is hashed through
        ``abi.encode`` (see ``MechMarketplace.sol:155-165``). A silent
        drift here (e.g. switching ``version`` to raw bytes to "match
        standard EIP-712") would still pass the older tests but recover
        the wrong signer at on-chain settlement.

        Reconstruct the expected ``request_id`` step-by-step using
        ``eth_abi.encode`` and ``eth_utils.keccak`` directly, mirroring
        the Solidity sources line-for-line:

        * Domain separator: ``MechMarketplace.sol:155-165``
        * Inner hash: ``MechMarketplace.sol:891-907``

        and assert ``derive_request_id_bytes`` produces the same output.
        """
        from eth_abi import encode as _abi_encode  # type: ignore[import-not-found]
        from eth_utils import keccak as _keccak  # type: ignore[import-not-found]

        marketplace = "0x" + "11" * 20
        mech = "0x" + "22" * 20
        requester = "0x" + "33" * 20
        data = b'{"prompt":"hi","tool":"t","nonce":"n"}'
        delivery_rate = 10**16
        payment_type = bytes.fromhex(
            "ba699a34be8fe0e7725e93dcbce1701b0211a8ca61330aaeb8a05bf2ec7abed1"
        )
        nonce = 42
        chain_id = 100

        # Mirror ``_computeDomainSeparator`` exactly. The name is hashed
        # raw, the version is hashed via ``abi.encode`` — the asymmetry
        # the function under test must preserve.
        domain_typehash = _keccak(
            text=(
                "EIP712Domain(string name,string version,uint256 chainId,"
                "address verifyingContract)"
            )
        )
        name_hash = _keccak(text="MechMarketplace")
        version_hash = _keccak(_abi_encode(["string"], ["1.1.0"]))
        domain_separator = _keccak(
            _abi_encode(
                ["bytes32", "bytes32", "bytes32", "uint256", "address"],
                [domain_typehash, name_hash, version_hash, chain_id, marketplace],
            )
        )

        # Mirror ``getRequestId``: keccak256("\\x19\\x01" || domain_sep || inner_hash).
        inner = _keccak(
            _abi_encode(
                [
                    "address",
                    "address",
                    "address",
                    "bytes32",
                    "uint256",
                    "bytes32",
                    "uint256",
                ],
                [
                    marketplace,
                    mech,
                    requester,
                    _keccak(data),
                    delivery_rate,
                    payment_type,
                    nonce,
                ],
            )
        )
        expected_request_id = _keccak(b"\x19\x01" + domain_separator + inner)

        # Pin both the intermediate domain separator and the final
        # ``request_id`` so a drift in either layer is caught.
        assert (
            domain_separator.hex()
            == "df50cdbe42bf9d9976fcb9374107c6cf0450b566360eaa04dacb3bb0c1cc8845"
        )
        assert (
            expected_request_id.hex()
            == "76a226b6cf8c7da71bd6340d00e54002d9b01d1e8db5670d4bd3282863633fb0"
        )

        # The function under test must produce the same value.
        actual = derive_request_id_bytes(
            marketplace_address=marketplace,
            mech_address=mech,
            requester=requester,
            data=data,
            delivery_rate=delivery_rate,
            payment_type=payment_type,
            nonce=nonce,
            chain_id=chain_id,
        )
        assert actual == expected_request_id

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


class TestExecutorDerivesRequestIdAgainstIpfsHashBytes:
    """Regression: the executor must hash the 32-byte ipfs multihash, not JSON.

    The marketplace computes ``keccak256(requestData)`` inside
    ``getRequestId``, and at settlement the mech submits the 32-byte
    ipfs multihash as ``requestData``. If the trader computes the
    request_id over ``ipfs_data.encode("utf-8")`` (the JSON body sent
    on the HTTP form), the signature is over a different digest than
    the Safe validates on chain, and ``checkSignatures`` reverts with
    ``GS026``. This bug went undetected in prod for months (1626
    requests, 0 settled) because no test exercised the call site's
    ``data=`` argument.
    """

    def _native_reads(self) -> List[Any]:
        """Match ``TestFreshCycle._native_reads`` — one happy-path attempt."""
        return [
            _state_resp({"data": 100}),  # _resolve_chain_id_int
            _state_resp({"data": 7}),  # _read_on_chain_nonce
            _state_resp({"payment_type": _NATIVE_PAYMENT_TYPE}),
            _state_resp({"max_delivery_rate": 10**16}),
        ]

    def test_request_id_matches_derivation_over_ipfs_hash_bytes(self) -> None:
        """Executor's request_id must equal derive_request_id_bytes(data=hash_bytes).

        Drives a real ``_fresh_cycle``, then independently reconstructs
        the request_id by rebuilding the same metadata blob to recover
        the ipfs_hash, decoding it to bytes, and passing those bytes as
        ``data`` to ``derive_request_id_bytes``. The two must match.

        The pre-fix implementation passed ``ipfs_data.encode("utf-8")``
        as ``data``, which produced a different keccak and thus a
        different request_id — this test would have failed against that
        code.
        """
        mech_addr = "0x" + "aa" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=self._native_reads(),
            http_responses=[_make_http_response(200)],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_DONE.value
        assert result.pending_request_json is not None
        pending = PendingRequest.from_dict(json.loads(result.pending_request_json))
        assert pending is not None

        # Independently reconstruct what the executor should have derived:
        # the 32-byte ipfs multihash, hashed by the marketplace's EIP-712.
        # ``pending.ipfs_hash`` is the ``"0x"``+62-hex form emitted by
        # ``build_request_metadata`` — 32 bytes when decoded.
        ipfs_hash_bytes = bytes.fromhex(
            pending.ipfs_hash[2:]
            if pending.ipfs_hash.startswith("0x")
            else pending.ipfs_hash
        )
        assert len(ipfs_hash_bytes) == 32
        expected_request_id = derive_request_id_bytes(
            marketplace_address=stub.params.mech_marketplace_config.mech_marketplace_address,
            mech_address=mech_addr,
            requester=stub.synchronized_data.safe_contract_address,
            data=ipfs_hash_bytes,
            delivery_rate=10**16,
            payment_type=_NATIVE_PAYMENT_TYPE,
            nonce=7,
            chain_id=100,
        )
        assert bytes.fromhex(pending.request_id) == expected_request_id

    def test_request_id_does_not_match_derivation_over_json_body(self) -> None:
        """Negative pin: the request_id must NOT equal derive(data=json_bytes).

        This is the exact regression path: pre-fix, the executor passed
        ``ipfs_data.encode("utf-8")`` as ``data``. If a future refactor
        reintroduces that, this assertion flips.

        Guards against ``keccak(json_bytes) == keccak(hash_bytes)`` being
        somehow coincidentally equal (it is not — 32-byte multihash vs
        multi-hundred-byte JSON), so the divergence is real.
        """
        mech_addr = "0x" + "aa" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=self._native_reads(),
            http_responses=[_make_http_response(200)],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.pending_request_json is not None
        pending = PendingRequest.from_dict(json.loads(result.pending_request_json))
        assert pending is not None

        wrong_request_id = derive_request_id_bytes(
            marketplace_address=stub.params.mech_marketplace_config.mech_marketplace_address,
            mech_address=mech_addr,
            requester=stub.synchronized_data.safe_contract_address,
            data=pending.ipfs_data.encode("utf-8"),
            delivery_rate=10**16,
            payment_type=_NATIVE_PAYMENT_TYPE,
            nonce=7,
            chain_id=100,
        )
        assert bytes.fromhex(pending.request_id) != wrong_request_id

    def test_matches_mech_client_reference_derivation(self) -> None:
        """Cross-check against a hand-computed reference of the marketplace formula.

        Mirrors what ``mech-client`` sees when it calls the contract's
        ``getRequestId(mech, sender, data_hash, delivery_rate, payment_type,
        nonce)`` view: ``data_hash`` is the 32-byte truncated ipfs multihash
        (see ``mech_client/services/marketplace_service.py:290``). Rebuilds
        the EIP-712 domain + inner hash from primitives and asserts the
        executor's request_id matches.
        """
        from eth_abi import encode as _abi_encode  # type: ignore[import-not-found]
        from eth_utils import keccak as _keccak  # type: ignore[import-not-found]

        mech_addr = "0x" + "aa" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=self._native_reads(),
            http_responses=[_make_http_response(200)],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.pending_request_json is not None
        pending = PendingRequest.from_dict(json.loads(result.pending_request_json))
        assert pending is not None

        marketplace = stub.params.mech_marketplace_config.mech_marketplace_address
        requester = stub.synchronized_data.safe_contract_address
        chain_id = 100
        delivery_rate = 10**16
        nonce = 7
        data_hash = bytes.fromhex(
            pending.ipfs_hash[2:]
            if pending.ipfs_hash.startswith("0x")
            else pending.ipfs_hash
        )

        # Domain separator (mirrors MechMarketplace._computeDomainSeparator).
        domain_typehash = _keccak(
            text=(
                "EIP712Domain(string name,string version,uint256 chainId,"
                "address verifyingContract)"
            )
        )
        name_hash = _keccak(text="MechMarketplace")
        version_hash = _keccak(_abi_encode(["string"], ["1.1.0"]))
        domain_separator = _keccak(
            _abi_encode(
                ["bytes32", "bytes32", "bytes32", "uint256", "address"],
                [
                    domain_typehash,
                    name_hash,
                    version_hash,
                    chain_id,
                    marketplace,
                ],
            )
        )

        # Inner hash: keccak of abi.encode(mp, mech, requester, keccak(data),
        # delivery_rate, payment_type, nonce). ``data`` is the 32-byte hash,
        # NOT the JSON.
        inner = _keccak(
            _abi_encode(
                [
                    "address",
                    "address",
                    "address",
                    "bytes32",
                    "uint256",
                    "bytes32",
                    "uint256",
                ],
                [
                    marketplace,
                    mech_addr,
                    requester,
                    _keccak(data_hash),
                    delivery_rate,
                    _NATIVE_PAYMENT_TYPE,
                    nonce,
                ],
            )
        )
        expected_request_id = _keccak(b"\x19\x01" + domain_separator + inner)

        assert bytes.fromhex(pending.request_id) == expected_request_id


class TestComputeSafeMessageHash:
    """SafeMessage EIP-712 wrapping mirrors CompatibilityFallbackHandler.

    Because ``MechMarketplace._verifySignedHash`` calls
    ``Safe.isValidSignature(request_id, sig)`` for a Safe requester, the
    signature posted to the mech must be over the digest the Safe's
    ``CompatibilityFallbackHandler`` (v1.3.0 and v1.4.1) rehashes
    internally, not over the raw ``request_id``. These tests pin the
    wrapping so any drift from ``getMessageHashForSafe`` would surface
    as an ``isValidSignature`` revert instead of a silent settlement
    fail.
    """

    # Fork-verified vector from anvil (Gnosis mainnet). Reproduce with
    # ``Safe.getMessageHash(abi.encode(request_id))`` on a threshold-1
    # Safe deployed via SafeProxyFactory 0x4e1DCf...ec67 + singleton
    # 0x4167...61a + fallback handler 0xfd07...ec99. Owner-signed sig
    # over this hash returns MAGIC_VALUE from ``isValidSignature``.
    _KNOWN_SAFE = "0x56f3a6943924e88e6aeb4278b88dcafbb9c2d7ae"
    _KNOWN_CHAIN_ID = 100
    _KNOWN_REQUEST_ID = bytes.fromhex(
        "1111111111111111111111111111111111111111111111111111111111111111"
    )
    _KNOWN_WRAPPED_HEX = (
        "107f780f314a238408740f6be23069534b1dfc78fca78aa55cd4599c5280b665"
    )

    def test_matches_fork_verified_vector(self) -> None:
        """Locks the byte layout against the on-chain reference."""
        wrapped = compute_safe_message_hash(
            self._KNOWN_REQUEST_ID,
            self._KNOWN_SAFE,
            self._KNOWN_CHAIN_ID,
        )
        assert wrapped.hex() == self._KNOWN_WRAPPED_HEX

    def test_domain_binds_to_safe_address(self) -> None:
        """Same request_id, different Safe → different digest.

        Regression guard for dropping ``safeAddress`` from the domain
        separator: signature from Safe A would spuriously validate
        against Safe B otherwise.
        """
        other = "0x" + "aa" * 20
        assert compute_safe_message_hash(
            self._KNOWN_REQUEST_ID, self._KNOWN_SAFE, self._KNOWN_CHAIN_ID
        ) != compute_safe_message_hash(
            self._KNOWN_REQUEST_ID, other, self._KNOWN_CHAIN_ID
        )

    def test_domain_binds_to_chain_id(self) -> None:
        """Same request_id + Safe, different chain → different digest.

        Prevents cross-chain replay of a Safe-owner signature.
        """
        assert compute_safe_message_hash(
            self._KNOWN_REQUEST_ID, self._KNOWN_SAFE, 100
        ) != compute_safe_message_hash(self._KNOWN_REQUEST_ID, self._KNOWN_SAFE, 137)

    def test_struct_hash_binds_to_request_id(self) -> None:
        """Different request_id → different digest.

        Catches a bug where the message field is dropped from the struct
        hash (all request_ids would then produce the same wrapped hash).
        """
        other = bytes.fromhex("22" * 32)
        assert compute_safe_message_hash(
            self._KNOWN_REQUEST_ID, self._KNOWN_SAFE, self._KNOWN_CHAIN_ID
        ) != compute_safe_message_hash(other, self._KNOWN_SAFE, self._KNOWN_CHAIN_ID)

    def test_rejects_non_32_byte_request_id(self) -> None:
        """A shorter or longer request_id would produce a silently wrong hash."""
        with pytest.raises(ValueError):
            compute_safe_message_hash(b"\x00" * 31, self._KNOWN_SAFE, 100)
        with pytest.raises(ValueError):
            compute_safe_message_hash(b"\x00" * 33, self._KNOWN_SAFE, 100)


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

    def test_non_numeric_required_rejected(self) -> None:
        """Non-numeric ``required`` is treated as a malformed 402 (review C5).

        Silently coercing to ``0`` previously mis-routed the deposit
        decision (``shortfall=0`` → request abandoned with no log). The
        parser now returns ``None`` so the caller maps to
        ``OFFCHAIN_BAD_RESPONSE`` and failover moves on.
        """
        assert parse_payment_challenge(self._body(required="not-a-number")) is None

    def test_non_numeric_current_balance_rejected(self) -> None:
        """Non-numeric ``currentBalance`` similarly trips ``BAD_RESPONSE``.

        Previously coerced to 0 and produced a spurious ``OVER_CAP`` that
        short-circuited failover.
        """
        assert parse_payment_challenge(self._body(currentBalance="oops")) is None

    def test_boolean_required_rejected(self) -> None:
        """A boolean ``required`` is not a number; the parser refuses it.

        ``json.loads`` can emit ``True`` / ``False`` for a misbehaving
        mech; ``int(True) == 1`` would otherwise sneak through.
        """
        assert parse_payment_challenge(self._body(required=True)) is None

    def test_missing_chain_id_is_tolerated(self) -> None:
        """``chainId`` is reported but not used by the deposit builder.

        A missing or non-numeric value defaults to 0 here so the parser
        still surfaces a usable challenge for the cap check and the
        deposit tx (which executes on the configured ``mech_chain_id``).
        """
        body = self._body()
        parsed = json.loads(body)
        del parsed["chainId"]
        challenge = parse_payment_challenge(json.dumps(parsed).encode())
        assert challenge is not None
        assert challenge.chain_id == 0

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
    """Round-trip of the in-flight state across the deposit retry.

    The signature is intentionally not part of this struct (review C1):
    a per-agent ECDSA sig would diverge across agents and break the
    ``CollectSameUntilThresholdRound`` consensus over the full payload
    tuple. Every field below must be deterministic given the on-chain
    reads so all agents converge on the same ``PendingRequest`` shape.
    """

    _VALID_REQUEST_ID = "a" * 64  # 64-char lower-hex, no 0x prefix

    def _raw(self, **overrides: Any) -> Dict[str, Any]:
        raw: Dict[str, Any] = {
            "request_id": self._VALID_REQUEST_ID,
            "nonce": 42,
            "mech_address": "0x" + "aa" * 20,
            "mech_url": "https://mech.example/",
            "sender": "0x" + "bb" * 20,
            "delivery_rate": 1000,
            "ipfs_hash": "0x" + "cc" * 31,
            "ipfs_data": '{"prompt":"x"}',
        }
        raw.update(overrides)
        return raw

    def test_round_trip(self) -> None:
        """Serialise then deserialise: every field survives intact."""
        pending = PendingRequest.from_dict(self._raw())
        assert pending is not None
        re_parsed = PendingRequest.from_dict(json.loads(pending.to_json()))
        assert re_parsed == pending

    def test_mech_address_is_lowercased(self) -> None:
        """``from_dict`` normalises ``mech_address`` to lowercase.

        Lets failover comparisons match regardless of input casing.
        """
        pending = PendingRequest.from_dict(self._raw(mech_address="0x" + "AA" * 20))
        assert pending is not None
        assert pending.mech_address == "0x" + "aa" * 20

    def test_request_id_0x_prefix_stripped(self) -> None:
        """``request_id`` is normalised to 64-char lower-hex without ``0x``.

        Earlier revisions persisted the value with ``0x``; ``from_dict``
        accepts both shapes so a re-entry against an older payload still
        validates.
        """
        pending = PendingRequest.from_dict(
            self._raw(request_id="0x" + self._VALID_REQUEST_ID)
        )
        assert pending is not None
        assert pending.request_id == self._VALID_REQUEST_ID

    def test_malformed_request_id_rejected_at_construction(self) -> None:
        """Bad ``request_id`` shape raises (review C9): fail fast at construct.

        Previously the bad value flowed all the way to ``_retry_pending``,
        which then crashed inside ``bytes.fromhex``. Asserting here means
        the round won't dispatch on a malformed pending request.
        """
        with pytest.raises(ValueError, match="request_id"):
            PendingRequest(
                request_id="not-hex",
                nonce=1,
                mech_address="0x" + "aa" * 20,
                mech_url="https://m",
                sender="0x" + "bb" * 20,
                delivery_rate=1,
                ipfs_hash="0x",
                ipfs_data="{}",
            )

    def test_uppercase_mech_address_rejected_at_construction(self) -> None:
        """``mech_address`` must be lowercase (review C9)."""
        with pytest.raises(ValueError, match="lower-cased"):
            PendingRequest(
                request_id=self._VALID_REQUEST_ID,
                nonce=1,
                mech_address="0x" + "AA" * 20,
                mech_url="https://m",
                sender="0x" + "bb" * 20,
                delivery_rate=1,
                ipfs_hash="0x",
                ipfs_data="{}",
            )

    def test_negative_nonce_rejected(self) -> None:
        """A negative ``nonce`` cannot be a real ``mapNonces`` reading."""
        with pytest.raises(ValueError, match="nonce"):
            PendingRequest(
                request_id=self._VALID_REQUEST_ID,
                nonce=-1,
                mech_address="0x" + "aa" * 20,
                mech_url="https://m",
                sender="0x" + "bb" * 20,
                delivery_rate=1,
                ipfs_hash="0x",
                ipfs_data="{}",
            )

    def test_missing_field_yields_none(self) -> None:
        """A partial blob returns ``None`` instead of raising.

        Callers (e.g. when the persistence shape changes) can treat the
        result as 'no pending request' and proceed.
        """
        raw = self._raw()
        del raw["request_id"]
        assert PendingRequest.from_dict(raw) is None

    def test_no_signature_field(self) -> None:
        """Sanity (review C1): the struct must not expose a signature field.

        If a future refactor reintroduces it, the multi-agent consensus
        guarantee is silently broken.
        """
        pending = PendingRequest.from_dict(self._raw())
        assert pending is not None
        assert not hasattr(pending, "signature")
        assert "signature" not in json.loads(pending.to_json())

    def test_metadata_nonce_survives_round_trip(self) -> None:
        """The caller-supplied metadata UUID must round-trip on ``PendingRequest``.

        Downstream consumers (e.g. market-resolver's ``build_answer_tx``)
        correlate a response back to the originating request by matching
        ``MechInteractionResponse.nonce`` against this UUID. If it drops
        across the deposit retry (which reconstructs the ``PendingRequest``
        from synced_data), the retry-side response is un-matchable.
        """
        uuid_meta = "b0d591e2-b340-4fc0-b663-65301ca2c673"
        raw = self._raw(metadata_nonce=uuid_meta)
        pending = PendingRequest.from_dict(raw)
        assert pending is not None
        assert pending.metadata_nonce == uuid_meta
        roundtrip = PendingRequest.from_dict(json.loads(pending.to_json()))
        assert roundtrip is not None
        assert roundtrip.metadata_nonce == uuid_meta

    def test_missing_metadata_nonce_defaults_to_empty(self) -> None:
        """Older payloads (pre-metadata-nonce) still deserialise cleanly.

        Backwards-compat: a payload written by an older revision that
        omits ``metadata_nonce`` re-enters as empty string rather than
        raising, since the deposit-retry path may pick up an in-flight
        request from before this field existed.
        """
        raw = self._raw()
        raw.pop("metadata_nonce", None)
        pending = PendingRequest.from_dict(raw)
        assert pending is not None
        assert pending.metadata_nonce == ""

    def test_null_metadata_nonce_normalises_to_empty(self) -> None:
        """``metadata_nonce: null`` on the wire also normalises to empty string.

        Without the ``or ""`` fallback, ``str(None)`` would produce the
        literal ``"None"`` string and ship as the correlation key in
        ``_serialise_pending_response``. MechMetadata doesn't runtime-
        enforce this field, so a null-emitting upstream can reach here.
        """
        raw = self._raw()
        raw["metadata_nonce"] = None
        pending = PendingRequest.from_dict(raw)
        assert pending is not None
        assert pending.metadata_nonce == ""


class TestPaymentTypeHashesMatchEnum:
    """Drift guard (review C7).

    The five hash constants in ``offchain_request`` mirror ``PaymentType``
    in ``behaviours/request`` (the comment in the executor admits the
    duplication to avoid a circular import). If the enum changes and one
    of these hashes is not updated, the deposit dispatch silently skips
    the affected payment type. This test fails fast in that case.
    """

    @pytest.mark.parametrize(
        "constant, enum_value",
        [
            (_PAYMENT_TYPE_HASH_NATIVE, PaymentType.NATIVE.value),
            (_PAYMENT_TYPE_HASH_TOKEN_OLAS, PaymentType.TOKEN_OLAS.value),
            (_PAYMENT_TYPE_HASH_TOKEN_USDC, PaymentType.TOKEN_USDC.value),
            (_PAYMENT_TYPE_HASH_NVM_NATIVE, PaymentType.NATIVE_NVM.value),
            (_PAYMENT_TYPE_HASH_NVM_TOKEN, PaymentType.TOKEN_NVM_USDC.value),
        ],
    )
    def test_payment_type_hash_matches_enum(
        self, constant: str, enum_value: str
    ) -> None:
        """Each module-level hash equals the matching ``PaymentType.value``."""
        assert constant == enum_value


# ----------------------------------------------------------------------------
# Behavioural tests (review C8) — exercise the executor's failover decision
# tree and the resume-after-deposit path with mocked contract reads and HTTP.
# ----------------------------------------------------------------------------


_NATIVE_PAYMENT_TYPE = bytes.fromhex(_PAYMENT_TYPE_HASH_NATIVE[2:])
_TOKEN_PAYMENT_TYPE = bytes.fromhex(_PAYMENT_TYPE_HASH_TOKEN_USDC[2:])
_VALID_REQUEST_ID_HEX = "ab" * 32


def _make_http_response(status_code: int, body: bytes = b"") -> SimpleNamespace:
    """Build the minimal shape ``_post_signed_request`` / ``_parse`` read."""
    return SimpleNamespace(status_code=status_code, body=body)


def _make_402_body(
    pay_to: str = "0x" + "11" * 20,
    asset: str = "0x" + "22" * 20,
    required: int = 1000,
    current: int = 100,
) -> bytes:
    """Build a structured 402 body matching ``parse_payment_challenge``."""
    return json.dumps(
        {
            "payTo": pay_to,
            "asset": asset,
            "chainId": 100,
            "currentBalance": str(current),
            "required": str(required),
            "error": "insufficient balance",
        }
    ).encode("utf-8")


class _FakeMechInfo:
    """Stand-in for a ``MechInfo`` entry in ``synchronized_data.ranked_mechs``."""

    def __init__(self, address: str, http_url: Optional[str]) -> None:
        self.address = address
        self.http_url = http_url


class _StateResponse(SimpleNamespace):
    """Matches the ``performative != STATE → return None`` guard."""


def _state_resp(body: Dict[str, Any]) -> Any:
    """Build a contract-api response that passes the STATE check."""
    from packages.valory.protocols.contract_api import ContractApiMessage

    return SimpleNamespace(
        performative=ContractApiMessage.Performative.STATE,
        state=SimpleNamespace(body=body),
    )


def _ledger_balance_resp(balance: int) -> Any:
    """Build a ledger-api ``get_balance`` response that passes the STATE check."""
    from packages.valory.protocols.ledger_api.message import LedgerApiMessage

    return SimpleNamespace(
        performative=LedgerApiMessage.Performative.STATE,
        state=SimpleNamespace(body={"get_balance_result": balance}),
    )


def _ledger_error_resp() -> Any:
    """Build a ledger-api ERROR response for RPC-failure paths."""
    from packages.valory.protocols.ledger_api.message import LedgerApiMessage

    return SimpleNamespace(
        performative=LedgerApiMessage.Performative.ERROR,
        state=SimpleNamespace(body={}),
    )


def _ledger_missing_key_resp() -> Any:
    """Build a ledger-api STATE response with an empty body.

    Simulates an RPC that returned a well-formed envelope but no
    ``get_balance_result`` field (e.g. an upstream schema drift). The
    balance reader must classify this as ``_BALANCE_READ_FAILED`` so the
    caller does not treat it as an underfunded Safe.
    """
    from packages.valory.protocols.ledger_api.message import LedgerApiMessage

    return SimpleNamespace(
        performative=LedgerApiMessage.Performative.STATE,
        state=SimpleNamespace(body={}),
    )


def _ledger_non_numeric_resp(value: Any) -> Any:
    """Build a ledger-api STATE response whose balance value is not numeric.

    Guards the ``int(raw)`` coercion branch in ``_read_safe_native_balance``:
    a malformed value (``"not-a-number"``, a list, a dict) must be
    classified as ``_BALANCE_READ_FAILED`` rather than crashing the
    executor with an uncaught ``ValueError``.
    """
    from packages.valory.protocols.ledger_api.message import LedgerApiMessage

    return SimpleNamespace(
        performative=LedgerApiMessage.Performative.STATE,
        state=SimpleNamespace(body={"get_balance_result": value}),
    )


class _StubBehaviour:
    """Minimal stub of the parent behaviour the executor talks back to.

    Only the methods the executor calls are implemented. ``contract_api_responses``
    is consulted in FIFO order so each test wires the exact sequence its
    code path is expected to hit.
    """

    def __init__(
        self,
        *,
        ranked_mechs: List[_FakeMechInfo],
        contract_api_responses: List[Any],
        http_responses: List[Any],
        signature: Optional[str] = "0x" + "dd" * 65,
        offchain_pending_request: Optional[Dict[str, Any]] = None,
        priority_mech_address: Optional[str] = None,
        offchain_url: Optional[str] = None,
        auto_deposit_cap: int = 10**18,
        deposit_target_calls: int = 10,
        failover_retries: int = 2,
        mech_requests: Optional[List[Any]] = None,
        use_dynamic_mech_selection: bool = True,
        ledger_api_responses: Optional[List[Any]] = None,
        mech_wrapped_native_token_address: Optional[str] = None,
    ) -> None:
        self.context = SimpleNamespace(
            logger=SimpleNamespace(
                info=lambda *a, **k: None,
                warning=lambda *a, **k: None,
                error=lambda *a, **k: None,
                debug=lambda *a, **k: None,
            ),
        )
        self.params = SimpleNamespace(
            mech_marketplace_config=SimpleNamespace(
                mech_marketplace_address="0x" + "ff" * 20,
                offchain_failover_max_retries=failover_retries,
                offchain_url=offchain_url,
                priority_mech_address=priority_mech_address,
                auto_deposit_cap_per_cycle=auto_deposit_cap,
                use_dynamic_mech_selection=use_dynamic_mech_selection,
            ),
            offchain_deposit_target_calls=deposit_target_calls,
            multisend_address="0x" + "ee" * 20,
            mech_chain_id="gnosis",
            mech_wrapped_native_token_address=mech_wrapped_native_token_address,
        )
        from packages.valory.skills.mech_interact_abci.states.base import MechMetadata

        self.synchronized_data = SimpleNamespace(
            ranked_mechs=ranked_mechs,
            mech_requests=(
                mech_requests
                if mech_requests is not None
                else [MechMetadata(prompt="hi", tool="t", nonce="n")]
            ),
            safe_contract_address="0x" + "cc" * 20,
            offchain_pending_request=offchain_pending_request,
        )
        self._contract_api_responses = list(contract_api_responses)
        self._http_responses = list(http_responses)
        self._ledger_api_responses = list(ledger_api_responses or [])
        self._signature = signature
        # Record of calls (lets tests assert what the executor did).
        self.posted_urls: List[str] = []
        self.signed_request_ids: List[bytes] = []
        # Record of every contract-api kwargs and every safe-tx kwargs so
        # tests can assert the deposit value (token path: kwargs["amount"]
        # on ERC20.build_approval_tx; native path: kwargs["value"] on
        # _get_safe_tx_hash).
        self.contract_api_calls: List[Dict[str, Any]] = []
        self.safe_tx_calls: List[Dict[str, Any]] = []
        # Record of every ledger-api call so tests can assert the account
        # and chain the Safe-balance guard queried.
        self.ledger_api_calls: List[Dict[str, Any]] = []

    # Required kwargs per canonical contract callable, on top of the framework-
    # supplied set (performative, contract_address, contract_id, chain_id).
    # Every callable exercised by ``OffchainRequestExecutor`` is listed here;
    # if a call site drops one of these, the stub raises instead of silently
    # returning the queued response -- which is how bennyjo's ``sender=``
    # vs ``sender_address=`` and missing-``amount`` regressions passed CI
    # before this stub was tightened.
    _CANONICAL_REQUIRED_KWARGS = {
        "get_nonce": {"sender_address"},
        "get_chain_id": set(),
        "get_payment_type": set(),
        "get_max_delivery_rate": set(),
        "get_balance_tracker_for_mech_type": {"mech_type"},
        "get_token_address": set(),
        "build_deposit_for_data": {"account", "amount"},
        "build_approval_tx": {"spender", "amount"},
        "check_balance": {"account"},
        "get_tx_data": {"multi_send_txs"},
        # Safe-tx-hash build sits on every settlement path; a dropped kwarg
        # here silently produces a wrong tx hash and the deposit / delivery
        # is signed against a stale envelope. Kept in step with
        # `_build_safe_tx_for_single_call`'s call site.
        "get_raw_safe_transaction_hash": {
            "to_address",
            "value",
            "data",
            "safe_tx_gas",
            "operation",
        },
    }

    def get_contract_api_response(self, **kwargs: Any) -> Any:
        if False:
            yield  # make this a generator
        callable_name = str(kwargs.get("contract_callable", ""))
        expected = self._CANONICAL_REQUIRED_KWARGS.get(callable_name)
        if expected is not None:
            missing = expected - set(kwargs.keys())
            if missing:
                raise AssertionError(
                    f"Canonical contract callable {callable_name!r} missing "
                    f"required kwargs {sorted(missing)}; call site passed "
                    f"{sorted(kwargs.keys())}."
                )
        self.contract_api_calls.append(kwargs)
        return self._contract_api_responses.pop(0)

    def get_http_response(self, **kwargs: Any) -> Any:
        if False:
            yield
        self.posted_urls.append(kwargs.get("url", ""))
        return self._http_responses.pop(0)

    def get_ledger_api_response(self, **kwargs: Any) -> Any:
        if False:
            yield  # make this a generator
        self.ledger_api_calls.append(kwargs)
        return self._ledger_api_responses.pop(0)

    def get_signature(self, digest: bytes, is_deprecated_mode: bool = True) -> Any:
        if False:
            yield
        self.signed_request_ids.append(digest)
        return self._signature

    def _get_safe_tx_hash(self, **kwargs: Any) -> Any:
        if False:
            yield
        self.safe_tx_calls.append(kwargs)
        return "0x" + "fe" * 32


def _drive(gen: Any) -> Any:
    """Run an executor generator to completion (no framework yields)."""
    try:
        while True:
            next(gen)
    except StopIteration as exc:
        return exc.value


class TestPickNextMechFallback:
    """Static-URL fallback gating (review C2)."""

    def test_blank_priority_address_does_not_yield_blank(self) -> None:
        """No ranked mech + no configured priority → ``(None, None)``.

        Previously returned ``("", offchain_url)`` and the caller appended
        an empty address to ``attempted`` before crashing in the contract
        reads.
        """
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            priority_mech_address=None,
            offchain_url="https://static.example",
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        assert executor._pick_next_mech([]) == (None, None)

    def test_static_fallback_used_with_real_priority(self) -> None:
        """A configured priority + static URL is returned when ranked is empty."""
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            priority_mech_address="0x" + "ab" * 20,
            offchain_url="https://static.example",
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        addr, url = executor._pick_next_mech([])
        assert addr == "0x" + "ab" * 20
        assert url == "https://static.example"

    def test_static_fallback_skipped_if_priority_already_attempted(self) -> None:
        """Once the static priority is attempted, the fallback is exhausted."""
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            priority_mech_address="0x" + "ab" * 20,
            offchain_url="https://static.example",
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        assert executor._pick_next_mech(["0x" + "AB" * 20]) == (None, None)


class TestSignRequestIdWrapsForSafeValidation:
    """Executor-level guard: the AEA boundary receives the wrapped digest.

    The wrapping happens inside ``_sign_request_id`` (not at the AEA
    boundary), so a regression that reverts to signing the raw
    ``request_id`` would surface here as ``signed_request_ids`` no
    longer matching the ``compute_safe_message_hash`` output.
    """

    def test_sign_request_id_produces_safe_valid_signature(self) -> None:
        """The digest sent to ``get_signature`` is the SafeMessage wrapping."""
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        request_id = bytes.fromhex("cd" * 32)
        chain_id = 100
        signature = _drive(executor._sign_request_id(request_id, chain_id))
        assert signature is not None
        # Only the wrapped digest — never the raw request_id — reached
        # the framework boundary. A raw-request_id call would let Safe's
        # ``checkSignatures`` recover the wrong signer and revert with
        # ``GS026`` at delivery-with-signatures settlement.
        expected_wrapped = compute_safe_message_hash(
            request_id,
            stub.synchronized_data.safe_contract_address,
            chain_id,
        )
        assert stub.signed_request_ids == [expected_wrapped]
        assert request_id not in stub.signed_request_ids


class TestFreshCycle:
    """End-to-end executor cycles with mocked reads + HTTP (review C8)."""

    def _native_reads(self) -> List[Any]:
        """Sequence of contract reads a single happy-path attempt consumes."""
        return [
            _state_resp({"data": 100}),  # _resolve_chain_id_int
            _state_resp({"data": 7}),  # _read_on_chain_nonce
            _state_resp({"payment_type": _NATIVE_PAYMENT_TYPE}),
            _state_resp({"max_delivery_rate": 10**16}),
        ]

    def test_oversized_metadata_returns_all_failed(self) -> None:
        """A prompt over the CID single-block ceiling fails cleanly (C2).

        Pre-fix ``build_request_metadata`` raised ``ValueError`` and
        nothing on this path caught it, so the FSM re-entered the round
        every period with the same crash. Now it routes through
        ``OFFCHAIN_ALL_FAILED`` with ``OFFCHAIN_METADATA_OVERSIZE`` as
        the failure reason.
        """
        from packages.valory.skills.mech_interact_abci.states.base import (
            MechMetadata,
            OFFCHAIN_METADATA_OVERSIZE,
        )

        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo("0x" + "aa" * 20, "https://mech-aa.example")],
            contract_api_responses=[],
            http_responses=[],
            # Well above the 256 KiB single-block ceiling.
            mech_requests=[
                MechMetadata(prompt="x" * (300 * 1024), tool="t", nonce="n")
            ],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        assert result.last_failure_reason == OFFCHAIN_METADATA_OVERSIZE
        # No mech POST attempted -- rejected before wire access.
        assert len(stub.posted_urls) == 0

    def test_done_first_try(self) -> None:
        """200 on the first mech yields ``OFFCHAIN_DONE`` and a pending blob."""
        mech_addr = "0x" + "aa" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=self._native_reads(),
            http_responses=[_make_http_response(200)],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert isinstance(result, OffchainCycleResult)
        assert result.offchain_result == Event.OFFCHAIN_DONE.value
        assert result.pending_request_json is not None
        pending = PendingRequest.from_dict(json.loads(result.pending_request_json))
        assert pending is not None
        assert pending.mech_address == mech_addr
        # The signed digest is the SafeMessage-wrapped request_id
        # (chain_id=100 from ``_native_reads``, safe from ``_StubBehaviour``),
        # not the raw request_id — Safe.isValidSignature would revert with
        # ``GS026`` on a raw-request_id sig at settlement.
        assert len(stub.signed_request_ids) == 1
        expected_wrapped = compute_safe_message_hash(
            bytes.fromhex(pending.request_id),
            stub.synchronized_data.safe_contract_address,
            100,
        )
        assert stub.signed_request_ids[0] == expected_wrapped
        # Only one HTTP attempt was made on the happy path.
        assert len(stub.posted_urls) == 1

    def test_402_under_cap_returns_deposit_needed(self) -> None:
        """A structured 402 within the cap builds a Safe tx + pending blob.

        Also asserts:
        - ``mech_requests_json`` is carried on this branch (consensus
          selection_key includes it; null would discard the original
          prompt/tool/nonce on the retry leg).
        - ``tx_submitter`` is the off-chain sentinel so the consumer
          multiplexer routes the settled deposit back into
          ``MechRequestRound`` (not forward to ``MechResponseRound``).
        """
        from packages.valory.skills.mech_interact_abci.states.request import (
            OFFCHAIN_DEPOSIT_TX_SUBMITTER,
        )

        mech_addr = "0x" + "aa" * 20
        canonical_tracker = "0x" + "11" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=[
                *self._native_reads(),
                # _validate_402_destination → marketplace.get_balance_tracker_for_mech_type
                _state_resp({"data": canonical_tracker}),
                # _build_native_deposit_tx → BalanceTracker.build_deposit_for_data
                _state_resp({"data": b"\x01\x02\x03"}),
                # _build_safe_tx_for_single_call → GnosisSafe.get_raw_safe_transaction_hash
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[
                _make_http_response(
                    402,
                    _make_402_body(pay_to=canonical_tracker, required=500, current=0),
                ),
            ],
            # Safe holds enough native to fund the sized deposit; the
            # balance guard passes and the deposit tx is built.
            ledger_api_responses=[_ledger_balance_resp(10 * 10**18)],
            auto_deposit_cap=10**18,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_DEPOSIT_NEEDED.value
        assert result.tx_hash is not None
        # tx_hash MUST be the packed ``hash_payload_to_hex`` form so the
        # downstream ``transaction_settlement`` skill can decode it back
        # to the Safe tx params. A raw 64-char hash would deserialise to
        # garbage and the deposit would never settle. Round-trip decode
        # to assert structure.
        from packages.valory.skills.transaction_settlement_abci.payload_tools import (
            skill_input_hex_to_payload,
        )

        decoded = skill_input_hex_to_payload(result.tx_hash)
        assert decoded["safe_tx_hash"] == "fe" * 32
        assert decoded["to_address"].lower() == canonical_tracker.lower()
        # The Safe envelope's ether_value must equal the deposit amount
        # sized by ``_compute_deposit_amount``: for this fixture,
        # deposit_target_calls (10) * maxDeliveryRate (1e16) = 1e17,
        # which exceeds the 500-wei shortfall and is under the 1e18 cap.
        # A regression that hard-coded ``value=0`` -- the same silent
        # zero-value bug the upstream native BalanceTracker now guards
        # against with ``amount <= 0`` in ``build_deposit_for_data`` --
        # would leave the calldata intact but the on-chain deposit
        # would credit zero, so the retry keeps 402ing. Pin the exact
        # value here so the value=deposit_amount plumbing is verified
        # end-to-end.
        assert decoded["ether_value"] == 10 * 10**16
        assert result.pending_request_json is not None
        # tx_submitter MUST be the sentinel so consumer multiplexers
        # can route the settled deposit back into MechRequestRound. Pin
        # the literal value too: an identity-only check against the
        # imported symbol would let a silent rename move both sides
        # together while consumers (which hardcode the string) break.
        assert result.tx_submitter == OFFCHAIN_DEPOSIT_TX_SUBMITTER
        assert result.tx_submitter == "mech_request_round_offchain_deposit"
        # Structured request metadata survives the deposit settlement.
        assert result.mech_requests_json is not None
        parsed = json.loads(result.mech_requests_json)
        assert len(parsed) == 1
        assert parsed[0]["prompt"] == "hi"
        assert parsed[0]["tool"] == "t"
        assert parsed[0]["nonce"] == "n"

    def test_deposit_needed_then_retry_preserves_mech_requests(self) -> None:
        """End-to-end deposit-retry: the original prompt/tool/nonce survives.

        Drives the full DEPOSIT_NEEDED → settlement → retry-DONE round
        trip. Pre-fix, the DEPOSIT_NEEDED branch dropped
        ``mech_requests_json`` so the round committed ``null``, the
        retry's read of ``self._synced.mech_requests`` returned ``[]``,
        and the consumer at ``FinishedOffchainMechRequestRound`` lost
        the structured metadata. The fix keeps the metadata on the
        first leg so the second leg's serialised result still names the
        original request.
        """
        from packages.valory.skills.mech_interact_abci.states.base import MechMetadata

        # First leg: fresh cycle returns DEPOSIT_NEEDED.
        mech_addr = "0x" + "aa" * 20
        canonical_tracker = "0x" + "11" * 20
        request_meta = MechMetadata(prompt="solve 2+2", tool="math", nonce="n1")
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=[
                *self._native_reads(),
                _state_resp({"data": canonical_tracker}),
                _state_resp({"data": b"\x01\x02\x03"}),
                # _build_safe_tx_for_single_call → GnosisSafe.get_raw_safe_transaction_hash
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[
                _make_http_response(
                    402,
                    _make_402_body(pay_to=canonical_tracker, required=500, current=0),
                ),
            ],
            ledger_api_responses=[_ledger_balance_resp(10 * 10**18)],
            auto_deposit_cap=10**18,
            mech_requests=[request_meta],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        first_leg = _drive(executor._fresh_cycle())
        assert first_leg.offchain_result == Event.OFFCHAIN_DEPOSIT_NEEDED.value

        # Simulate the round committing the payload, including the
        # ``mech_requests_json`` carried on this branch. The retry then
        # reads ``synced_data.mech_requests`` for the response payload.
        retry_mech_requests = [
            MechMetadata(**item) for item in json.loads(first_leg.mech_requests_json)
        ]
        pending = PendingRequest.from_dict(json.loads(first_leg.pending_request_json))
        assert pending is not None

        retry_stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"data": 100}),  # _resolve_chain_id_int on retry
            ],
            http_responses=[_make_http_response(200)],
            mech_requests=retry_mech_requests,
        )
        retry_executor = OffchainRequestExecutor(retry_stub)  # type: ignore[arg-type]
        retry_result = _drive(retry_executor._retry_pending(pending))
        assert retry_result.offchain_result == Event.OFFCHAIN_DONE.value
        # The original prompt + tool + nonce survive into the second leg.
        assert retry_result.mech_requests_json is not None
        parsed = json.loads(retry_result.mech_requests_json)
        assert len(parsed) == 1
        assert parsed[0]["prompt"] == "solve 2+2"
        assert parsed[0]["tool"] == "math"
        assert parsed[0]["nonce"] == "n1"

    def test_402_over_cap_short_circuits(self) -> None:
        """When the shortfall exceeds the cap, failover is short-circuited.

        Other ranked mechs would charge the same Safe balance, so spending
        the failover budget can't change the outcome.
        """
        mech_addr = "0x" + "aa" * 20
        other_addr = "0x" + "bb" * 20
        stub = _StubBehaviour(
            ranked_mechs=[
                _FakeMechInfo(mech_addr, "https://mech-aa.example"),
                _FakeMechInfo(other_addr, "https://mech-bb.example"),
            ],
            contract_api_responses=self._native_reads(),
            http_responses=[
                _make_http_response(402, _make_402_body(required=10**20)),
            ],
            auto_deposit_cap=1,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        assert result.last_failure_reason == OFFCHAIN_402_INSUFFICIENT
        # The second mech is never tried — the OVER_CAP shortcut fires first.
        assert len(stub.posted_urls) == 1

    def test_timeout_then_done_uses_failover(self) -> None:
        """First mech 5xx → executor falls over to the second and succeeds."""
        mech_a = "0x" + "aa" * 20
        mech_b = "0x" + "bb" * 20
        # Each attempt consumes one payment_type + one delivery_rate read.
        per_attempt_reads = [
            _state_resp({"payment_type": _NATIVE_PAYMENT_TYPE}),
            _state_resp({"max_delivery_rate": 10**16}),
        ]
        stub = _StubBehaviour(
            ranked_mechs=[
                _FakeMechInfo(mech_a, "https://mech-aa.example"),
                _FakeMechInfo(mech_b, "https://mech-bb.example"),
            ],
            contract_api_responses=[
                _state_resp({"data": 100}),
                _state_resp({"data": 7}),
                *per_attempt_reads,  # attempt against mech_a
                *per_attempt_reads,  # attempt against mech_b
            ],
            http_responses=[
                _make_http_response(503),
                _make_http_response(200),
            ],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_DONE.value
        assert len(stub.posted_urls) == 2

    def test_all_timeouts_all_failed(self) -> None:
        """All ranked mechs fail → ``OFFCHAIN_ALL_FAILED`` with the right label."""
        per_attempt_reads = [
            _state_resp({"payment_type": _NATIVE_PAYMENT_TYPE}),
            _state_resp({"max_delivery_rate": 10**16}),
        ]
        stub = _StubBehaviour(
            ranked_mechs=[
                _FakeMechInfo("0x" + "aa" * 20, "https://mech-aa.example"),
            ],
            contract_api_responses=[
                _state_resp({"data": 100}),
                _state_resp({"data": 7}),
                *per_attempt_reads,
            ],
            http_responses=[_make_http_response(503)],
            failover_retries=0,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value

    def test_chain_id_read_failure_fails_cycle(self) -> None:
        """``chainId`` read failure surfaces as ``OFFCHAIN_TIMEOUT_ALL_MECHS``.

        Verifies the new logging path (review C4) is reached on a
        non-STATE performative.
        """
        from packages.valory.protocols.contract_api import ContractApiMessage

        bad = SimpleNamespace(
            performative=ContractApiMessage.Performative.ERROR,
            state=SimpleNamespace(body={}),
        )
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo("0x" + "aa" * 20, "https://m")],
            contract_api_responses=[bad],
            http_responses=[],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        assert result.last_failure_reason == OFFCHAIN_TIMEOUT_ALL_MECHS

    def test_zero_delivery_rate_treated_as_invalid_read(self) -> None:
        """``maxDeliveryRate == 0`` is rejected like a non-STATE read (review C20).

        Without the guard a zero rate slips past ``is None`` and silently
        collapses ``_compute_deposit_amount`` to one-call sizing on every
        cycle. The executor must reject the read and fail over, surfacing
        ``OFFCHAIN_TIMEOUT_ALL_MECHS`` once the failover budget is spent.
        """
        mech_addr = "0x" + "aa" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://m")],
            contract_api_responses=[
                _state_resp({"data": 100}),
                _state_resp({"data": 7}),
                _state_resp({"payment_type": _NATIVE_PAYMENT_TYPE}),
                # ``0`` would silently neutralize the dynamic sizing.
                _state_resp({"max_delivery_rate": 0}),
            ],
            http_responses=[],
            failover_retries=0,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        assert result.last_failure_reason == OFFCHAIN_TIMEOUT_ALL_MECHS

    def test_negative_delivery_rate_treated_as_invalid_read(self) -> None:
        """Negative ``maxDeliveryRate`` is also rejected as an invalid read."""
        mech_addr = "0x" + "aa" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://m")],
            contract_api_responses=[
                _state_resp({"data": 100}),
                _state_resp({"data": 7}),
                _state_resp({"payment_type": _NATIVE_PAYMENT_TYPE}),
                _state_resp({"max_delivery_rate": -1}),
            ],
            http_responses=[],
            failover_retries=0,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        assert result.last_failure_reason == OFFCHAIN_TIMEOUT_ALL_MECHS

    def test_null_metadata_nonce_does_not_crash_after_signed_post(self) -> None:
        """``MechMetadata(nonce=None)`` from an untyped producer doesn't crash mid-cycle.

        Reproduces the bug where the strengthened ``__post_init__``
        isinstance guard would fire on the direct construction path
        AFTER ``_post_signed_request`` had already POSTed -- turning a
        quiet shape bug into an uncaught ValueError past an irreversible
        side effect. The coercion at the ``_build_pending`` call site
        (``str(request_meta.nonce or "")``) restores the graceful
        happy-path completion.
        """
        from packages.valory.skills.mech_interact_abci.states.base import MechMetadata

        mech_addr = "0x" + "aa" * 20
        # ``MechMetadata.nonce`` is typed ``str`` but the dataclass has
        # no runtime enforcement; the DB-blob → ``MechMetadata(**dict)``
        # path can pass a null through. Unpacking bypasses the type
        # checker exactly as the production path does.
        null_nonce_meta = MechMetadata(**{"prompt": "x", "tool": "t", "nonce": None})  # type: ignore[arg-type]
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=self._native_reads(),
            http_responses=[_make_http_response(200)],
            mech_requests=[null_nonce_meta],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_DONE.value
        assert result.pending_request_json is not None
        pending = PendingRequest.from_dict(json.loads(result.pending_request_json))
        assert pending is not None
        # ``None`` normalised to empty, not the literal string ``"None"``.
        assert pending.metadata_nonce == ""


class TestRetryPending:
    """Resumed cycle after a deposit settles (review C8)."""

    def _pending_raw(self) -> Dict[str, Any]:
        return {
            "request_id": _VALID_REQUEST_ID_HEX,
            "nonce": 7,
            "mech_address": "0x" + "aa" * 20,
            "mech_url": "https://mech-aa.example",
            "sender": "0x" + "bb" * 20,
            "delivery_rate": 1000,
            "ipfs_hash": "0x" + "cc" * 31,
            "ipfs_data": '{"prompt":"x"}',
        }

    def test_retry_done_returns_offchain_done(self) -> None:
        """The retry POST returns 200 → ``OFFCHAIN_DONE`` with the same pending blob."""
        pending = PendingRequest.from_dict(self._pending_raw())
        assert pending is not None
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                # Retry re-reads chain_id so the SafeMessage wrapping is
                # bound to the correct settlement chain (mirrors the
                # fresh-cycle read on `_resolve_chain_id_int`).
                _state_resp({"data": 100}),
            ],
            http_responses=[_make_http_response(200)],
            offchain_pending_request=self._pending_raw(),
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._retry_pending(pending))
        assert result.offchain_result == Event.OFFCHAIN_DONE.value
        # The signed digest is the SafeMessage-wrapped cached request_id.
        # Signing the raw cached hex would revert on-chain with ``GS026``.
        expected_wrapped = compute_safe_message_hash(
            bytes.fromhex(pending.request_id),
            stub.synchronized_data.safe_contract_address,
            100,
        )
        assert stub.signed_request_ids == [expected_wrapped]
        assert result.pending_request_json is not None

    def test_retry_second_402_surfaces_insufficient(self) -> None:
        """A 402 on retry surfaces ``OFFCHAIN_402_INSUFFICIENT`` without looping."""
        pending = PendingRequest.from_dict(self._pending_raw())
        assert pending is not None
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"data": 100}),  # _resolve_chain_id_int on retry
            ],
            http_responses=[_make_http_response(402, _make_402_body())],
            offchain_pending_request=self._pending_raw(),
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._retry_pending(pending))
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        assert result.last_failure_reason == OFFCHAIN_402_INSUFFICIENT

    def test_retry_timeout_surfaces_timeout_not_insufficient(self) -> None:
        """A network-blip TIMEOUT on retry maps to TIMEOUT, not INSUFFICIENT.

        The previous branch hard-coded ``OFFCHAIN_402_INSUFFICIENT`` for
        every non-DONE outcome, so an operator investigating a retry
        failure saw "your deposit cap is too small" when the real cause
        was the mech being unreachable. Distinct labels let the operator
        route the diagnosis to network/server health vs deposit sizing.
        """
        pending = PendingRequest.from_dict(self._pending_raw())
        assert pending is not None
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"data": 100}),  # _resolve_chain_id_int on retry
            ],
            # ``None`` from the HTTP framework helper surfaces as TIMEOUT
            # in ``_post_signed_request`` -- see the outcome mapping there.
            http_responses=[None],
            offchain_pending_request=self._pending_raw(),
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._retry_pending(pending))
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        assert result.last_failure_reason == OFFCHAIN_TIMEOUT_ALL_MECHS

    def test_retry_pending_fails_closed_when_chain_id_unavailable(self) -> None:
        """A non-STATE ``chainId`` read on retry short-circuits before signing.

        The retry path resolves the chain id fresh so the SafeMessage
        wrapping is bound to the settlement chain. If that read fails
        (RPC blip, contract-api ERROR performative), the executor must
        exit with ``OFFCHAIN_ALL_FAILED`` + ``OFFCHAIN_TIMEOUT_ALL_MECHS``
        without signing anything or POSTing. Without this test the guard
        (``if chain_id_int is None``) could be inverted, or the failure
        reason silently swapped, and CI would still pass.
        """
        from packages.valory.protocols.contract_api import ContractApiMessage

        pending = PendingRequest.from_dict(self._pending_raw())
        assert pending is not None
        bad = SimpleNamespace(
            performative=ContractApiMessage.Performative.ERROR,
            state=SimpleNamespace(body={}),
        )
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[bad],
            http_responses=[],
            offchain_pending_request=self._pending_raw(),
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._retry_pending(pending))
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        assert result.last_failure_reason == OFFCHAIN_TIMEOUT_ALL_MECHS
        assert stub.signed_request_ids == []
        assert stub.posted_urls == []


class TestLoadPendingRequestDistinguishesCorruption:
    """The pending-request loader distinguishes empty-raw from corrupt-raw.

    Before the warning, ``_load_pending_request`` returned ``None`` on
    both "nothing was pending" (normal) and "a stored payload failed
    validation" (paid-for correlation lost) -- ``run()`` fell to
    ``_fresh_cycle`` identically. That path silently abandons the
    deposit-just-settled bridge with no diagnostic trail.
    """

    def test_corrupt_raw_logs_and_falls_to_fresh(self) -> None:
        """Truthy-but-invalid raw payload emits a validation warning."""
        warnings: List[str] = []
        # Missing required ``request_id`` triggers ``KeyError`` inside
        # ``from_dict`` (swallowed by the pre-existing except block).
        # Keeps ``mech_url``/``sender`` so the log key list demonstrates
        # the payload wasn't empty -- just broken.
        corrupt = {
            "mech_url": "https://mech-aa.example",
            "sender": "0x" + "bb" * 20,
        }
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            offchain_pending_request=corrupt,
        )
        stub.context.logger.warning = lambda *a, **k: warnings.append(a[0] if a else "")
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = executor._load_pending_request()
        assert result is None
        assert any("failed validation" in w for w in warnings)

    def test_empty_raw_is_quiet(self) -> None:
        """Empty raw payload is the ``run()``-fresh-cycle path -- no warning."""
        warnings: List[str] = []
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            offchain_pending_request=None,
        )
        stub.context.logger.warning = lambda *a, **k: warnings.append(a[0] if a else "")
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        assert executor._load_pending_request() is None
        assert warnings == []

    def test_stale_pending_metadata_nonce_mismatch_discards(self) -> None:
        """Nonce-mismatched pending is discarded (C1).

        A well-formed pending whose nonce differs from the incoming
        request is dropped instead of resumed. Previously the executor
        re-POSTed the delivered request and paired the old answer with
        the new prompt.
        """
        from packages.valory.skills.mech_interact_abci.states.base import MechMetadata

        pending = {
            "request_id": "ab" * 32,
            "nonce": 7,
            "mech_address": "0x" + "aa" * 20,
            "mech_url": "https://mech-aa.example",
            "sender": "0x" + "cc" * 20,
            "delivery_rate": 10**16,
            "ipfs_hash": "0x" + "de" * 32,
            "ipfs_data": "{}",
            "metadata_nonce": "prev-nonce",
        }
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            offchain_pending_request=pending,
            mech_requests=[MechMetadata(prompt="p", tool="t", nonce="new-nonce")],
        )
        infos: List[str] = []
        stub.context.logger.info = lambda *a, **k: infos.append(a[0] if a else "")
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        assert executor._load_pending_request() is None
        assert any("stale offchain_pending_request" in msg for msg in infos), infos

    def test_matching_metadata_nonce_resumes(self) -> None:
        """When the incoming request carries the same nonce, resume normally.

        Guard rail so the C1 stale-discard doesn't accidentally kill the
        deposit-just-settled retry leg (the retry period re-enters with
        the same ``mech_requests`` list).
        """
        from packages.valory.skills.mech_interact_abci.states.base import MechMetadata

        pending = {
            "request_id": "ab" * 32,
            "nonce": 7,
            "mech_address": "0x" + "aa" * 20,
            "mech_url": "https://mech-aa.example",
            "sender": "0x" + "cc" * 20,
            "delivery_rate": 10**16,
            "ipfs_hash": "0x" + "de" * 32,
            "ipfs_data": "{}",
            "metadata_nonce": "same-nonce",
        }
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            offchain_pending_request=pending,
            mech_requests=[MechMetadata(prompt="p", tool="t", nonce="same-nonce")],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = executor._load_pending_request()
        assert result is not None
        assert result.metadata_nonce == "same-nonce"


class TestComputeDepositAmount:
    """Direct unit tests for the deposit-sizing math.

    The shape ``target_calls × delivery_rate`` (clamped by the cap, floored
    at the shortfall) is the agent's contract with the operator. Tests pin
    each branch so a regression surfaces at this layer rather than only
    when a token deposit lands the wrong amount on-chain.
    """

    @staticmethod
    def _make_executor(
        target_calls: int = 10,
        cap: int = 10**18,
    ) -> "OffchainRequestExecutor":
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            auto_deposit_cap=cap,
            deposit_target_calls=target_calls,
        )
        return OffchainRequestExecutor(stub)  # type: ignore[arg-type]

    def test_uses_target_calls_times_delivery_rate(self) -> None:
        """Happy path: empty balance, plenty of cap, deposit covers N calls."""
        executor = self._make_executor(target_calls=10, cap=10**18)
        # delivery_rate = 100, shortfall = 100 (one call), current_balance = 0
        result = executor._compute_deposit_amount(
            shortfall=100, current_balance=0, delivery_rate=100
        )
        # desired = 10 × 100 = 1000; needed = max(100, 1000 − 0) = 1000.
        assert result == 1000

    def test_partial_balance_top_up_to_target(self) -> None:
        """Partial existing balance: top up to the target, not over."""
        executor = self._make_executor(target_calls=10, cap=10**18)
        # Existing 700 at the tracker; topping up to 1000 needs only 300.
        result = executor._compute_deposit_amount(
            shortfall=100, current_balance=700, delivery_rate=100
        )
        # needed = max(100, 1000 − 700) = 300.
        assert result == 300

    def test_uses_shortfall_when_desired_below_shortfall(self) -> None:
        """If desired − balance < shortfall, deposit at least the shortfall.

        Triggers when the current_balance already covers most of the target
        but the next request still needs more than the balance can supply.
        Without the ``max(shortfall, …)`` floor the deposit would be too
        small to settle the current 402.
        """
        executor = self._make_executor(target_calls=10, cap=10**18)
        # desired = 1000; current_balance = 900 → desired − balance = 100;
        # but shortfall = 500 (e.g. the mech raised delivery_rate mid-cycle).
        result = executor._compute_deposit_amount(
            shortfall=500, current_balance=900, delivery_rate=100
        )
        assert result == 500

    def test_clamped_to_cap(self) -> None:
        """``desired > cap``: deposit equals cap (still ≥ shortfall)."""
        executor = self._make_executor(target_calls=100, cap=500)
        # desired = 100 × 100 = 10_000; clamped to cap 500.
        result = executor._compute_deposit_amount(
            shortfall=100, current_balance=0, delivery_rate=100
        )
        assert result == 500

    def test_zero_cap_disables_clamp(self) -> None:
        """``cap == 0`` disables the safety bound (legacy/optional shape).

        Matches the existing ``auto_deposit_cap_per_cycle or 0`` semantics:
        operators who explicitly set the cap to 0 disable auto-deposit (cap
        below any positive shortfall), so this branch is unreachable in
        practice when 402 contains a real shortfall. Pinned because the
        accessor uses ``or 0`` and a future refactor might reintroduce 0
        as ``unlimited`` accidentally.
        """
        executor = self._make_executor(target_calls=10, cap=0)
        result = executor._compute_deposit_amount(
            shortfall=100, current_balance=0, delivery_rate=100
        )
        # cap=0 disables clamp → returns full desired = 1000.
        assert result == 1000


class TestDepositScalesWithDeliveryRate:
    """Integration: a delivery_rate change → the next deposit scales linearly.

    Regression for the dynamic-pricing concern: the deposit is computed off
    the live on-chain ``delivery_rate`` (read per attempt via
    ``_resolve_delivery_rate``), not a static config or the 402's
    ``required`` field. Doubling the mech price doubles the next deposit
    without operator action.
    """

    _CANONICAL_TRACKER = "0x" + "11" * 20
    _CANONICAL_TOKEN = "0x" + "22" * 20

    def _token_reads_with_rate(self, delivery_rate: int) -> List[Any]:
        return [
            _state_resp({"data": 100}),
            _state_resp({"data": 7}),
            _state_resp({"payment_type": _TOKEN_PAYMENT_TYPE}),
            _state_resp({"max_delivery_rate": delivery_rate}),
        ]

    def _drive_cycle(self, delivery_rate: int) -> _StubBehaviour:
        mech_addr = "0x" + "aa" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=[
                *self._token_reads_with_rate(delivery_rate),
                # _validate_402_destination → tracker + token
                _state_resp({"data": self._CANONICAL_TRACKER}),
                _state_resp({"token_address": self._CANONICAL_TOKEN}),
                # _read_safe_token_balance → Safe holds enough token to
                # cover any deposit sized within the auto-deposit cap.
                _state_resp({"token": 10 * 10**18}),
                # token deposit multisend: approve + depositFor + multisend
                _state_resp({"data": b"\xaa"}),
                _state_resp({"data": b"\xbb"}),
                _state_resp({"data": "0xcc"}),
                # _build_safe_tx_for_single_call → GnosisSafe.get_raw_safe_transaction_hash
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[
                _make_http_response(
                    402,
                    _make_402_body(
                        pay_to=self._CANONICAL_TRACKER,
                        asset=self._CANONICAL_TOKEN,
                        required=delivery_rate,  # 402 says "you need 1 call"
                        current=0,
                    ),
                ),
            ],
            auto_deposit_cap=10**18,
            deposit_target_calls=10,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_DEPOSIT_NEEDED.value
        return stub

    @staticmethod
    def _approve_amount(stub: _StubBehaviour) -> int:
        """Pull the ``amount`` kwarg from the ERC20.build_approval_tx call."""
        for call in stub.contract_api_calls:
            if call.get("contract_callable") == "build_approval_tx":
                return int(call["amount"])
        raise AssertionError("no build_approval_tx call recorded")

    def test_doubling_delivery_rate_doubles_deposit(self) -> None:
        """delivery_rate × 2 → deposit × 2, no operator action."""
        low = self._drive_cycle(delivery_rate=100)
        high = self._drive_cycle(delivery_rate=200)
        # target_calls=10 in both cycles; deposit = 10 × delivery_rate.
        assert self._approve_amount(low) == 1000
        assert self._approve_amount(high) == 2000


class TestDepositBuilderSafeBalancePrecheck:
    """The deposit builders refuse to build when the Safe cannot fund the tx.

    Both ``_build_native_deposit_tx`` and ``_build_token_deposit_multisend``
    read the requester Safe's on-chain balance for the asset the tx will
    forward and skip the build if the balance is short of ``shortfall``.
    This keeps the downstream tx-settlement path from submitting an
    execTransaction that the Safe cannot fulfil. When the balance covers
    ``shortfall`` but not the full sized ``deposit_amount`` (a prebuy),
    the deposit is clamped so this request still lands.

    The fixture sets ``shortfall`` and ``deposit_amount`` to distinct
    values (shortfall much smaller than deposit_amount) so the tests
    pin the guard's operand: swapping ``< shortfall`` for ``<
    deposit_amount`` would flip test results. Under the previous
    fixture (``current_balance=0, required=deposit_amount``) the two
    quantities were equal and the guard's operand was invisible.
    """

    _NATIVE_PAY_TO = "0x" + "11" * 20
    _TOKEN_PAY_TO = "0x" + "11" * 20
    _TOKEN_ASSET = "0x" + "22" * 20
    # Distinct constants so the guard's operand (shortfall vs deposit_amount)
    # is pinned. Numbers picked so ``shortfall`` (100) << ``deposit_amount``
    # (10_000) with ``current_balance`` (900) proving ``shortfall`` is
    # ``required - current_balance``, not just ``required``.
    _CURRENT_BALANCE = 900
    _REQUIRED = 1000
    _SHORTFALL = _REQUIRED - _CURRENT_BALANCE  # 100
    _DEPOSIT_AMOUNT = 10_000

    @staticmethod
    def _capture_warnings(stub: _StubBehaviour) -> List[str]:
        """Route logger.warning through a list so tests can assert log content."""
        collected: List[str] = []

        def _warn(msg: str, *args: Any, **kwargs: Any) -> None:
            collected.append(str(msg))

        stub.context.logger.warning = _warn  # type: ignore[assignment]
        return collected

    @classmethod
    def _native_challenge(cls) -> PaymentChallenge:
        return PaymentChallenge(
            pay_to=cls._NATIVE_PAY_TO,
            asset="0x" + "00" * 20,
            chain_id=100,
            current_balance=cls._CURRENT_BALANCE,
            required=cls._REQUIRED,
            error="",
        )

    @classmethod
    def _token_challenge(cls) -> PaymentChallenge:
        return PaymentChallenge(
            pay_to=cls._TOKEN_PAY_TO,
            asset=cls._TOKEN_ASSET,
            chain_id=100,
            current_balance=cls._CURRENT_BALANCE,
            required=cls._REQUIRED,
            error="",
        )

    def test_native_short_balance_returns_short_and_warns(self) -> None:
        """Native Safe balance < shortfall → skip with ``_BALANCE_SHORT`` reason.

        Pins the guard against ``shortfall`` (not ``deposit_amount``): the
        Safe holds one wei less than ``shortfall`` while still holding far
        less than ``deposit_amount`` in absolute terms. Swapping the guard
        to compare against ``deposit_amount`` would still classify this as
        short (correctly), but combined with the clamp test below, the
        pair pins the shortfall semantics.
        """
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            # Safe holds one wei less than the current-request shortfall.
            ledger_api_responses=[_ledger_balance_resp(self._SHORTFALL - 1)],
        )
        warnings = self._capture_warnings(stub)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_SHORT
        # No contract-api call is made past the balance guard: the deposit
        # calldata and Safe-tx builders never ran.
        assert stub.contract_api_calls == []
        # Warning names the Safe, the shortfall, and the (short) balance.
        assert any(
            "native balance" in w
            and str(self._SHORTFALL) in w
            and str(self._SHORTFALL - 1) in w
            and stub.synchronized_data.safe_contract_address in w
            for w in warnings
        )

    def test_native_balance_between_shortfall_and_deposit_amount_clamps(
        self,
    ) -> None:
        """Safe holds >= shortfall but < deposit_amount → build with clamp.

        This is the fixture-differentiated test: Safe balance sits
        strictly between ``shortfall`` and ``deposit_amount``. If the
        guard had been kept against ``deposit_amount``, the build would
        be skipped and this request would never land. Comparing against
        ``shortfall`` and clamping ``deposit_amount = min(deposit_amount,
        safe_balance)`` lets the current 402 settle while cutting the
        prebuy to what the Safe actually holds.
        """
        clamped_amount = 5_000  # shortfall (100) < 5_000 < deposit_amount (10_000)
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                # _build_native_deposit_tx → BalanceTracker.build_deposit_for_data
                _state_resp({"data": b"\x01\x02\x03"}),
                # _build_safe_tx_for_single_call → GnosisSafe.get_raw_safe_transaction_hash
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[],
            ledger_api_responses=[_ledger_balance_resp(clamped_amount)],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is not None
        assert result.reason is None
        # The depositFor calldata was built with the clamped amount, not
        # the operator-configured prebuy. Pins the clamp end-to-end.
        deposit_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_deposit_for_data"
        )
        assert deposit_call["amount"] == clamped_amount
        # And the Safe envelope's ether_value equals the clamped amount
        # too (native path forwards value on the outer tx).
        safe_tx_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "get_raw_safe_transaction_hash"
        )
        assert safe_tx_call["value"] == clamped_amount

    def test_native_sufficient_balance_builds_tx_at_full_amount(self) -> None:
        """Native Safe balance >= deposit_amount → build at full prebuy."""
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                # _build_native_deposit_tx → BalanceTracker.build_deposit_for_data
                _state_resp({"data": b"\x01\x02\x03"}),
                # _build_safe_tx_for_single_call → GnosisSafe.get_raw_safe_transaction_hash
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[],
            ledger_api_responses=[_ledger_balance_resp(self._DEPOSIT_AMOUNT * 10)],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is not None
        assert result.reason is None
        # No clamp: the depositFor calldata uses the full prebuy amount.
        deposit_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_deposit_for_data"
        )
        assert deposit_call["amount"] == self._DEPOSIT_AMOUNT
        # The ledger call queried the requester Safe on the mech chain.
        assert len(stub.ledger_api_calls) == 1
        assert stub.ledger_api_calls[0]["account"] == (
            stub.synchronized_data.safe_contract_address
        )
        assert stub.ledger_api_calls[0]["chain_id"] == "gnosis"
        # Both downstream contract reads ran (deposit calldata + safe tx hash).
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        assert callables == [
            "build_deposit_for_data",
            "get_raw_safe_transaction_hash",
        ]

    def test_token_short_balance_returns_short_and_skips_multisend(self) -> None:
        """Token Safe balance < shortfall → skip with ``_BALANCE_SHORT`` reason."""
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                # _read_safe_token_balance → ERC20.check_balance returns short balance.
                _state_resp({"token": self._SHORTFALL - 1}),
            ],
            http_responses=[],
        )
        warnings = self._capture_warnings(stub)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_token_deposit_multisend(
                self._token_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_SHORT
        # The only contract-api call was the balance check; approve /
        # depositFor / multisend never ran.
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        assert callables == ["check_balance"]
        assert "build_approval_tx" not in callables
        assert "build_deposit_for_data" not in callables
        assert any(
            "token" in w
            and self._TOKEN_ASSET in w
            and str(self._SHORTFALL) in w
            and str(self._SHORTFALL - 1) in w
            for w in warnings
        )

    def test_token_balance_between_shortfall_and_deposit_amount_clamps(
        self,
    ) -> None:
        """Token Safe balance in the clamp band → both approve and depositFor use the clamped amount.

        The multisend must ship a single amount to both legs; if either
        the approve or depositFor uses a different value, the settlement
        reverts or the deposit under-credits. Pin them together.
        """
        clamped_amount = 500  # shortfall (100) < 500 < deposit_amount (10_000)
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"token": clamped_amount}),
                _state_resp({"data": b"\xaa"}),
                _state_resp({"data": b"\xbb"}),
                _state_resp({"data": "0xcc"}),
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_token_deposit_multisend(
                self._token_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is not None
        assert result.reason is None
        approve_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_approval_tx"
        )
        deposit_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_deposit_for_data"
        )
        assert approve_call["amount"] == clamped_amount
        assert deposit_call["amount"] == clamped_amount

    def test_token_sufficient_balance_builds_multisend_at_full_amount(self) -> None:
        """Token Safe balance >= deposit_amount → multisend at full prebuy."""
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"token": self._DEPOSIT_AMOUNT * 10}),
                _state_resp({"data": b"\xaa"}),
                _state_resp({"data": b"\xbb"}),
                _state_resp({"data": "0xcc"}),
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_token_deposit_multisend(
                self._token_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is not None
        assert result.reason is None
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        # Balance check runs first, then the standard multisend sequence.
        assert callables == [
            "check_balance",
            "build_approval_tx",
            "build_deposit_for_data",
            "get_tx_data",
            "get_raw_safe_transaction_hash",
        ]
        # No clamp: both approve and depositFor use the full amount.
        approve_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_approval_tx"
        )
        deposit_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_deposit_for_data"
        )
        assert approve_call["amount"] == self._DEPOSIT_AMOUNT
        assert deposit_call["amount"] == self._DEPOSIT_AMOUNT
        # The check_balance call was scoped to the requester Safe.
        assert stub.contract_api_calls[0]["account"] == (
            stub.synchronized_data.safe_contract_address
        )

    def test_native_balance_read_failure_returns_read_failed_reason(self) -> None:
        """Ledger-API ERROR on the native balance read → ``_BALANCE_READ_FAILED``.

        Retry depends on the consumer re-entering with a fresh request;
        the alternative — building an execTransaction against an unknown
        balance — is worse. Tagging as ``_BALANCE_READ_FAILED`` lets the
        caller route to ``OFFCHAIN_TIMEOUT_ALL_MECHS`` rather than
        surfacing a spurious "top up your Safe" signal to the consumer.
        """
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            ledger_api_responses=[_ledger_error_resp()],
        )
        warnings = self._capture_warnings(stub)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_READ_FAILED
        # Nothing downstream ran.
        assert stub.contract_api_calls == []
        # Both the low-level "read failed" warning and the builder-level
        # "skipping … balance read failed" warning surface, so operators
        # can distinguish an RPC blip from a short-balance skip.
        assert any("Safe native balance read failed" in w for w in warnings)
        assert any(
            "Skipping native deposit build" in w and "balance read failed" in w
            for w in warnings
        )

    def test_native_balance_missing_key_classified_as_read_failed(self) -> None:
        """STATE response with empty body → ``_BALANCE_READ_FAILED``.

        Directly exercises the missing-``get_balance_result`` branch of
        ``_read_safe_native_balance``. Under a pre-fix reader that
        returned bare ``None`` for every failure mode, this shape was
        indistinguishable from a genuinely short balance and would have
        surfaced ``OFFCHAIN_402_INSUFFICIENT`` on an RPC schema drift.
        """
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            ledger_api_responses=[_ledger_missing_key_resp()],
        )
        warnings = self._capture_warnings(stub)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        balance, reason = _drive(executor._read_safe_native_balance())
        assert balance is None
        assert reason == _BALANCE_READ_FAILED
        assert any(
            "Safe native balance response missing" in w and "get_balance_result" in w
            for w in warnings
        )

    @pytest.mark.parametrize(
        "bad_value",
        ["not-a-number", [1, 2, 3], {"nested": "dict"}],
    )
    def test_native_balance_non_numeric_classified_as_read_failed(
        self, bad_value: Any
    ) -> None:
        """Non-numeric balance value → ``_BALANCE_READ_FAILED``, not a raise.

        Guards the ``int(raw)`` coercion so a malformed body from a
        degraded RPC surfaces as a failover-eligible outcome rather than
        crashing the executor with an uncaught ``ValueError``.
        """
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            ledger_api_responses=[_ledger_non_numeric_resp(bad_value)],
        )
        warnings = self._capture_warnings(stub)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        balance, reason = _drive(executor._read_safe_native_balance())
        assert balance is None
        assert reason == _BALANCE_READ_FAILED
        assert any("is not numeric" in w for w in warnings)

    def test_token_balance_read_failure_returns_read_failed_reason(self) -> None:
        """ERC20 balance read failure → ``_BALANCE_READ_FAILED`` (companion to native).

        Without this coverage the token path could silently regress to
        emitting ``_BALANCE_SHORT`` on an RPC blip and the caller would
        map to ``OFFCHAIN_402_INSUFFICIENT`` instead of the
        failover-eligible timeout label.
        """
        from packages.valory.protocols.contract_api import ContractApiMessage

        error_resp = SimpleNamespace(
            performative=ContractApiMessage.Performative.ERROR,
            state=SimpleNamespace(body={}),
        )
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[error_resp],
            http_responses=[],
        )
        warnings = self._capture_warnings(stub)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_token_deposit_multisend(
                self._token_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_READ_FAILED
        # Only the failing check_balance call ran; the multisend legs did not.
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        assert callables == ["check_balance"]
        assert any(
            "Skipping token deposit build" in w and "balance read failed" in w
            for w in warnings
        )

    def test_token_balance_non_numeric_classified_as_read_failed(self) -> None:
        """ERC20 ``token`` slot returning a non-numeric value → ``_BALANCE_READ_FAILED``.

        Directly exercises the ``int(raw)`` coercion branch in
        ``_read_safe_token_balance``: a mech-side wrapper returning a
        string that isn't parseable as an int must surface as
        ``_BALANCE_READ_FAILED`` so the caller failovers rather than
        crashing on an uncaught ValueError.
        """
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[_state_resp({"token": "abc"})],
            http_responses=[],
        )
        warnings = self._capture_warnings(stub)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        balance, reason = _drive(executor._read_safe_token_balance(self._TOKEN_ASSET))
        assert balance is None
        assert reason == _BALANCE_READ_FAILED
        assert any("non-numeric token balance" in w for w in warnings)


class TestNativeDepositWithWrappedNativeFallback:
    """Native deposit path folds in the wrapped-native balance as a fallback.

    Pins: which reads happen in each shape (native alone, native+wrapped,
    wrapped-unset), the deposit-amount clamp when wrapped is only a
    partial top-up, the exact-shortfall boundary, the zero-wrapped
    branch, and the read-failure handling (wrapped-read failure
    surfaces only when native alone can't cover the shortfall).
    """

    _NATIVE_PAY_TO = "0x" + "11" * 20
    _WRAPPED_ADDR = "0x" + "44" * 20
    _CURRENT_BALANCE = 900
    _REQUIRED = 1000
    _SHORTFALL = _REQUIRED - _CURRENT_BALANCE  # 100
    _DEPOSIT_AMOUNT = 10_000

    @staticmethod
    def _capture_warnings(stub: _StubBehaviour) -> List[str]:
        collected: List[str] = []

        def _warn(msg: str, *args: Any, **kwargs: Any) -> None:
            collected.append(str(msg))

        stub.context.logger.warning = _warn  # type: ignore[assignment]
        return collected

    @classmethod
    def _native_challenge(cls) -> PaymentChallenge:
        return PaymentChallenge(
            pay_to=cls._NATIVE_PAY_TO,
            asset="0x" + "00" * 20,
            chain_id=100,
            current_balance=cls._CURRENT_BALANCE,
            required=cls._REQUIRED,
            error="",
        )

    def test_native_short_but_wrapped_covers_deposit_builds_multisend(self) -> None:
        """Native < shortfall, native + wrapped >= deposit_amount → unwrap+deposit multisend.

        The Safe holds one wei less than the current-request shortfall in
        native but plenty of wrapped-native. The build must (a) route
        through the multisend path, (b) size the unwrap leg to exactly
        ``deposit_amount - native``, and (c) submit the deposit leg at
        the full ``deposit_amount`` with the same value as its inner
        ``value`` — the atomic multisend guarantees the freshly-unwrapped
        native is available for the deposit's inner CALL.
        """
        native_balance = self._SHORTFALL - 1  # 99
        wrapped_balance = self._DEPOSIT_AMOUNT * 10
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                # _read_safe_token_balance → wxDAI check_balance
                _state_resp({"token": wrapped_balance}),
                # ERC20.build_withdraw_tx (unwrap leg calldata)
                _state_resp({"data": b"\xaa"}),
                # BalanceTracker.build_deposit_for_data (deposit leg calldata)
                _state_resp({"data": b"\xbb"}),
                # MultiSend.get_tx_data (packed multisend)
                _state_resp({"data": "0xcc"}),
                # GnosisSafe.get_raw_safe_transaction_hash (settlement envelope)
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[],
            ledger_api_responses=[_ledger_balance_resp(native_balance)],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is not None
        assert result.reason is None
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        # Full multisend sequence ran: wxDAI read, unwrap calldata,
        # deposit calldata, multisend pack, Safe envelope.
        assert callables == [
            "check_balance",
            "build_withdraw_tx",
            "build_deposit_for_data",
            "get_tx_data",
            "get_raw_safe_transaction_hash",
        ]
        # Unwrap leg was sized to exactly what native was short of the
        # full deposit: unwrap = deposit_amount - native.
        withdraw_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_withdraw_tx"
        )
        assert withdraw_call["amount"] == self._DEPOSIT_AMOUNT - native_balance
        # Deposit leg funds at the full deposit_amount (no clamp: reachable
        # native + wrapped covers it comfortably).
        deposit_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_deposit_for_data"
        )
        assert deposit_call["amount"] == self._DEPOSIT_AMOUNT
        # Multisend contains both legs in order [unwrap, deposit] with
        # the deposit leg's value set to deposit_amount so the inner CALL
        # forwards value out of the Safe's post-unwrap native balance.
        multisend_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "get_tx_data"
        )
        legs = multisend_call["multi_send_txs"]
        assert len(legs) == 2
        assert legs[0]["to"] == self._WRAPPED_ADDR
        assert legs[0]["value"] == 0
        assert legs[1]["to"] == self._NATIVE_PAY_TO
        assert legs[1]["value"] == self._DEPOSIT_AMOUNT
        # Outer Safe envelope carries value=0 and DELEGATECALLs multisend.
        safe_tx_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "get_raw_safe_transaction_hash"
        )
        assert safe_tx_call["value"] == 0
        assert safe_tx_call["to_address"] == stub.params.multisend_address
        assert safe_tx_call["operation"] == 1  # DELEGATE_CALL

    def test_native_short_and_wrapped_partial_clamps_deposit(self) -> None:
        """Native + wrapped in [shortfall, deposit_amount) → multisend clamped.

        Safe has 40 native and 1000 wrapped; deposit_amount is 10_000
        and shortfall is 100. Sum (1040) covers the shortfall so the
        request should land, but not the full prebuy. Clamp to 1040 and
        size the unwrap to 1040 - 40 = 1000 (all wrapped burned to fund
        the clamped deposit).
        """
        native_balance = 40
        wrapped_balance = 1000
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"token": wrapped_balance}),
                _state_resp({"data": b"\xaa"}),
                _state_resp({"data": b"\xbb"}),
                _state_resp({"data": "0xcc"}),
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[],
            ledger_api_responses=[_ledger_balance_resp(native_balance)],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is not None
        clamped = native_balance + wrapped_balance
        withdraw_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_withdraw_tx"
        )
        deposit_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_deposit_for_data"
        )
        # Deposit clamped to reachable balance, unwrap burns all wrapped.
        assert deposit_call["amount"] == clamped
        assert withdraw_call["amount"] == wrapped_balance

    def test_native_and_wrapped_both_short_returns_short_and_names_both(
        self,
    ) -> None:
        """Native + wrapped < shortfall → ``_BALANCE_SHORT``, warning names both.

        Even after burning every wrapped-native token, the Safe can't
        fund the current request. The warning must name both operand
        balances so operators can tell whether they need a native or
        wrapped top-up.
        """
        native_balance = 40
        wrapped_balance = 50  # sum 90 < shortfall 100
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"token": wrapped_balance}),
            ],
            http_responses=[],
            ledger_api_responses=[_ledger_balance_resp(native_balance)],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        warnings = self._capture_warnings(stub)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_SHORT
        # Only the balance reads ran; no deposit or multisend build.
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        assert callables == ["check_balance"]
        assert any(
            str(native_balance) in w
            and str(wrapped_balance) in w
            and str(self._SHORTFALL) in w
            for w in warnings
        )

    def test_native_covers_deposit_skips_wrapped_read(self) -> None:
        """Native >= deposit_amount → wrapped-native read is skipped entirely.

        Skips a contract read on the happy path: if the Safe already
        holds enough native to fund the full sized deposit, no
        wrapped-native balance is fetched even when the wrapped address
        is configured. Pins the ``balance < deposit_amount`` guard.
        """
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"data": b"\x01\x02\x03"}),
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[],
            ledger_api_responses=[_ledger_balance_resp(self._DEPOSIT_AMOUNT * 10)],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is not None
        # No check_balance in the call list: the wrapped read never ran.
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        assert callables == [
            "build_deposit_for_data",
            "get_raw_safe_transaction_hash",
        ]

    def test_wrapped_addr_unset_falls_back_to_native_only(self) -> None:
        """No wrapped address configured → native-only guard, no wrapped read.

        When the deployment doesn't configure a wrapped-native token
        (e.g. chains where the mech uses ERC20 payment and native isn't
        drained from the Safe by msg.value paths), the wrapped read is
        skipped and the guard collapses to the pre-existing "native
        short → BALANCE_SHORT" behaviour.
        """
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
            ledger_api_responses=[_ledger_balance_resp(self._SHORTFALL - 1)],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_SHORT
        # No contract-api calls: neither the wrapped read nor any
        # downstream builder ran.
        assert stub.contract_api_calls == []

    def test_wrapped_read_failed_but_native_covers_shortfall_uses_single_call(
        self,
    ) -> None:
        """Wrapped read fails, native alone covers shortfall → single-call, no unwrap.

        A transient contract-api failure on the wrapped-native read must
        not block deposits when native alone would already fund the
        current 402. The build clamps to native (existing single-call
        path) and continues.
        """
        from packages.valory.protocols.contract_api import ContractApiMessage

        error_resp = SimpleNamespace(
            performative=ContractApiMessage.Performative.ERROR,
            state=SimpleNamespace(body={}),
        )
        native_balance = 5_000  # >= shortfall (100), < deposit_amount (10_000)
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                # wxDAI read fails → _read_safe_token_balance returns (None, READ_FAILED).
                error_resp,
                # Downstream single-call build proceeds with clamp to native.
                _state_resp({"data": b"\x01\x02\x03"}),
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[],
            ledger_api_responses=[_ledger_balance_resp(native_balance)],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is not None
        assert result.reason is None
        # Single-call path: no multisend, no unwrap calldata.
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        assert callables == [
            "check_balance",
            "build_deposit_for_data",
            "get_raw_safe_transaction_hash",
        ]
        deposit_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_deposit_for_data"
        )
        assert deposit_call["amount"] == native_balance
        safe_tx_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "get_raw_safe_transaction_hash"
        )
        # Native single-call: outer envelope carries the value directly.
        assert safe_tx_call["value"] == native_balance
        assert safe_tx_call["operation"] == 0  # CALL

    def test_wrapped_read_failed_and_native_short_surfaces_read_failed(
        self,
    ) -> None:
        """Wrapped read fails and native < shortfall → surface ``_BALANCE_READ_FAILED``.

        If we can't decide because wrapped is unknown AND native is
        below the shortfall, we cannot say "BALANCE_SHORT" (wrapped
        might have covered it). Surface the transient read failure so
        the caller retries rather than mislabels the Safe as underfunded.
        """
        from packages.valory.protocols.contract_api import ContractApiMessage

        error_resp = SimpleNamespace(
            performative=ContractApiMessage.Performative.ERROR,
            state=SimpleNamespace(body={}),
        )
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[error_resp],
            http_responses=[],
            ledger_api_responses=[_ledger_balance_resp(self._SHORTFALL - 1)],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        warnings = self._capture_warnings(stub)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_READ_FAILED
        # Only the wxDAI read ran; the deposit build path was not reached.
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        assert callables == ["check_balance"]
        assert any(
            "wrapped-native read failed" in w and str(self._SHORTFALL) in w
            for w in warnings
        )

    def test_native_exact_shortfall_boundary_proceeds_to_build(self) -> None:
        """Native == shortfall boundary → proceeds (guard is strict ``<``).

        Pins the operand of the ``reachable < shortfall`` guard: an
        accidental swap to ``<=`` would flip this exact-boundary case
        from a successful (clamped) build to a spurious
        ``_BALANCE_SHORT``.
        """
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"token": 0}),
                _state_resp({"data": b"\x01\x02\x03"}),
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[],
            ledger_api_responses=[_ledger_balance_resp(self._SHORTFALL)],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is not None
        assert result.reason is None
        deposit_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_deposit_for_data"
        )
        # Deposit clamps to native (= shortfall) since wrapped=0.
        assert deposit_call["amount"] == self._SHORTFALL

    def test_multisend_withdraw_calldata_failure_surfaces_read_failed(
        self,
    ) -> None:
        """Withdraw calldata read failure in the multisend path → ``_BALANCE_READ_FAILED``.

        Pins the routing of the first of three inner reads: a
        contract-api ERROR when building the unwrap leg's calldata is a
        transient RPC issue, not an underfunded Safe. Regressing this
        return to ``(None, None)`` would mislabel the cycle as
        ``OFFCHAIN_402_INSUFFICIENT``.
        """
        from packages.valory.protocols.contract_api import ContractApiMessage

        error_resp = SimpleNamespace(
            performative=ContractApiMessage.Performative.ERROR,
            state=SimpleNamespace(body={}),
        )
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                # wxDAI check_balance: enough to enter the multisend path
                _state_resp({"token": self._DEPOSIT_AMOUNT * 10}),
                # withdraw calldata read: contract-api ERROR
                error_resp,
            ],
            http_responses=[],
            ledger_api_responses=[
                _ledger_balance_resp(self._SHORTFALL - 1),
            ],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_READ_FAILED
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        assert callables == ["check_balance", "build_withdraw_tx"]

    def test_multisend_deposit_calldata_failure_surfaces_read_failed(
        self,
    ) -> None:
        """Deposit calldata read failure in the multisend path → ``_BALANCE_READ_FAILED``.

        Pins the routing of the second inner read. Withdraw succeeded,
        but the depositFor calldata build failed; same reasoning as the
        withdraw case.
        """
        from packages.valory.protocols.contract_api import ContractApiMessage

        error_resp = SimpleNamespace(
            performative=ContractApiMessage.Performative.ERROR,
            state=SimpleNamespace(body={}),
        )
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"token": self._DEPOSIT_AMOUNT * 10}),
                _state_resp({"data": b"\xaa"}),
                # deposit calldata read: contract-api ERROR
                error_resp,
            ],
            http_responses=[],
            ledger_api_responses=[
                _ledger_balance_resp(self._SHORTFALL - 1),
            ],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_READ_FAILED
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        assert callables == [
            "check_balance",
            "build_withdraw_tx",
            "build_deposit_for_data",
        ]

    def test_multisend_pack_failure_surfaces_read_failed(self) -> None:
        """MultiSend.get_tx_data failure in the multisend path → ``_BALANCE_READ_FAILED``.

        Pins the routing of the third inner read. Both leg calldatas
        succeeded, but packing them into the multisend envelope failed;
        same reasoning as the withdraw case.
        """
        from packages.valory.protocols.contract_api import ContractApiMessage

        error_resp = SimpleNamespace(
            performative=ContractApiMessage.Performative.ERROR,
            state=SimpleNamespace(body={}),
        )
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"token": self._DEPOSIT_AMOUNT * 10}),
                _state_resp({"data": b"\xaa"}),
                _state_resp({"data": b"\xbb"}),
                # multisend pack read: contract-api ERROR
                error_resp,
            ],
            http_responses=[],
            ledger_api_responses=[
                _ledger_balance_resp(self._SHORTFALL - 1),
            ],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_READ_FAILED
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        assert callables == [
            "check_balance",
            "build_withdraw_tx",
            "build_deposit_for_data",
            "get_tx_data",
        ]

    def test_multisend_withdraw_non_bytes_surfaces_read_failed(self) -> None:
        """Withdraw calldata returns a non-bytes payload → ``_BALANCE_READ_FAILED``.

        Sibling to ``test_multisend_withdraw_calldata_failure_surfaces_read_failed``:
        that one covers the ``is None`` branch (contract-api ERROR),
        this one covers the ``not isinstance(..., (bytes, bytearray))``
        branch. A future refactor of ``_read_contract_state`` that
        returns a sentinel instead of ``None`` on failure would
        silently bypass the ``is None`` branch and leave the isinstance
        guards as the sole discriminator; without this test that
        regression would ship untested.
        """
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"token": self._DEPOSIT_AMOUNT * 10}),
                # withdraw calldata: wrong shape (int instead of bytes)
                _state_resp({"data": 42}),
            ],
            http_responses=[],
            ledger_api_responses=[
                _ledger_balance_resp(self._SHORTFALL - 1),
            ],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_READ_FAILED

    def test_multisend_deposit_non_bytes_surfaces_read_failed(self) -> None:
        """Deposit calldata returns a non-bytes payload → ``_BALANCE_READ_FAILED``.

        Sibling isinstance-guard case for the deposit-calldata read.
        Same regression risk as the withdraw non-bytes test.
        """
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"token": self._DEPOSIT_AMOUNT * 10}),
                _state_resp({"data": b"\xaa"}),
                # deposit calldata: wrong shape
                _state_resp({"data": 42}),
            ],
            http_responses=[],
            ledger_api_responses=[
                _ledger_balance_resp(self._SHORTFALL - 1),
            ],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_READ_FAILED

    def test_multisend_pack_non_string_surfaces_read_failed(self) -> None:
        """MultiSend.get_tx_data returns a non-string payload → ``_BALANCE_READ_FAILED``.

        Sibling isinstance-guard case for the multisend pack. The
        wrapper is contracted to return a hex string here; wrapping a
        non-string payload in ``HexBytes`` would fail deep in the
        settlement path, so the isinstance guard's routing has to
        survive future refactors.
        """
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"token": self._DEPOSIT_AMOUNT * 10}),
                _state_resp({"data": b"\xaa"}),
                _state_resp({"data": b"\xbb"}),
                # multisend pack: wrong shape (bytes instead of str)
                _state_resp({"data": b"\xcc"}),
            ],
            http_responses=[],
            ledger_api_responses=[
                _ledger_balance_resp(self._SHORTFALL - 1),
            ],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is None
        assert result.reason == _BALANCE_READ_FAILED

    def test_wrapped_zero_and_native_covers_shortfall_clamps_to_native(
        self,
    ) -> None:
        """Wrapped read succeeds with 0 balance, native covers shortfall → single-call clamp.

        Distinct from ``test_wrapped_read_failed_but_native_covers_shortfall``:
        here the read succeeds and returns a genuine zero (no wrapped
        collateral). The build must not try to unwrap 0 tokens; instead
        it should clamp to native and take the single-call path.
        """
        native_balance = 5_000
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[
                _state_resp({"token": 0}),
                _state_resp({"data": b"\x01\x02\x03"}),
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[],
            ledger_api_responses=[_ledger_balance_resp(native_balance)],
            mech_wrapped_native_token_address=self._WRAPPED_ADDR,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._build_native_deposit_tx(
                self._native_challenge(), self._DEPOSIT_AMOUNT
            )
        )
        assert result.tx_hex is not None
        assert result.reason is None
        # Single-call path: check_balance (returning 0) then deposit
        # calldata + Safe envelope. No unwrap calldata, no multisend
        # pack — a zero-amount unwrap leg would encode a wasted call.
        callables = [c.get("contract_callable") for c in stub.contract_api_calls]
        assert callables == [
            "check_balance",
            "build_deposit_for_data",
            "get_raw_safe_transaction_hash",
        ]
        deposit_call = next(
            c
            for c in stub.contract_api_calls
            if c.get("contract_callable") == "build_deposit_for_data"
        )
        assert deposit_call["amount"] == native_balance


class TestFreshCycleSafeBalanceGuard:
    """End-to-end ``_fresh_cycle`` wiring for the Safe-balance precheck.

    The direct-builder tests in ``TestDepositBuilderSafeBalancePrecheck``
    pin the reader / builder return shapes; these tests drive a full
    cycle so the caller-side outcome mapping at
    ``offchain_request.py:_fresh_cycle`` (``build.reason ==
    _BALANCE_READ_FAILED`` → ``OFFCHAIN_TIMEOUT_ALL_MECHS`` vs everything
    else → ``OFFCHAIN_402_INSUFFICIENT``) is exercised. Without this an
    accidental swap of the two labels — or a change from ``==`` to
    ``!=`` on the reason check — would still pass the direct-builder
    suite but ship the wrong signal to the consumer.
    """

    _CANONICAL_TRACKER = "0x" + "11" * 20

    def _native_pre_402_reads(self) -> List[Any]:
        return [
            _state_resp({"data": 100}),  # _resolve_chain_id_int
            _state_resp({"data": 7}),  # _read_on_chain_nonce
            _state_resp({"payment_type": _NATIVE_PAYMENT_TYPE}),
            _state_resp({"max_delivery_rate": 10**16}),
        ]

    def test_short_safe_balance_surfaces_402_insufficient(self) -> None:
        """Safe balance below shortfall → OFFCHAIN_ALL_FAILED + OFFCHAIN_402_INSUFFICIENT.

        Failover to another ranked mech would charge the same Safe
        balance and reach the same result, so the executor short-circuits
        to the consumer with the "top up your Safe" label rather than
        burning the failover budget on certain failures.
        """
        mech_addr = "0x" + "aa" * 20
        stub = _StubBehaviour(
            ranked_mechs=[
                _FakeMechInfo(mech_addr, "https://mech-aa.example"),
                _FakeMechInfo("0x" + "bb" * 20, "https://mech-bb.example"),
            ],
            contract_api_responses=[
                *self._native_pre_402_reads(),
                # _validate_402_destination → canonical tracker read
                _state_resp({"data": self._CANONICAL_TRACKER}),
            ],
            http_responses=[
                _make_http_response(
                    402,
                    _make_402_body(
                        pay_to=self._CANONICAL_TRACKER,
                        required=1000,
                        current=0,
                    ),
                ),
            ],
            # Safe holds one wei less than the current-request shortfall.
            ledger_api_responses=[_ledger_balance_resp(999)],
            auto_deposit_cap=10**18,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        assert result.last_failure_reason == OFFCHAIN_402_INSUFFICIENT
        # Only one mech POST attempted: the balance-short label short-
        # circuits before the second ranked mech is tried.
        assert len(stub.posted_urls) == 1

    def test_balance_read_failure_surfaces_timeout_all_mechs(self) -> None:
        """Ledger-API ERROR on the balance read → OFFCHAIN_ALL_FAILED + OFFCHAIN_TIMEOUT_ALL_MECHS.

        Companion to the balance-short test above. The reason tag must
        route the RPC-blip case to the failover-eligible timeout label
        so operators see a network/RPC label rather than a spurious
        "top up your Safe" signal. A future refactor that collapses
        ``build.reason`` back to a bare ``None`` (losing the
        distinction) is caught here.
        """
        mech_addr = "0x" + "aa" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=[
                *self._native_pre_402_reads(),
                _state_resp({"data": self._CANONICAL_TRACKER}),
            ],
            http_responses=[
                _make_http_response(
                    402,
                    _make_402_body(
                        pay_to=self._CANONICAL_TRACKER,
                        required=1000,
                        current=0,
                    ),
                ),
            ],
            ledger_api_responses=[_ledger_error_resp()],
            auto_deposit_cap=10**18,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        assert result.last_failure_reason == OFFCHAIN_TIMEOUT_ALL_MECHS


class TestClassifyPaymentType:
    """``_classify_payment_type`` knows every deployed selector."""

    @pytest.mark.parametrize(
        "hex_const, expected_label",
        [
            (_PAYMENT_TYPE_HASH_NATIVE, "native"),
            (_PAYMENT_TYPE_HASH_TOKEN_OLAS, "token"),
            (_PAYMENT_TYPE_HASH_TOKEN_USDC, "token"),
            (_PAYMENT_TYPE_HASH_NVM_NATIVE, "nvm_native"),
            (_PAYMENT_TYPE_HASH_NVM_TOKEN, "nvm_token"),
        ],
    )
    def test_known_hashes_classified(self, hex_const: str, expected_label: str) -> None:
        """Each deployed selector maps to the right dispatch label."""
        label = OffchainRequestExecutor._classify_payment_type(
            bytes.fromhex(hex_const[2:])
        )
        assert label == expected_label

    def test_unknown_hash_returns_empty(self) -> None:
        """An unrecognised selector dispatches to "no auto-resolve"."""
        label = OffchainRequestExecutor._classify_payment_type(bytes.fromhex("ee" * 32))
        assert label == ""


class TestAttemptResultPlumbing:
    """Sanity over how ``_post_signed_request`` maps statuses to outcomes."""

    def _stub_with_response(self, response: Any) -> _StubBehaviour:
        return _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[response],
        )

    @pytest.mark.parametrize(
        "status_code, expected",
        [
            (200, OffchainAttemptOutcome.DONE),
            (503, OffchainAttemptOutcome.SERVER_BUSY),
            (418, OffchainAttemptOutcome.BAD_RESPONSE),
            # AEA http_client synthesises 600 on connection failures
            # (valory/http_client/connection.py:113). Map to TIMEOUT so
            # failover fires on the next ranked mech instead of the loop
            # burning the retry budget on a dead-mech ``BAD_RESPONSE``.
            (600, OffchainAttemptOutcome.TIMEOUT),
        ],
    )
    def test_status_to_outcome(
        self, status_code: int, expected: OffchainAttemptOutcome
    ) -> None:
        """Non-402 statuses map to their dedicated outcomes."""
        stub = self._stub_with_response(_make_http_response(status_code))
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        attempt: OffchainAttemptResult = _drive(
            executor._post_signed_request(
                mech_url="https://m",
                mech_address="0x" + "aa" * 20,
                ipfs_hash="0x" + "cc" * 31,
                ipfs_data="{}",
                request_id_bytes=bytes(32),
                signature_hex="0x" + "dd" * 65,
                nonce=0,
                delivery_rate=1,
                sender="0x" + "bb" * 20,
            )
        )
        assert attempt.outcome == expected

    def test_402_with_malformed_body_classified_bad_response(self) -> None:
        """A 402 whose body is not a structured challenge → ``BAD_RESPONSE``.

        Pre-fix this fell through to ``OFFCHAIN_TIMEOUT_ALL_MECHS`` (review
        C5) — a chargeable 402 reported as a network timeout.
        """
        stub = self._stub_with_response(_make_http_response(402, b"not-json"))
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        attempt = _drive(
            executor._post_signed_request(
                mech_url="https://m",
                mech_address="0x" + "aa" * 20,
                ipfs_hash="0x" + "cc" * 31,
                ipfs_data="{}",
                request_id_bytes=bytes(32),
                signature_hex="0x" + "dd" * 65,
                nonce=0,
                delivery_rate=1,
                sender="0x" + "bb" * 20,
            )
        )
        assert attempt.outcome == OffchainAttemptOutcome.BAD_RESPONSE


class TestValidate402Destination:
    """Cross-check 402 destination against on-chain BalanceTracker (Benny).

    These tests pin the recipient-validation behaviour added to defend
    against a malicious mech directing the auto-deposit to an
    attacker-chosen address.
    """

    _CANONICAL_TRACKER = "0x" + "11" * 20
    _CANONICAL_TOKEN = "0x" + "22" * 20

    def _native_pre_402_reads(self) -> List[Any]:
        return [
            _state_resp({"data": 100}),
            _state_resp({"data": 7}),
            _state_resp({"payment_type": _NATIVE_PAYMENT_TYPE}),
            _state_resp({"max_delivery_rate": 10**16}),
        ]

    def _token_pre_402_reads(self) -> List[Any]:
        return [
            _state_resp({"data": 100}),
            _state_resp({"data": 7}),
            _state_resp({"payment_type": _TOKEN_PAYMENT_TYPE}),
            _state_resp({"max_delivery_rate": 10**16}),
        ]

    def test_pay_to_mismatch_treated_as_bad_response(self) -> None:
        """Malicious payTo → BAD_RESPONSE; failover continues to next mech.

        The native deposit builder is never called because the canonical
        tracker read precedes it and rejects the destination.
        """
        mech_a = "0x" + "aa" * 20
        mech_b = "0x" + "bb" * 20
        attacker_address = "0x" + "ee" * 20
        per_attempt_reads = [
            _state_resp({"payment_type": _NATIVE_PAYMENT_TYPE}),
            _state_resp({"max_delivery_rate": 10**16}),
        ]
        stub = _StubBehaviour(
            ranked_mechs=[
                _FakeMechInfo(mech_a, "https://mech-aa.example"),
                _FakeMechInfo(mech_b, "https://mech-bb.example"),
            ],
            contract_api_responses=[
                _state_resp({"data": 100}),
                _state_resp({"data": 7}),
                *per_attempt_reads,  # attempt #1
                # validation → marketplace.get_balance_tracker_for_mech_type
                _state_resp({"data": self._CANONICAL_TRACKER}),
                *per_attempt_reads,  # attempt #2
                # validation on the second mech (also attacker payTo)
                _state_resp({"data": self._CANONICAL_TRACKER}),
            ],
            http_responses=[
                _make_http_response(402, _make_402_body(pay_to=attacker_address)),
                _make_http_response(402, _make_402_body(pay_to=attacker_address)),
            ],
            auto_deposit_cap=10**18,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        # Distinct from OFFCHAIN_TIMEOUT_ALL_MECHS: this is a misbehaving mech.
        from packages.valory.skills.mech_interact_abci.states.base import (
            OFFCHAIN_BAD_RESPONSE,
        )

        assert result.last_failure_reason == OFFCHAIN_BAD_RESPONSE
        # Both ranked mechs were attempted, deposit never built.
        assert len(stub.posted_urls) == 2

    def test_token_asset_mismatch_treated_as_bad_response(self) -> None:
        """A canonical payTo but attacker-chosen asset → BAD_RESPONSE.

        The token path additionally validates that ``challenge.asset``
        equals the tracker's ``token()`` getter so the Safe can't be
        steered into approving an arbitrary contract.
        """
        mech_addr = "0x" + "aa" * 20
        attacker_token = "0x" + "ee" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=[
                *self._token_pre_402_reads(),
                # validation: tracker matches but token does not
                _state_resp({"data": self._CANONICAL_TRACKER}),
                _state_resp({"token_address": self._CANONICAL_TOKEN}),
            ],
            http_responses=[
                _make_http_response(
                    402,
                    _make_402_body(
                        pay_to=self._CANONICAL_TRACKER, asset=attacker_token
                    ),
                ),
            ],
            auto_deposit_cap=10**18,
            failover_retries=0,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        from packages.valory.skills.mech_interact_abci.states.base import (
            OFFCHAIN_BAD_RESPONSE,
        )

        assert result.last_failure_reason == OFFCHAIN_BAD_RESPONSE

    def test_token_path_matching_destination_proceeds_to_build(self) -> None:
        """Canonical payTo + canonical asset on the token path proceeds.

        Validation succeeds, so the executor goes on to build the
        approve + depositFor multisend and surfaces
        ``OFFCHAIN_DEPOSIT_NEEDED``.
        """
        mech_addr = "0x" + "aa" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=[
                *self._token_pre_402_reads(),
                # validation: both tracker and token match
                _state_resp({"data": self._CANONICAL_TRACKER}),
                _state_resp({"token_address": self._CANONICAL_TOKEN}),
                # _read_safe_token_balance → ERC20.check_balance (Safe holds
                # enough of the token to fund the sized deposit).
                _state_resp({"token": 10 * 10**18}),
                # token deposit multisend reads: approve, depositFor, multisend
                _state_resp({"data": b"\xaa"}),
                _state_resp({"data": b"\xbb"}),
                _state_resp({"data": "0xcc"}),
                # _build_safe_tx_for_single_call → GnosisSafe.get_raw_safe_transaction_hash
                _state_resp({"tx_hash": "0x" + "fe" * 32}),
            ],
            http_responses=[
                _make_http_response(
                    402,
                    _make_402_body(
                        pay_to=self._CANONICAL_TRACKER,
                        asset=self._CANONICAL_TOKEN,
                        required=500,
                        current=0,
                    ),
                ),
            ],
            auto_deposit_cap=10**18,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_DEPOSIT_NEEDED.value
        assert result.tx_hash is not None

    def test_tracker_read_failure_treated_as_bad_response(self) -> None:
        """If the canonical-tracker read errors we refuse to deposit.

        Without the on-chain read we can't prove the payTo is safe, so
        we treat the attempt as a misbehaving mech and fail over.
        """
        from packages.valory.protocols.contract_api import ContractApiMessage

        bad = SimpleNamespace(
            performative=ContractApiMessage.Performative.ERROR,
            state=SimpleNamespace(body={}),
        )
        mech_addr = "0x" + "aa" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=[
                *self._native_pre_402_reads(),
                # validation read fails
                bad,
            ],
            http_responses=[
                _make_http_response(
                    402, _make_402_body(pay_to=self._CANONICAL_TRACKER)
                ),
            ],
            auto_deposit_cap=10**18,
            failover_retries=0,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        from packages.valory.skills.mech_interact_abci.states.base import (
            OFFCHAIN_BAD_RESPONSE,
        )

        assert result.last_failure_reason == OFFCHAIN_BAD_RESPONSE

    def test_zero_address_tracker_treated_as_bad_response(self) -> None:
        """A marketplace with no tracker registered for paymentType is refused.

        The on-chain read succeeds but returns the zero address, meaning
        no canonical destination exists; validation must refuse rather
        than vacuously accept the mech's claim.
        """
        mech_addr = "0x" + "aa" * 20
        zero = "0x" + "00" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=[
                *self._native_pre_402_reads(),
                _state_resp({"data": zero}),
            ],
            http_responses=[
                _make_http_response(
                    402, _make_402_body(pay_to=self._CANONICAL_TRACKER)
                ),
            ],
            auto_deposit_cap=10**18,
            failover_retries=0,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        from packages.valory.skills.mech_interact_abci.states.base import (
            OFFCHAIN_BAD_RESPONSE,
        )

        assert result.last_failure_reason == OFFCHAIN_BAD_RESPONSE


class TestSerialisePendingResponseNonce:
    """Wire semantics: ``MechInteractionResponse.nonce`` carries the metadata UUID.

    Downstream consumers (e.g. market-resolver's ``build_answer_tx``)
    correlate a response back to its request by matching this field
    against the caller-supplied metadata UUID. The legacy on-chain path
    (``request.py:660``) already does that. The offchain path must match,
    otherwise multi-request consumers silently drop every response.
    """

    def test_metadata_nonce_is_wire_value(self) -> None:
        """The placeholder's ``nonce`` must be the metadata UUID verbatim."""
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[],
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        request_id_bytes = bytes.fromhex("aa" * 32)
        metadata_nonce = "b0d591e2-b340-4fc0-b663-65301ca2c673"
        raw = executor._serialise_pending_response(request_id_bytes, metadata_nonce)
        responses = json.loads(raw)
        assert len(responses) == 1
        assert responses[0]["nonce"] == metadata_nonce


class TestReadContractState:
    """Shared GET_STATE adapter: performative + missing-key + logging.

    Locks in the invariants that were previously duplicated (with subtle
    drift, per bennyjo's structural review) across 11 call sites. If a
    future refactor loosens one branch here, every migrated caller
    inherits the regression -- so these tests carry the load for all of
    them.
    """

    def _stub(self, response: Any, warnings: List[str]) -> "_StubBehaviour":
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[response],
            http_responses=[],
        )
        stub.context.logger.warning = lambda *a, **k: warnings.append(a[0] if a else "")
        return stub

    def test_returns_body_value_on_state(self) -> None:
        """Happy path: STATE performative + result_key present → returns value."""
        warnings: List[str] = []
        stub = self._stub(_state_resp({"data": 42}), warnings)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._read_contract_state(
                contract_address="0x" + "aa" * 20,
                contract_id="dummy",
                contract_callable="get_nonce",
                error_label="dummy",
                sender_address="0x" + "bb" * 20,
            )
        )
        assert result == 42
        assert warnings == []

    def test_custom_result_key(self) -> None:
        """``result_key`` other than ``data`` (e.g. ``tx_hash``) is honoured."""
        warnings: List[str] = []
        stub = self._stub(_state_resp({"tx_hash": "0x" + "cc" * 32}), warnings)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._read_contract_state(
                contract_address="0x" + "aa" * 20,
                contract_id="dummy",
                contract_callable="get_raw_safe_transaction_hash",
                error_label="dummy",
                result_key="tx_hash",
                to_address="0x" + "bb" * 20,
                value=0,
                data=b"",
                safe_tx_gas=0,
                operation=0,
            )
        )
        assert result == "0x" + "cc" * 32
        assert warnings == []

    def test_non_state_performative_returns_none_with_warning(self) -> None:
        """Silent-failure branch: non-STATE performative now logs before returning None."""
        from packages.valory.protocols.contract_api import ContractApiMessage

        error_resp = SimpleNamespace(
            performative=ContractApiMessage.Performative.ERROR,
            state=SimpleNamespace(body={}),
        )
        warnings: List[str] = []
        stub = self._stub(error_resp, warnings)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._read_contract_state(
                contract_address="0x" + "aa" * 20,
                contract_id="dummy",
                contract_callable="get_nonce",
                error_label="MechMarketplace.mapNonces",
                sender_address="0x" + "bb" * 20,
            )
        )
        assert result is None
        assert any("MechMarketplace.mapNonces" in w for w in warnings)
        assert any("read failed" in w for w in warnings)

    def test_missing_result_key_returns_none_with_warning(self) -> None:
        """Key drift (renamed/dropped) surfaces as a warning, not silence.

        Before the extraction, only some sites checked the ``.get(key) is
        None`` branch and only two of those logged; the rest returned
        ``None`` with zero diagnostic trail. Now every caller inherits
        the same shape.
        """
        warnings: List[str] = []
        stub = self._stub(_state_resp({"other_key": 1}), warnings)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._read_contract_state(
                contract_address="0x" + "aa" * 20,
                contract_id="dummy",
                contract_callable="get_nonce",
                error_label="MechMarketplace.mapNonces",
                sender_address="0x" + "bb" * 20,
            )
        )
        assert result is None
        assert any("missing 'data'" in w for w in warnings)
        assert any("MechMarketplace.mapNonces" in w for w in warnings)

    def test_present_but_null_value_returns_none_with_warning(self) -> None:
        """Present-but-``None`` (e.g. degraded RPC returning ``{"data": null}``) also warns.

        Pre-refactor, several sites logged this shape via their combined
        ``is None or not isinstance(...)`` guards; the extraction moved
        the None-short-circuit above the isinstance branch, so the
        adapter must own this warning or the diagnostic trail is lost.
        """
        warnings: List[str] = []
        stub = self._stub(_state_resp({"data": None}), warnings)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(
            executor._read_contract_state(
                contract_address="0x" + "aa" * 20,
                contract_id="dummy",
                contract_callable="get_nonce",
                error_label="MechMarketplace.mapNonces",
                sender_address="0x" + "bb" * 20,
            )
        )
        assert result is None
        assert any("value is None" in w for w in warnings)
        assert any("MechMarketplace.mapNonces" in w for w in warnings)

    def test_chain_id_injected_from_params(self) -> None:
        """The helper injects ``chain_id`` from behaviour params so callers don't."""
        warnings: List[str] = []
        stub = self._stub(_state_resp({"data": 1}), warnings)
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        _drive(
            executor._read_contract_state(
                contract_address="0x" + "aa" * 20,
                contract_id="dummy",
                contract_callable="get_chain_id",
                error_label="dummy",
            )
        )
        recorded = stub.contract_api_calls[-1]
        assert "chain_id" in recorded


class TestStubValidatesCanonicalKwargs:
    """Regression guard for the test stub itself.

    The pre-fix stub swallowed every kwarg and popped the queued response
    regardless of shape, which let two contract-signature mismatches
    (``sender=`` vs ``sender_address=`` on ``get_nonce`` and missing
    ``amount`` on native ``build_deposit_for_data``) land silently. These
    tests fail if a future refactor loosens the stub again.
    """

    def _bare_stub(self) -> "_StubBehaviour":
        return _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[_state_resp({"data": 0})],
            http_responses=[],
        )

    def test_missing_sender_address_on_get_nonce_raises(self) -> None:
        """Dropping ``sender_address`` on ``get_nonce`` is a hard error."""
        stub = self._bare_stub()
        with pytest.raises(AssertionError, match="get_nonce"):
            _drive(
                stub.get_contract_api_response(
                    contract_callable="get_nonce",
                    contract_address="0x" + "aa" * 20,
                    contract_id="dummy",
                    chain_id="gnosis",
                )
            )

    def test_missing_kwargs_on_get_raw_safe_transaction_hash_raises(
        self,
    ) -> None:
        """Dropping any of the 5 required kwargs on the Safe-tx-hash build is a hard error.

        The Safe-tx-hash build is the highest-value call site: dropping
        e.g. ``value`` here would produce a wrong tx hash and every
        deposit / delivery would be signed against a stale envelope.
        Guard against the whole kwarg-drop class, not just one at a
        time.
        """
        for missing in (
            "to_address",
            "value",
            "data",
            "safe_tx_gas",
            "operation",
        ):
            stub = self._bare_stub()
            kwargs = {
                "to_address": "0x" + "bb" * 20,
                "value": 1,
                "data": b"",
                "safe_tx_gas": 0,
                "operation": 0,
            }
            kwargs.pop(missing)
            with pytest.raises(AssertionError, match="get_raw_safe_transaction_hash"):
                _drive(
                    stub.get_contract_api_response(
                        contract_callable="get_raw_safe_transaction_hash",
                        contract_address="0x" + "aa" * 20,
                        contract_id="dummy",
                        chain_id="gnosis",
                        **kwargs,
                    )
                )

    def test_correct_kwargs_pass_through(self) -> None:
        """The validator is additive -- valid calls still record + return."""
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[_state_resp({"data": 42})],
            http_responses=[],
        )
        response = _drive(
            stub.get_contract_api_response(
                contract_callable="get_nonce",
                contract_address="0x" + "aa" * 20,
                contract_id="dummy",
                chain_id="gnosis",
                sender_address="0x" + "bb" * 20,
            )
        )
        assert response.state.body["data"] == 42
        assert stub.contract_api_calls[-1]["sender_address"] == "0x" + "bb" * 20

    def test_native_build_deposit_requires_account_and_amount(self) -> None:
        """Native ``build_deposit_for_data`` needs both ``account`` and ``amount``.

        The pre-fix call site passed only ``account``; without ``amount``
        the canonical contract layer raises before the calldata is
        encoded, so the deposit tx never builds.
        """
        stub = self._bare_stub()
        with pytest.raises(AssertionError, match="build_deposit_for_data"):
            _drive(
                stub.get_contract_api_response(
                    contract_callable="build_deposit_for_data",
                    contract_address="0x" + "aa" * 20,
                    contract_id="dummy",
                    chain_id="gnosis",
                    account="0x" + "bb" * 20,
                )
            )
