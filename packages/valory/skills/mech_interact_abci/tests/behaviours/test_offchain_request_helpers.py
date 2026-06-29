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
    _PAYMENT_TYPE_HASH_NATIVE,
    _PAYMENT_TYPE_HASH_NVM_NATIVE,
    _PAYMENT_TYPE_HASH_NVM_TOKEN,
    _PAYMENT_TYPE_HASH_TOKEN_OLAS,
    _PAYMENT_TYPE_HASH_TOKEN_USDC,
    build_request_metadata,
    compute_cidv1_bytes,
    derive_request_id_bytes,
    parse_payment_challenge,
)
from packages.valory.skills.mech_interact_abci.behaviours.request import PaymentType
from packages.valory.skills.mech_interact_abci.states.base import (
    Event,
    OFFCHAIN_402_INSUFFICIENT,
    OFFCHAIN_TIMEOUT_ALL_MECHS,
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
        failover_retries: int = 2,
        mech_requests: Optional[List[Any]] = None,
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
            ),
            multisend_address="0x" + "ee" * 20,
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
        self._signature = signature
        # Record of calls (lets tests assert what the executor did).
        self.posted_urls: List[str] = []
        self.signed_request_ids: List[bytes] = []

    def get_contract_api_response(self, **_kwargs: Any) -> Any:
        if False:
            yield  # make this a generator
        return self._contract_api_responses.pop(0)

    def get_http_response(self, **kwargs: Any) -> Any:
        if False:
            yield
        self.posted_urls.append(kwargs.get("url", ""))
        return self._http_responses.pop(0)

    def get_signature(self, digest: bytes, is_deprecated_mode: bool = True) -> Any:
        if False:
            yield
        self.signed_request_ids.append(digest)
        return self._signature

    def _get_safe_tx_hash(self, **_kwargs: Any) -> Any:
        if False:
            yield
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


class TestFreshCycle:
    """End-to-end executor cycles with mocked reads + HTTP (review C8)."""

    def _native_reads(self) -> List[Any]:
        """Sequence of contract reads a single happy-path attempt consumes."""
        return [
            _state_resp({"chain_id": 100}),  # _resolve_chain_id_int
            _state_resp({"nonce": 7}),  # _read_on_chain_nonce
            _state_resp({"payment_type": _NATIVE_PAYMENT_TYPE}),
            _state_resp({"max_delivery_rate": 10**16}),
        ]

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
        # The signed digest is exactly the locally-derived request_id.
        assert len(stub.signed_request_ids) == 1
        assert stub.signed_request_ids[0].hex() == pending.request_id
        # Only one HTTP attempt was made on the happy path.
        assert len(stub.posted_urls) == 1

    def test_402_under_cap_returns_deposit_needed(self) -> None:
        """A structured 402 within the cap builds a Safe tx + pending blob.

        The mech advertises the canonical tracker as ``payTo``, so the
        destination validation passes and the executor proceeds to build
        the deposit tx.
        """
        mech_addr = "0x" + "aa" * 20
        canonical_tracker = "0x" + "11" * 20
        stub = _StubBehaviour(
            ranked_mechs=[_FakeMechInfo(mech_addr, "https://mech-aa.example")],
            contract_api_responses=[
                *self._native_reads(),
                # _validate_402_destination → marketplace.get_balance_tracker
                _state_resp({"balance_tracker": canonical_tracker}),
                # _build_native_deposit_tx → BalanceTracker.build_deposit_for_data
                _state_resp({"data": b"\x01\x02\x03"}),
            ],
            http_responses=[
                _make_http_response(
                    402,
                    _make_402_body(pay_to=canonical_tracker, required=500, current=0),
                ),
            ],
            auto_deposit_cap=10**18,
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._fresh_cycle())
        assert result.offchain_result == Event.OFFCHAIN_DEPOSIT_NEEDED.value
        assert result.tx_hash is not None
        assert result.pending_request_json is not None

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
                _state_resp({"chain_id": 100}),
                _state_resp({"nonce": 7}),
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
                _state_resp({"chain_id": 100}),
                _state_resp({"nonce": 7}),
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
            contract_api_responses=[],
            http_responses=[_make_http_response(200)],
            offchain_pending_request=self._pending_raw(),
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._retry_pending(pending))
        assert result.offchain_result == Event.OFFCHAIN_DONE.value
        # The signed digest is the cached request_id, not freshly derived.
        assert stub.signed_request_ids == [bytes.fromhex(pending.request_id)]
        assert result.pending_request_json is not None

    def test_retry_second_402_surfaces_insufficient(self) -> None:
        """A 402 on retry surfaces ``OFFCHAIN_402_INSUFFICIENT`` without looping."""
        pending = PendingRequest.from_dict(self._pending_raw())
        assert pending is not None
        stub = _StubBehaviour(
            ranked_mechs=[],
            contract_api_responses=[],
            http_responses=[_make_http_response(402, _make_402_body())],
            offchain_pending_request=self._pending_raw(),
        )
        executor = OffchainRequestExecutor(stub)  # type: ignore[arg-type]
        result = _drive(executor._retry_pending(pending))
        assert result.offchain_result == Event.OFFCHAIN_ALL_FAILED.value
        assert result.last_failure_reason == OFFCHAIN_402_INSUFFICIENT


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
            _state_resp({"chain_id": 100}),
            _state_resp({"nonce": 7}),
            _state_resp({"payment_type": _NATIVE_PAYMENT_TYPE}),
            _state_resp({"max_delivery_rate": 10**16}),
        ]

    def _token_pre_402_reads(self) -> List[Any]:
        return [
            _state_resp({"chain_id": 100}),
            _state_resp({"nonce": 7}),
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
                _state_resp({"chain_id": 100}),
                _state_resp({"nonce": 7}),
                *per_attempt_reads,  # attempt #1
                # validation → marketplace.get_balance_tracker
                _state_resp({"balance_tracker": self._CANONICAL_TRACKER}),
                *per_attempt_reads,  # attempt #2
                # validation on the second mech (also attacker payTo)
                _state_resp({"balance_tracker": self._CANONICAL_TRACKER}),
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
                _state_resp({"balance_tracker": self._CANONICAL_TRACKER}),
                _state_resp({"token": self._CANONICAL_TOKEN}),
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
                _state_resp({"balance_tracker": self._CANONICAL_TRACKER}),
                _state_resp({"token": self._CANONICAL_TOKEN}),
                # token deposit multisend reads: approve, depositFor, multisend
                _state_resp({"data": b"\xaa"}),
                _state_resp({"data": b"\xbb"}),
                _state_resp({"data": "0xcc"}),
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
                _state_resp({"balance_tracker": zero}),
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
