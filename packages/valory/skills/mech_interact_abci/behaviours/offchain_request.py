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

"""Off-chain request behaviour for the mech-interact skill.

When ``MechMarketplaceConfig.use_offchain`` is true, ``MechRequestBehaviour``
hands off to ``OffchainRequestExecutor.run`` instead of building a Safe tx
for the on-chain marketplace request. The executor:

* picks the next ranked mech and resolves its HTTP endpoint (from the
  per-mech ``http_url`` populated in :mod:`mech_info` or from the static
  ``offchain_url`` fallback);
* assembles the request metadata, computes the bare-file CIDv1 locally
  (byte-identical to ``mech-client``'s ``fetch_ipfs_hash``), reads the
  requester's on-chain nonce, derives the ``request_id`` per
  ``MechMarketplace.getRequestId``;
* signs the request_id via the framework's deprecated-mode signing path
  (raw ECDSA over the 32-byte digest, no EIP-191 prefix, so the mech's
  later on-chain settlement verifies the same signature the contract
  expects);
* POSTs to ``/send_signed_requests`` with the form-urlencoded body
  ``mech-client`` already speaks;
* on 200 records the pending response so the polling behaviour can fetch it;
* on a structured 402 builds a Safe-multisend that approves and deposits up
  to the operator-configured cap, returns the tx for settlement, and resumes
  with the same request_id after the deposit lands;
* on timeout / 503 walks the ranked list, capped by
  ``offchain_failover_max_retries``.

The wire shapes and signing path are tightly coupled to
``mech-client``'s offchain implementation and to
``MechMarketplace.sol`` — see the docstrings on individual helpers for the
exact mech-client / contract references each one is mirroring.
"""

import dataclasses
import enum
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Optional, Tuple
from urllib.parse import urlencode

from eth_abi import encode as abi_encode  # type: ignore[import-not-found]
from eth_utils import keccak as eth_keccak  # type: ignore[import-not-found]
from hexbytes import HexBytes  # type: ignore[import-not-found]

from packages.valory.contracts.balance_tracker_fixed_price_native.contract import (
    BalanceTrackerFixedPriceNative,
)
from packages.valory.contracts.balance_tracker_fixed_price_token.contract import (
    BalanceTrackerFixedPriceToken,
)
from packages.valory.contracts.erc20.contract import ERC20TokenContract
from packages.valory.contracts.mech_marketplace.contract import MechMarketplace
from packages.valory.contracts.mech_mm.contract import MechMM as MechMMContract
from packages.valory.contracts.multisend.contract import (
    MultiSendContract,
    MultiSendOperation,
)
from packages.valory.protocols.contract_api import ContractApiMessage
from packages.valory.skills.mech_interact_abci.states.base import (
    Event,
    MechInteractionResponse,
    MechMetadata,
    OFFCHAIN_402_INSUFFICIENT,
    OFFCHAIN_503_ALL_MECHS,
    OFFCHAIN_BAD_RESPONSE,
    OFFCHAIN_TIMEOUT_ALL_MECHS,
)

# ----------------------------------------------------------------------------
# Local CIDv1 (bare-file UnixFS+DAG-PB single block) — ported from
# ``mech-client/mech_client/infrastructure/ipfs/local_cid.py``. Mech-client and
# this module MUST produce byte-identical CIDs for the same input; the mech's
# task-execution skill recomputes the CID from the form-body ``ipfs_data`` and
# uses it for the on-chain commitment at settlement. Any drift here breaks
# settlement, so the algorithm is intentionally a literal port.
# ----------------------------------------------------------------------------

_MAX_BLOCK_BYTES = 256 * 1024
_CIDV1_VERSION = 0x01
_DAG_PB_CODEC = 0x70
_SHA256_MULTIHASH_CODE = 0x12
_SHA256_DIGEST_LEN = 0x20
_UNIXFS_TYPE_FILE = 2


def _varint(value: int) -> bytes:
    """Encode an unsigned integer as a protobuf varint."""
    if value < 0:
        raise ValueError("varint requires non-negative value")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _length_delimited(field_number: int, payload: bytes) -> bytes:
    """Encode a protobuf length-delimited field (wire type 2)."""
    tag = (field_number << 3) | 2
    return _varint(tag) + _varint(len(payload)) + payload


def _varint_field(field_number: int, value: int) -> bytes:
    """Encode a protobuf varint field (wire type 0)."""
    tag = (field_number << 3) | 0
    return _varint(tag) + _varint(value)


def _encode_unixfs_file(content: bytes) -> bytes:
    """Encode a UnixFS ``Type=File`` Data message for the given content."""
    out = bytearray()
    out += _varint_field(1, _UNIXFS_TYPE_FILE)
    if content:
        out += _length_delimited(2, content)
    out += _varint_field(3, len(content))
    return bytes(out)


def _encode_dag_pb_node(data: bytes) -> bytes:
    """Wrap ``data`` in a DAG-PB ``PBNode`` with no links."""
    return _length_delimited(1, data)


def compute_cidv1_bytes(content: bytes) -> bytes:
    """Raw CIDv1 bytes for ``content``.

    Byte-identical to ``mech-client``'s ``compute_cidv1_bytes`` and to the
    mech's ``task_execution/utils/local_cid.py``. Single-block only; raises
    ``ValueError`` above 256 KiB so a future oversized prompt fails loudly
    instead of producing a CID the mech could not reproduce.
    """
    if len(content) > _MAX_BLOCK_BYTES:
        raise ValueError(
            f"content size {len(content)} exceeds single-block bound "
            f"{_MAX_BLOCK_BYTES}; chunked DAG encoding is not supported"
        )
    unixfs_bytes = _encode_unixfs_file(content)
    dag_pb_bytes = _encode_dag_pb_node(unixfs_bytes)
    digest = hashlib.sha256(dag_pb_bytes).digest()
    multihash = bytes([_SHA256_MULTIHASH_CODE, _SHA256_DIGEST_LEN]) + digest
    return bytes([_CIDV1_VERSION, _DAG_PB_CODEC]) + multihash


def build_request_metadata(
    prompt: str,
    tool: str,
    extra_attributes: Optional[Dict[str, Any]] = None,
    nonce_str: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Build the offchain request metadata and its on-chain hash.

    Mirrors ``mech-client``'s ``fetch_ipfs_hash`` shape so the mech server
    recomputes the same CID on receipt. Returns ``(truncated_hash,
    v1_file_hash_hex, ipfs_data)`` where ``truncated_hash`` is the ``0x``
    + 62-hex form the on-chain commitment uses and ``ipfs_data`` is the
    JSON string carried as the ``ipfs_data`` form field.
    """
    metadata: Dict[str, Any] = {
        "prompt": prompt,
        "tool": tool,
        "nonce": nonce_str if nonce_str is not None else str(uuid.uuid4()),
    }
    if extra_attributes:
        metadata.update(extra_attributes)
    ipfs_data = json.dumps(metadata)
    cid_bytes = compute_cidv1_bytes(ipfs_data.encode("utf-8"))
    v1_file_hash_hex = "f" + cid_bytes.hex()
    truncated_hash = "0x" + v1_file_hash_hex[9:]
    return truncated_hash, v1_file_hash_hex, ipfs_data


# ----------------------------------------------------------------------------
# request_id derivation — local mirror of ``MechMarketplace.getRequestId``
# (``contracts/MechMarketplace.sol:883-908``). Computing client-side avoids an
# RPC hop per attempt; the same hash is what the contract validates at
# settlement so byte-for-byte parity matters.
# ----------------------------------------------------------------------------

_MARKETPLACE_NAME = "MechMarketplace"
_MARKETPLACE_VERSION = "1.1.0"
_DOMAIN_TYPEHASH = eth_keccak(
    text=(
        "EIP712Domain(string name,string version,uint256 chainId,"
        "address verifyingContract)"
    )
)


def _compute_domain_separator(chain_id: int, marketplace_address: str) -> bytes:
    """Reproduce ``MechMarketplace._computeDomainSeparator`` in Python.

    The contract hashes the version through ``abi.encode`` not as raw
    bytes (see ``MechMarketplace.sol:160``), so the Python side must
    encode-then-hash the version string too — a raw ``keccak256(b"1.1.0")``
    would not match.
    """
    name_hash = eth_keccak(text=_MARKETPLACE_NAME)
    version_hash = eth_keccak(abi_encode(["string"], [_MARKETPLACE_VERSION]))
    return eth_keccak(
        abi_encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                _DOMAIN_TYPEHASH,
                name_hash,
                version_hash,
                chain_id,
                marketplace_address,
            ],
        )
    )


def derive_request_id_bytes(  # noqa: D417
    marketplace_address: str,
    mech_address: str,
    requester: str,
    data: bytes,
    delivery_rate: int,
    payment_type: bytes,
    nonce: int,
    chain_id: int,
) -> bytes:
    """Local mirror of ``MechMarketplace.getRequestId``.

    :param marketplace_address: ``address(this)`` on the settlement chain.
    :param mech_address: The mech the request targets.
    :param requester: The Safe (or EOA) that owns the prepaid balance.
    :param data: The raw bytes of the request metadata (the same bytes the
        ``ipfs_data`` form field carries; the contract takes ``keccak256(data)``).
    :param delivery_rate: Per-request charge (matches the on-chain
        ``deliveryRate`` argument).
    :param payment_type: 32-byte ``paymentType`` constant for the mech's
        payment model.
    :param nonce: The requester's current on-chain ``mapNonces`` value.
    :param chain_id: The settlement chain id (for the EIP-712 domain).
    :return: The 32-byte ``request_id`` the contract will compute at
        ``_deliverMarketplaceWithSignatures`` time.
    """
    if len(payment_type) != 32:
        raise ValueError("payment_type must be 32 bytes")
    domain_separator = _compute_domain_separator(chain_id, marketplace_address)
    inner_hash = eth_keccak(
        abi_encode(
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
                marketplace_address,
                mech_address,
                requester,
                eth_keccak(data),
                delivery_rate,
                payment_type,
                nonce,
            ],
        )
    )
    return eth_keccak(b"\x19\x01" + domain_separator + inner_hash)


# ----------------------------------------------------------------------------
# Structured 402 challenge — parses the body the mech server returns from
# ``handlers.py::_build_402_challenge`` (mech repo). Mirrors mech-client's
# ``PaymentChallenge`` so the cap logic and the deposit builder downstream
# read the same field names.
# ----------------------------------------------------------------------------


def _try_int(value: Any) -> Optional[int]:
    """Coerce a 402-body field to ``int`` or return ``None`` if malformed.

    Returning ``None`` (instead of silently defaulting) lets the caller
    distinguish "field absent / non-numeric" from "field present with a
    valid 0", so a malformed ``required`` doesn't get treated as
    ``shortfall=0`` and a malformed ``currentBalance`` doesn't get treated
    as a real zero balance. ``bool`` is excluded because ``json.loads``
    can emit ``True`` / ``False`` for these fields if a mech misbehaves
    and we'd rather route them through ``BAD_RESPONSE`` than coerce.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 10)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class PaymentChallenge:
    """Parsed structured 402 body (``payTo``, ``currentBalance``, ``required``).

    Fields mirror mech-client's parsed shape (``payTo`` lower-cased and
    used verbatim against the ``BalanceTracker``; ``asset`` is the ERC20
    address for the token model and the zero address for native). The
    derived ``shortfall`` is computed eagerly so the cap check is a single
    field read.
    """

    pay_to: str
    asset: str
    chain_id: int
    current_balance: int
    required: int
    error: str

    @property
    def shortfall(self) -> int:
        """Amount that has to land on the BalanceTracker for the retry to pass."""
        return max(0, self.required - self.current_balance)


def parse_payment_challenge(body: bytes) -> Optional[PaymentChallenge]:
    """Parse a structured 402 body into a :class:`PaymentChallenge`.

    Returns ``None`` when the body is missing, unparseable, or shaped
    differently from what the mech ``handlers.py::_build_402_challenge``
    emits, **including** non-numeric ``required`` / ``currentBalance``
    (which previously silently coerced to ``0`` and mis-routed the
    deposit decision — see review C5). A ``None`` from a structured 402
    is mapped by the caller to :data:`OffchainAttemptOutcome.BAD_RESPONSE`
    so the failover loop can move on to the next ranked mech rather
    than spend the deposit on a misreported shortfall or short-circuit
    to ``OVER_CAP``.
    """
    if not body:
        return None
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    pay_to = payload.get("payTo")
    if not isinstance(pay_to, str) or not pay_to:
        return None
    asset = payload.get("asset") or "0x" + "0" * 40
    if not isinstance(asset, str):
        return None
    current_balance = _try_int(payload.get("currentBalance"))
    required = _try_int(payload.get("required"))
    if current_balance is None or required is None:
        return None
    # ``chainId`` is reported but not consulted by the deposit builder
    # (the Safe tx executes on the configured ``mech_chain_id``); a
    # missing/non-numeric value is harmless, so default to 0 here.
    chain_id = _try_int(payload.get("chainId"))
    return PaymentChallenge(
        pay_to=pay_to,
        asset=asset,
        chain_id=chain_id if chain_id is not None else 0,
        current_balance=current_balance,
        required=required,
        error=str(payload.get("error", "")),
    )


# ----------------------------------------------------------------------------
# Attempt outcome enum + per-attempt result. Used so the failover loop can
# branch without re-parsing HTTP responses and so the unit tests can assert
# on each terminal state crisply.
# ----------------------------------------------------------------------------


_ZERO_ADDRESS = "0x" + "0" * 40
_NATIVE_ASSET = _ZERO_ADDRESS


class OffchainAttemptOutcome(enum.Enum):
    """Terminal state of a single ``/send_signed_requests`` POST."""

    DONE = "done"
    """The mech accepted the request and returned 200 with a ``Payment-Receipt``."""

    DEPOSIT_NEEDED = "deposit_needed"
    """The mech rejected with a structured 402; deposit-and-retry path triggered."""

    OVER_CAP = "over_cap"
    """The 402 shortfall exceeded ``auto_deposit_cap_per_cycle``; surface to consumer."""

    TIMEOUT = "timeout"
    """The HTTP call raised (framework error) or returned ``None``.

    Counts toward the failover budget. The framework helper used here
    (``get_http_response``) does not expose a per-call timeout argument,
    so this is the framework's own raise/None signal rather than a
    deadline policed locally — see the ``offchain_http_timeout_seconds``
    history in the module-level review notes.
    """

    SERVER_BUSY = "server_busy"
    """The mech returned 503 (counts toward failover budget)."""

    BAD_RESPONSE = "bad_response"
    """The mech returned a non-handled status, or a 402 with a malformed body.

    Counts toward the failover budget but surfaces as
    :data:`OFFCHAIN_BAD_RESPONSE` rather than ``OFFCHAIN_TIMEOUT_ALL_MECHS``
    so an operator can distinguish a misbehaving mech from a network stall.
    """


@dataclass(frozen=True)
class OffchainAttemptResult:
    """Result of a single :meth:`OffchainRequestExecutor._attempt_single_mech` call."""

    outcome: OffchainAttemptOutcome
    mech_address: str
    mech_url: str
    challenge: Optional[PaymentChallenge] = None
    status_code: Optional[int] = None


# ----------------------------------------------------------------------------
# In-flight request state — persisted on ``synced_data.offchain_pending_request``
# so the deposit-then-retry round-trip preserves the original
# ``(mech, request_id, nonce, ipfs_data)`` tuple. Without this the retry
# POST would derive a different ``request_id`` (mech-address changes the
# hash) and waste the deposit.
#
# *Why no signature*: ``MechRequestRound`` is a
# ``CollectSameUntilThresholdRound`` whose consensus is over the full payload
# tuple, including ``offchain_pending_request``. A per-agent ECDSA signature
# would diverge across agents (each has its own key) and the round would
# loop ``NO_MAJORITY`` forever on any n>1 service. Instead, every field
# kept here is deterministic given the on-chain reads; each agent re-signs
# locally on the POST and (after deposit settles) on retry, so the contract
# still verifies the same recovered signer at settlement time.
# ----------------------------------------------------------------------------


_REQUEST_ID_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PendingRequest:
    """Serialized state of an in-flight offchain request awaiting deposit retry.

    All fields must be deterministic across agents — the signature is
    intentionally **not** carried here (see module-level note). ``__post_init__``
    enforces the format invariants the retry path (`_retry_pending`) relies on
    so a malformed value fails fast at construction instead of crashing
    inside ``bytes.fromhex``.
    """

    request_id: str  # 64-char lower-hex, no 0x prefix
    nonce: int
    mech_address: str  # lowercase 0x-prefixed
    mech_url: str
    sender: str
    delivery_rate: int
    ipfs_hash: str
    ipfs_data: str

    def __post_init__(self) -> None:
        """Reject malformed values up front; raise rather than silently drift."""
        if not isinstance(self.request_id, str) or not _REQUEST_ID_HEX_RE.match(
            self.request_id
        ):
            raise ValueError(
                "PendingRequest.request_id must be 64-char lower-hex without "
                f"0x prefix; got {self.request_id!r}"
            )
        if not isinstance(self.mech_address, str) or self.mech_address != (
            self.mech_address.lower()
        ):
            raise ValueError(
                "PendingRequest.mech_address must be lower-cased; "
                f"got {self.mech_address!r}"
            )
        if self.nonce < 0:
            raise ValueError(
                f"PendingRequest.nonce must be non-negative; got {self.nonce}"
            )
        if self.delivery_rate < 0:
            raise ValueError(
                "PendingRequest.delivery_rate must be non-negative; "
                f"got {self.delivery_rate}"
            )

    def to_json(self) -> str:
        """Serialize to the form persisted on synchronized_data."""
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> Optional["PendingRequest"]:
        """Round-trip from the dict the SynchronizedData accessor returns.

        Normalizes ``request_id`` (strip ``0x`` prefix, lowercase) and
        ``mech_address`` (lowercase) so a re-entry from a payload written
        by an older revision still validates cleanly under the
        ``__post_init__`` invariants.
        """
        try:
            request_id = str(raw["request_id"]).lower()
            if request_id.startswith("0x"):
                request_id = request_id[2:]
            return cls(
                request_id=request_id,
                nonce=int(raw["nonce"]),
                mech_address=str(raw["mech_address"]).lower(),
                mech_url=str(raw["mech_url"]),
                sender=str(raw["sender"]),
                delivery_rate=int(raw["delivery_rate"]),
                ipfs_hash=str(raw["ipfs_hash"]),
                ipfs_data=str(raw["ipfs_data"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


# ----------------------------------------------------------------------------
# OffchainRequestExecutor — main coordinator. Owns one offchain request cycle:
# discovers the mech URL, picks the next mech in the ranked failover order,
# signs and POSTs, branches on 200/402/timeout/503, builds the deposit
# multisend on a chargeable 402, surfaces the result via a payload-shaped
# return value that ``MechRequestBehaviour`` lifts straight onto the round.
# ----------------------------------------------------------------------------

# Lower-cased payment-type labels used to dispatch the deposit builder. The
# hashes themselves mirror the ``PaymentType`` enum in
# :mod:`mech_interact_abci.behaviours.request`; duplicating them here keeps
# this module importable without pulling that file in (which would create a
# circular import via ``MechRequestBehaviour``).
_PAYMENT_NATIVE = "native"
_PAYMENT_TOKEN = "token"  # nosec B105 - label string, not a credential
_PAYMENT_NVM_NATIVE = "nvm_native"
_PAYMENT_NVM_TOKEN = "nvm_token"  # nosec B105 - label string, not a credential

_PAYMENT_TYPE_HASH_NATIVE = (
    "0xba699a34be8fe0e7725e93dcbce1701b0211a8ca61330aaeb8a05bf2ec7abed1"
)
_PAYMENT_TYPE_HASH_TOKEN_OLAS = (  # nosec B105 - public on-chain payment-type selector hash
    "0x3679d66ef546e66ce9057c4a052f317b135bc8e8c509638f7966edfd4fcf45e9"
)
_PAYMENT_TYPE_HASH_TOKEN_USDC = (  # nosec B105 - public on-chain payment-type selector hash
    "0x6406bb5f31a732f898e1ce9fdd988a80a808d36ab5d9a4a4805a8be8d197d5e3"
)
_PAYMENT_TYPE_HASHES_TOKEN = frozenset(
    {_PAYMENT_TYPE_HASH_TOKEN_OLAS, _PAYMENT_TYPE_HASH_TOKEN_USDC}
)
_PAYMENT_TYPE_HASH_NVM_NATIVE = (
    "0x803dd08fe79d91027fc9024e254a0942372b92f3ccabc1bd19f4a5c2b251c316"
)
_PAYMENT_TYPE_HASH_NVM_TOKEN = (  # nosec B105 - public on-chain payment-type selector hash
    "0x0d6fd99afa9c4c580fab5e341922c2a5c4b61d880da60506193d7bf88944dd14"
)


@dataclass(frozen=True)
class OffchainCycleResult:
    """Final outcome of one offchain request cycle, lifted onto the payload.

    ``offchain_result`` is always one of
    ``Event.OFFCHAIN_DONE.value`` / ``Event.OFFCHAIN_DEPOSIT_NEEDED.value`` /
    ``Event.OFFCHAIN_ALL_FAILED.value`` — keyed off the enum (not free
    strings) so a rename of the event values fails type-checking and the
    round dispatch stays in lockstep.
    """

    offchain_result: str
    mech_requests_json: Optional[str] = None
    mech_responses_json: Optional[str] = None
    pending_request_json: Optional[str] = None
    last_failure_reason: Optional[str] = None
    # Set only on the deposit path. The Safe-multisend hash that the
    # consumer's transaction_settlement skill submits before re-entering
    # ``MechRequestRound`` to retry the POST.
    tx_submitter: Optional[str] = None
    tx_hash: Optional[str] = None


_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")


class OffchainRequestExecutor:
    """Coordinator for one ``MechRequestRound`` offchain cycle.

    Holds a reference back to the parent behaviour so it can yield through
    the framework's HTTP / signing / contract API helpers. Stateless across
    cycles — persistent state lives on ``SynchronizedData`` so it survives
    the deposit-then-retry round-trip.
    """

    def __init__(self, behaviour: "Any") -> None:
        """Wire the executor to the calling behaviour for context access."""
        self._b = behaviour

    # ---------- convenience accessors --------------------------------------

    @property
    def _config(self) -> Any:
        return self._b.params.mech_marketplace_config

    @property
    def _synced(self) -> Any:
        return self._b.synchronized_data

    @property
    def _logger(self) -> Any:
        return self._b.context.logger

    # ---------- entry point -------------------------------------------------

    def run(self) -> Generator[None, None, OffchainCycleResult]:
        """Execute one offchain request cycle.

        Two entry shapes:

        * fresh cycle — no pending request on synced data, build new
          metadata, walk the failover ranked list;
        * resumed cycle — pending request present (deposit just settled),
          re-POST the same request_id at the same mech without re-deriving.
        """
        pending = self._load_pending_request()
        if pending is not None:
            self._logger.info(
                f"Resuming offchain request {pending.request_id[:10]}… "
                f"after deposit settlement; re-POSTing to {pending.mech_address}"
            )
            outcome = yield from self._retry_pending(pending)
            return outcome

        outcome = yield from self._fresh_cycle()
        return outcome

    # ---------- fresh cycle -------------------------------------------------

    def _fresh_cycle(self) -> Generator[None, None, OffchainCycleResult]:
        """Build a brand-new request and walk the ranked mech list."""
        # Trader and other current consumers populate ``mech_requests`` with
        # exactly one entry per cycle; the offchain wire shape supports one
        # request per ``request_id`` so we serialise on the first entry and
        # leave multi-request batching for a follow-up.
        requests = self._synced.mech_requests
        if not requests:
            self._logger.info("Offchain branch reached with no mech_requests; skipping")
            return OffchainCycleResult(offchain_result=Event.OFFCHAIN_DONE.value)

        request_meta: MechMetadata = requests[0]
        ipfs_hash, _, ipfs_data = build_request_metadata(
            prompt=request_meta.prompt,
            tool=request_meta.tool,
            extra_attributes=request_meta.extra_attributes,
            nonce_str=request_meta.nonce,
        )

        chain_id_int = yield from self._resolve_chain_id_int()
        if chain_id_int is None:
            return OffchainCycleResult(
                offchain_result=Event.OFFCHAIN_ALL_FAILED.value,
                last_failure_reason=OFFCHAIN_TIMEOUT_ALL_MECHS,
            )

        on_chain_nonce = yield from self._read_on_chain_nonce()
        if on_chain_nonce is None:
            return OffchainCycleResult(
                offchain_result=Event.OFFCHAIN_ALL_FAILED.value,
                last_failure_reason=OFFCHAIN_TIMEOUT_ALL_MECHS,
            )

        attempted: List[str] = []
        last_failure: Optional[str] = None
        last_outcome: Optional[OffchainAttemptOutcome] = None

        for _ in range(self._config.offchain_failover_max_retries + 1):
            mech_address, mech_url = self._pick_next_mech(attempted)
            if mech_address is None or mech_url is None:
                last_failure = self._failure_label_for(last_outcome)
                break

            attempted.append(mech_address.lower())

            payment_type_bytes = yield from self._resolve_payment_type(mech_address)
            if payment_type_bytes is None:
                last_failure = OFFCHAIN_TIMEOUT_ALL_MECHS
                continue

            delivery_rate = yield from self._resolve_delivery_rate(mech_address)
            if delivery_rate is None:
                last_failure = OFFCHAIN_TIMEOUT_ALL_MECHS
                continue

            request_id_bytes = derive_request_id_bytes(
                marketplace_address=self._config.mech_marketplace_address,
                mech_address=mech_address,
                requester=self._safe_address(),
                data=ipfs_data.encode("utf-8"),
                delivery_rate=delivery_rate,
                payment_type=payment_type_bytes,
                nonce=on_chain_nonce,
                chain_id=chain_id_int,
            )
            signature_hex = yield from self._sign_request_id(request_id_bytes)
            if signature_hex is None:
                last_failure = OFFCHAIN_TIMEOUT_ALL_MECHS
                continue

            attempt = yield from self._post_signed_request(
                mech_url=mech_url,
                mech_address=mech_address,
                ipfs_hash=ipfs_hash,
                ipfs_data=ipfs_data,
                request_id_bytes=request_id_bytes,
                signature_hex=signature_hex,
                nonce=on_chain_nonce,
                delivery_rate=delivery_rate,
                sender=self._safe_address(),
            )
            last_outcome = attempt.outcome

            pending = self._build_pending(
                request_id_bytes=request_id_bytes,
                nonce=on_chain_nonce,
                mech_address=mech_address,
                mech_url=mech_url,
                delivery_rate=delivery_rate,
                ipfs_hash=ipfs_hash,
                ipfs_data=ipfs_data,
            )

            if attempt.outcome is OffchainAttemptOutcome.DONE:
                return OffchainCycleResult(
                    offchain_result=Event.OFFCHAIN_DONE.value,
                    mech_requests_json=self._serialise_mech_requests([request_meta]),
                    mech_responses_json=self._serialise_pending_response(
                        request_id_bytes, on_chain_nonce
                    ),
                    pending_request_json=pending.to_json(),
                )

            if attempt.outcome is OffchainAttemptOutcome.DEPOSIT_NEEDED:
                # Defence against a malicious or compromised mech directing
                # the auto-deposit to an attacker-chosen address: cross-check
                # ``challenge.pay_to`` (and ``asset`` on the token path)
                # against the canonical BalanceTracker the marketplace has
                # registered for the mech's ``paymentType``. A mismatch is
                # treated as a misbehaving mech (BAD_RESPONSE) and failover
                # moves on rather than landing a redirected deposit.
                destination_ok = yield from self._validate_402_destination(
                    challenge=attempt.challenge,  # type: ignore[arg-type]
                    payment_type=payment_type_bytes,
                )
                if not destination_ok:
                    last_outcome = OffchainAttemptOutcome.BAD_RESPONSE
                    last_failure = OFFCHAIN_BAD_RESPONSE
                    continue
                tx_hex = yield from self._build_deposit_tx(
                    challenge=attempt.challenge,  # type: ignore[arg-type]
                    payment_type=payment_type_bytes,
                )
                if tx_hex is None:
                    # Either NVM (not on-chain auto-resolvable) or the
                    # deposit builder hit a contract-read failure; both
                    # are non-recoverable on this cycle, so surface to
                    # the consumer instead of burning failover budget.
                    return OffchainCycleResult(
                        offchain_result=Event.OFFCHAIN_ALL_FAILED.value,
                        last_failure_reason=OFFCHAIN_402_INSUFFICIENT,
                    )
                # Persist the structured request metadata on this branch
                # too: ``mech_requests`` is in ``MechRequestRound.selection_key``,
                # so omitting it causes the round to commit ``null``. The
                # accessor collapses ``null`` to ``[]``, and the retry path
                # (which reads ``self._synced.mech_requests`` to compose the
                # final response payload) would then ship the original
                # ``prompt``/``tool``/``nonce`` as empty.
                return OffchainCycleResult(
                    offchain_result=Event.OFFCHAIN_DEPOSIT_NEEDED.value,
                    tx_submitter="MechRequestRound",
                    tx_hash=tx_hex,
                    mech_requests_json=self._serialise_mech_requests([request_meta]),
                    pending_request_json=pending.to_json(),
                )

            if attempt.outcome is OffchainAttemptOutcome.OVER_CAP:
                # The 402 shortfall exceeds the operator-set cap, so the
                # on-chain deposit is refused on this cycle. The other
                # ranked mechs would charge against the same Safe balance,
                # so failover can't change the outcome — surface
                # ``OFFCHAIN_402_INSUFFICIENT`` to the consumer instead of
                # burning the rest of the failover budget on certain failures.
                return OffchainCycleResult(
                    offchain_result=Event.OFFCHAIN_ALL_FAILED.value,
                    last_failure_reason=OFFCHAIN_402_INSUFFICIENT,
                )

            # TIMEOUT / SERVER_BUSY / BAD_RESPONSE — try the next mech.
            last_failure = self._failure_label_for(attempt.outcome)
            continue

        return OffchainCycleResult(
            offchain_result=Event.OFFCHAIN_ALL_FAILED.value,
            last_failure_reason=last_failure or OFFCHAIN_TIMEOUT_ALL_MECHS,
        )

    # ---------- resume after deposit ---------------------------------------

    def _retry_pending(
        self, pending: PendingRequest
    ) -> Generator[None, None, OffchainCycleResult]:
        """Re-POST a pending request once the deposit has settled.

        Reuses the cached ``request_id``, nonce, mech, and ipfs payload
        verbatim — the on-chain nonce has not advanced (the deposit does
        not consume one) and the target mech has not changed, so the
        contract still verifies the same ``request_id`` hash. The
        signature is re-derived locally per agent (it is not carried
        through synchronized data so the round's consensus tuple stays
        deterministic). Only a single retry is attempted; a second 402
        indicates a misconfigured cap or a stuck balance and is surfaced
        rather than looping into another deposit.
        """
        request_id_bytes = bytes.fromhex(pending.request_id)
        signature_hex = yield from self._sign_request_id(request_id_bytes)
        if signature_hex is None:
            return OffchainCycleResult(
                offchain_result=Event.OFFCHAIN_ALL_FAILED.value,
                last_failure_reason=OFFCHAIN_TIMEOUT_ALL_MECHS,
            )

        attempt = yield from self._post_signed_request(
            mech_url=pending.mech_url,
            mech_address=pending.mech_address,
            ipfs_hash=pending.ipfs_hash,
            ipfs_data=pending.ipfs_data,
            request_id_bytes=request_id_bytes,
            signature_hex=signature_hex,
            nonce=pending.nonce,
            delivery_rate=pending.delivery_rate,
            sender=pending.sender,
        )

        if attempt.outcome is OffchainAttemptOutcome.DONE:
            return OffchainCycleResult(
                offchain_result=Event.OFFCHAIN_DONE.value,
                mech_requests_json=self._serialise_mech_requests(
                    self._synced.mech_requests
                ),
                mech_responses_json=self._serialise_pending_response(
                    request_id_bytes, pending.nonce
                ),
                pending_request_json=pending.to_json(),
            )
        return OffchainCycleResult(
            offchain_result=Event.OFFCHAIN_ALL_FAILED.value,
            last_failure_reason=OFFCHAIN_402_INSUFFICIENT,
        )

    # ---------- attempts (HTTP wire) ----------------------------------------

    def _build_pending(
        self,
        request_id_bytes: bytes,
        nonce: int,
        mech_address: str,
        mech_url: str,
        delivery_rate: int,
        ipfs_hash: str,
        ipfs_data: str,
    ) -> PendingRequest:
        """Assemble the ``PendingRequest`` recorded on consensus payloads.

        Centralized so the ``DONE`` and ``DEPOSIT_NEEDED`` branches can't
        drift in field shape (the original revision had two near-identical
        constructor calls). All fields are deterministic given the
        on-chain reads; see :class:`PendingRequest` for why the per-agent
        signature is excluded.
        """
        return PendingRequest(
            request_id=request_id_bytes.hex(),
            nonce=nonce,
            mech_address=mech_address.lower(),
            mech_url=mech_url,
            sender=self._safe_address(),
            delivery_rate=delivery_rate,
            ipfs_hash=ipfs_hash,
            ipfs_data=ipfs_data,
        )

    def _post_signed_request(
        self,
        mech_url: str,
        mech_address: str,
        ipfs_hash: str,
        ipfs_data: str,
        request_id_bytes: bytes,
        signature_hex: str,
        nonce: int,
        delivery_rate: int,
        sender: str,
    ) -> Generator[None, None, OffchainAttemptResult]:
        """POST to ``/send_signed_requests`` and classify the response."""
        body = self._build_form_body(
            sender=sender,
            signature=signature_hex,
            ipfs_hash=ipfs_hash,
            request_id=int.from_bytes(request_id_bytes, "big"),
            delivery_rate=delivery_rate,
            nonce=nonce,
            ipfs_data=ipfs_data,
        )
        url = mech_url.rstrip("/") + "/send_signed_requests"
        try:
            response = yield from self._b.get_http_response(
                method="POST",
                url=url,
                content=body,
                headers={"Content-Type": "application/json"},
            )
        except Exception as exc:  # pragma: no cover - framework boundary
            self._logger.warning(
                f"Offchain POST to {mech_url} raised {exc.__class__.__name__}: {exc}"
            )
            return OffchainAttemptResult(
                outcome=OffchainAttemptOutcome.TIMEOUT,
                mech_address=mech_address,
                mech_url=mech_url,
            )

        if response is None:
            return OffchainAttemptResult(
                outcome=OffchainAttemptOutcome.TIMEOUT,
                mech_address=mech_address,
                mech_url=mech_url,
            )

        status = getattr(response, "status_code", None)
        body_bytes = getattr(response, "body", b"") or b""

        if status == 200:
            return OffchainAttemptResult(
                outcome=OffchainAttemptOutcome.DONE,
                mech_address=mech_address,
                mech_url=mech_url,
                status_code=status,
            )
        if status == 402:
            challenge = parse_payment_challenge(body_bytes)
            if challenge is None:
                return OffchainAttemptResult(
                    outcome=OffchainAttemptOutcome.BAD_RESPONSE,
                    mech_address=mech_address,
                    mech_url=mech_url,
                    status_code=status,
                )
            cap = self._config.auto_deposit_cap_per_cycle or 0
            if challenge.shortfall > cap:
                return OffchainAttemptResult(
                    outcome=OffchainAttemptOutcome.OVER_CAP,
                    mech_address=mech_address,
                    mech_url=mech_url,
                    challenge=challenge,
                    status_code=status,
                )
            return OffchainAttemptResult(
                outcome=OffchainAttemptOutcome.DEPOSIT_NEEDED,
                mech_address=mech_address,
                mech_url=mech_url,
                challenge=challenge,
                status_code=status,
            )
        if status == 503:
            return OffchainAttemptResult(
                outcome=OffchainAttemptOutcome.SERVER_BUSY,
                mech_address=mech_address,
                mech_url=mech_url,
                status_code=status,
            )
        return OffchainAttemptResult(
            outcome=OffchainAttemptOutcome.BAD_RESPONSE,
            mech_address=mech_address,
            mech_url=mech_url,
            status_code=status,
        )

    @staticmethod
    def _build_form_body(
        sender: str,
        signature: str,
        ipfs_hash: str,
        request_id: int,
        delivery_rate: int,
        nonce: int,
        ipfs_data: str,
    ) -> bytes:
        """Form-urlencode the body the mech ``handlers.py`` parses.

        The mech server uses ``parse_qs`` on the body (it accepts the
        ``Content-Type: application/json`` header for symmetry with mech-client
        but never JSON-decodes the body), so we encode each scalar field
        as ``key=value`` joined with ``&`` and URL-encode special characters
        in the JSON payload. Matches mech-client's
        ``requests.post(data=dict(...))`` behaviour byte-for-byte.
        """
        return urlencode(
            {
                "sender": sender,
                "signature": signature,
                "ipfs_hash": ipfs_hash,
                "request_id": request_id,
                "delivery_rate": delivery_rate,
                "nonce": nonce,
                "ipfs_data": ipfs_data,
            }
        ).encode("utf-8")

    # ---------- signing -----------------------------------------------------

    def _sign_request_id(
        self, request_id_bytes: bytes
    ) -> Generator[None, None, Optional[str]]:
        r"""Sign the 32-byte request_id with the raw-ECDSA path.

        ``get_signature(..., is_deprecated_mode=True)`` skips the
        ``\x19Ethereum Signed Message:\n32`` prefix and signs the raw 32
        bytes directly. Mech-client uses the same shape via
        ``crypto.sign_message(..., is_deprecated_mode=True)``; the contract's
        ``_verifySignedHash`` recovers the same signer at settlement.
        """
        try:
            signature = yield from self._b.get_signature(
                request_id_bytes,
                is_deprecated_mode=True,
            )
        except Exception as exc:  # pragma: no cover - framework boundary
            self._logger.error(f"Offchain request_id signing failed: {exc}")
            return None
        if not isinstance(signature, str):
            return None
        if not signature.startswith("0x"):
            signature = "0x" + signature
        return signature

    # ---------- on-chain reads ---------------------------------------------

    def _read_on_chain_nonce(self) -> Generator[None, None, Optional[int]]:
        """Read ``MechMarketplace.mapNonces(safe)`` for the current Safe.

        Mirrors mech-client's pre-sign nonce read at
        ``marketplace_service.py:364``. The contract advances this slot at
        settlement, so the value pinned here is the one the request_id
        derivation must match.
        """
        response = yield from self._b.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=self._config.mech_marketplace_address,
            contract_id=str(MechMarketplace.contract_id),
            contract_callable="get_nonce",
            sender=self._safe_address(),
        )
        if response.performative != ContractApiMessage.Performative.STATE:
            self._logger.warning(
                f"mapNonces read failed: performative={response.performative}"
            )
            return None
        nonce = response.state.body.get("nonce")
        if nonce is None:
            return None
        try:
            return int(nonce)
        except (TypeError, ValueError):
            return None

    def _resolve_chain_id_int(self) -> Generator[None, None, Optional[int]]:
        """Return the numeric chain id used by the EIP-712 domain.

        Reads ``MechMarketplace.chainId`` (the immutable baked at
        construction time) so the locally-derived ``request_id`` binds to
        the same chain the contract validates against. Going through the
        contract instead of trusting service config keeps the derivation
        self-consistent across forks and replay scenarios.
        """
        response = yield from self._b.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=self._config.mech_marketplace_address,
            contract_id=str(MechMarketplace.contract_id),
            contract_callable="get_chain_id",
        )
        if response.performative != ContractApiMessage.Performative.STATE:
            self._logger.warning(
                f"MechMarketplace.chainId read failed: "
                f"performative={response.performative}"
            )
            return None
        try:
            return int(response.state.body.get("chain_id"))
        except (TypeError, ValueError) as exc:
            self._logger.warning(
                f"MechMarketplace.chainId returned a non-numeric value: {exc}"
            )
            return None

    def _resolve_payment_type(
        self, mech_address: str
    ) -> Generator[None, None, Optional[bytes]]:
        """Read the mech's ``paymentType`` (32-byte selector) for the request_id."""
        response = yield from self._b.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=mech_address,
            contract_id=str(MechMMContract.contract_id),
            contract_callable="get_payment_type",
        )
        if response.performative != ContractApiMessage.Performative.STATE:
            self._logger.warning(
                f"Mech {mech_address} paymentType read failed: "
                f"performative={response.performative}"
            )
            return None
        raw = response.state.body.get("payment_type")
        if isinstance(raw, (bytes, bytearray)):
            payment_type = bytes(raw)
        elif isinstance(raw, str):
            payment_type = HexBytes(raw)
        else:
            self._logger.warning(
                f"Mech {mech_address} paymentType returned an unexpected "
                f"type {type(raw).__name__}"
            )
            return None
        if len(payment_type) != 32:
            self._logger.warning(
                f"Mech {mech_address} paymentType has wrong length "
                f"{len(payment_type)} (expected 32)"
            )
            return None
        return payment_type

    def _resolve_delivery_rate(
        self, mech_address: str
    ) -> Generator[None, None, Optional[int]]:
        """Read the mech's ``maxDeliveryRate``; mech-client charges at this rate."""
        response = yield from self._b.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=mech_address,
            contract_id=str(MechMMContract.contract_id),
            contract_callable="get_max_delivery_rate",
        )
        if response.performative != ContractApiMessage.Performative.STATE:
            self._logger.warning(
                f"Mech {mech_address} maxDeliveryRate read failed: "
                f"performative={response.performative}"
            )
            return None
        raw = response.state.body.get("max_delivery_rate")
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            self._logger.warning(
                f"Mech {mech_address} maxDeliveryRate returned a non-numeric "
                f"value: {exc}"
            )
            return None

    # ---------- 402 destination validation ---------------------------------

    def _resolve_canonical_tracker(
        self, payment_type: bytes
    ) -> Generator[None, None, Optional[str]]:
        """Read ``MechMarketplace.mapPaymentTypeBalanceTrackers(paymentType)``.

        Returns the lowercase address (no checksum) so the caller can
        compare against the lowercased ``challenge.pay_to`` without
        worrying about EIP-55 differences. ``None`` on a read failure or
        a zero-address registration (meaning the marketplace has no
        tracker for this paymentType).
        """
        response = yield from self._b.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=self._config.mech_marketplace_address,
            contract_id=str(MechMarketplace.contract_id),
            contract_callable="get_balance_tracker",
            payment_type=payment_type,
        )
        if response.performative != ContractApiMessage.Performative.STATE:
            self._logger.warning(
                "MechMarketplace.mapPaymentTypeBalanceTrackers read failed: "
                f"performative={response.performative}"
            )
            return None
        raw = response.state.body.get("balance_tracker")
        if not isinstance(raw, str) or not raw:
            self._logger.warning(
                "MechMarketplace.mapPaymentTypeBalanceTrackers returned an "
                f"unexpected value {raw!r}"
            )
            return None
        lowered = raw.lower()
        if lowered == _ZERO_ADDRESS:
            self._logger.warning(
                "MechMarketplace has no BalanceTracker registered for "
                f"paymentType 0x{payment_type.hex()}; cannot validate 402 "
                "destination."
            )
            return None
        return lowered

    def _resolve_tracker_token(
        self, tracker_address: str
    ) -> Generator[None, None, Optional[str]]:
        """Read ``BalanceTrackerFixedPriceToken.token()``.

        Returns the lowercase ERC20 address the tracker accepts, so the
        caller can compare it against ``challenge.asset``. ``None`` on
        any read failure or non-address response.
        """
        response = yield from self._b.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=tracker_address,
            contract_id=str(BalanceTrackerFixedPriceToken.contract_id),
            contract_callable="get_token",
        )
        if response.performative != ContractApiMessage.Performative.STATE:
            self._logger.warning(
                f"BalanceTrackerFixedPriceToken.token() read failed at "
                f"{tracker_address}: performative={response.performative}"
            )
            return None
        raw = response.state.body.get("token")
        if not isinstance(raw, str) or not raw:
            self._logger.warning(
                "BalanceTrackerFixedPriceToken.token() returned an "
                f"unexpected value {raw!r}"
            )
            return None
        return raw.lower()

    def _validate_402_destination(
        self,
        challenge: PaymentChallenge,
        payment_type: bytes,
    ) -> Generator[None, None, bool]:
        """Cross-check the 402 ``payTo`` (and ``asset``) against the on-chain tracker.

        Returns ``True`` when the 402 destination matches the canonical
        BalanceTracker the marketplace has registered for this
        ``paymentType``, and (on the token path) when ``challenge.asset``
        matches the tracker's ``token()`` getter. Without this check a
        malicious mech could direct a real native transfer / ERC20
        approval to an attacker-chosen address — bounded by
        ``auto_deposit_cap_per_cycle`` but recurring (review: bennyjo on
        PR #95).

        NVM paths are skipped: ``_build_deposit_tx`` already returns
        ``None`` for NVM (the BalanceTracker reverts ``depositFor`` by
        design), so the destination would never be used.
        """
        payment_label = self._classify_payment_type(payment_type)
        if payment_label in (_PAYMENT_NVM_NATIVE, _PAYMENT_NVM_TOKEN, ""):
            # The deposit builder won't act on these labels anyway; treat
            # validation as a no-op so the existing surface (None tx hex →
            # OFFCHAIN_402_INSUFFICIENT) drives the outcome.
            return True

        canonical = yield from self._resolve_canonical_tracker(payment_type)
        if canonical is None:
            # Read failure or no tracker registered; we cannot prove the
            # destination is safe, so refuse to deposit.
            return False

        challenge_pay_to = challenge.pay_to.lower()
        if challenge_pay_to != canonical:
            self._logger.error(
                "Offchain 402 payTo mismatch: mech reported "
                f"{challenge.pay_to!r} but the canonical BalanceTracker for "
                f"paymentType 0x{payment_type.hex()} is {canonical!r}. "
                "Refusing to deposit and failing over."
            )
            return False

        if payment_label == _PAYMENT_TOKEN:
            tracker_token = yield from self._resolve_tracker_token(canonical)
            if tracker_token is None:
                return False
            challenge_asset = challenge.asset.lower()
            if challenge_asset != tracker_token:
                self._logger.error(
                    "Offchain 402 asset mismatch: mech reported "
                    f"{challenge.asset!r} but BalanceTracker {canonical} "
                    f"accepts {tracker_token!r}. Refusing to deposit and "
                    "failing over."
                )
                return False

        return True

    # ---------- mech selection ---------------------------------------------

    def _pick_next_mech(
        self, attempted: List[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        """Pick the next ranked mech with a known HTTP URL.

        Reuses ``synchronized_data.ranked_mechs`` from the existing on-chain
        selection (no separate ranking for offchain), filtering out mechs
        already tried this cycle. URL comes from ``MechInfo.http_url`` (set
        by :func:`mech_info.populate_tools` from the manifest) and falls
        back to the static ``offchain_url`` config when the per-mech field
        is empty.

        Static-URL fallback is gated on a truthy ``priority_mech_address``.
        A blank address would be silently appended to ``attempted`` and the
        downstream contract reads at ``""`` would fail before the static URL
        was ever POSTed to (review C2), so we only return the fallback when
        an explicit address is configured.
        """
        attempted_lc = {a.lower() for a in attempted}
        for mech in self._synced.ranked_mechs:
            if mech.address.lower() in attempted_lc:
                continue
            url = mech.http_url or self._config.offchain_url
            if not url:
                continue
            return mech.address, url
        # No ranked mech left. Fall back to the static URL only if the
        # operator configured both an ``offchain_url`` and a
        # ``priority_mech_address`` that has not already been tried.
        fallback = self._config.offchain_url
        static_address = self._config.priority_mech_address
        if fallback and static_address and static_address.lower() not in attempted_lc:
            return static_address, fallback
        return None, None

    # ---------- deposit multisend ------------------------------------------

    def _build_deposit_tx(
        self,
        challenge: PaymentChallenge,
        payment_type: bytes,
    ) -> Generator[None, None, Optional[str]]:
        """Build the Safe-multisend hash for the 402 retry path.

        ERC20: bundles ``approve(BalanceTracker, shortfall) +
        depositFor(safe, shortfall)`` atomically through MultiSend. Native:
        a single Safe tx calling ``depositFor(safe)`` with ``value=shortfall``.
        Nevermined: returns ``None`` (``BalanceTrackerNvmSubscription{Native,Token}.depositFor``
        reverts by design, so an on-chain retry path is impossible). An
        unknown ``paymentType`` also returns ``None``. The caller treats
        any ``None`` here as ``OFFCHAIN_402_INSUFFICIENT`` — failover to
        another ranked mech would charge the same Safe balance and reach
        the same result, so the caller surfaces to the consumer rather
        than burning the failover budget.
        """
        if challenge.shortfall <= 0:
            return None
        payment_label = self._classify_payment_type(payment_type)
        if payment_label in (_PAYMENT_NVM_NATIVE, _PAYMENT_NVM_TOKEN):
            self._logger.info(
                "Offchain 402 against an NVM mech; auto-deposit is not "
                "supported on chain. Surfacing to the consumer."
            )
            return None

        if payment_label == _PAYMENT_NATIVE:
            tx_hash = yield from self._build_native_deposit_tx(challenge)
            return tx_hash
        if payment_label == _PAYMENT_TOKEN:
            tx_hash = yield from self._build_token_deposit_multisend(challenge)
            return tx_hash
        self._logger.warning(
            f"Unknown paymentType selector 0x{payment_type.hex()}; "
            "cannot build a deposit tx. Surfacing to the consumer."
        )
        return None

    def _build_native_deposit_tx(
        self, challenge: PaymentChallenge
    ) -> Generator[None, None, Optional[str]]:
        """Single Safe tx: ``BalanceTracker.depositFor(safe)`` with ``value=shortfall``."""
        # Encode the depositFor selector — value is supplied by the Safe
        # tx envelope, not by the calldata, mirroring mech-client's native
        # ``deposit_native`` path.
        call_data_response = yield from self._b.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=challenge.pay_to,
            contract_id=str(BalanceTrackerFixedPriceNative.contract_id),
            contract_callable="build_deposit_for_data",
            account=self._safe_address(),
        )
        if call_data_response.performative != ContractApiMessage.Performative.STATE:
            self._logger.warning(
                f"BalanceTrackerFixedPriceNative.build_deposit_for_data "
                f"read failed at {challenge.pay_to}: "
                f"performative={call_data_response.performative}"
            )
            return None
        data = call_data_response.state.body.get("data")
        if not isinstance(data, (bytes, bytearray)):
            self._logger.warning(
                "BalanceTrackerFixedPriceNative.build_deposit_for_data "
                f"returned a non-bytes payload {type(data).__name__}"
            )
            return None
        return (
            yield from self._build_safe_tx_for_single_call(
                to_address=challenge.pay_to,
                data=bytes(data),
                value=challenge.shortfall,
            )
        )

    def _build_token_deposit_multisend(
        self, challenge: PaymentChallenge
    ) -> Generator[None, None, Optional[str]]:
        """Two-call multisend: ``ERC20.approve`` + ``BalanceTracker.depositFor``."""
        approve_data_response = yield from self._b.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=challenge.asset,
            contract_id=str(ERC20TokenContract.contract_id),
            contract_callable="build_approval_tx",
            spender=challenge.pay_to,
            amount=challenge.shortfall,
        )
        if approve_data_response.performative != ContractApiMessage.Performative.STATE:
            self._logger.warning(
                f"ERC20.build_approval_tx read failed at {challenge.asset}: "
                f"performative={approve_data_response.performative}"
            )
            return None
        approve_data = approve_data_response.state.body.get("data")

        deposit_data_response = yield from self._b.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=challenge.pay_to,
            contract_id=str(BalanceTrackerFixedPriceToken.contract_id),
            contract_callable="build_deposit_for_data",
            account=self._safe_address(),
            amount=challenge.shortfall,
        )
        if deposit_data_response.performative != ContractApiMessage.Performative.STATE:
            self._logger.warning(
                f"BalanceTrackerFixedPriceToken.build_deposit_for_data "
                f"read failed at {challenge.pay_to}: "
                f"performative={deposit_data_response.performative}"
            )
            return None
        deposit_data = deposit_data_response.state.body.get("data")

        if not isinstance(approve_data, (bytes, bytearray)):
            self._logger.warning(
                "ERC20.build_approval_tx returned a non-bytes payload "
                f"{type(approve_data).__name__}"
            )
            return None
        if not isinstance(deposit_data, (bytes, bytearray)):
            self._logger.warning(
                "BalanceTrackerFixedPriceToken.build_deposit_for_data returned "
                f"a non-bytes payload {type(deposit_data).__name__}"
            )
            return None

        multisend_response = yield from self._b.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=self._b.params.multisend_address,
            contract_id=str(MultiSendContract.contract_id),
            contract_callable="get_tx_data",
            multi_send_txs=[
                {
                    "operation": MultiSendOperation.CALL,
                    "to": challenge.asset,
                    "value": 0,
                    "data": HexBytes(bytes(approve_data)),
                },
                {
                    "operation": MultiSendOperation.CALL,
                    "to": challenge.pay_to,
                    "value": 0,
                    "data": HexBytes(bytes(deposit_data)),
                },
            ],
        )
        if multisend_response.performative != ContractApiMessage.Performative.STATE:
            self._logger.warning(
                f"MultiSend.get_tx_data read failed: "
                f"performative={multisend_response.performative}"
            )
            return None
        data = multisend_response.state.body.get("data")
        if not isinstance(data, str):
            self._logger.warning(
                f"MultiSend.get_tx_data returned a non-string payload "
                f"{type(data).__name__}"
            )
            return None

        return (
            yield from self._build_safe_tx_for_single_call(
                to_address=self._b.params.multisend_address,
                data=HexBytes(data),
                value=0,
                operation=MultiSendOperation.DELEGATE_CALL.value,
            )
        )

    def _build_safe_tx_for_single_call(
        self,
        to_address: str,
        data: bytes,
        value: int,
        operation: int = 0,
    ) -> Generator[None, None, Optional[str]]:
        """Wrap a single contract call into a Safe tx hash for settlement.

        Reuses the existing ``MechRequestBehaviour`` machinery for assembling
        a Safe transaction hash; identical shape to the on-chain mech
        request build path so the consumer's transaction_settlement skill
        treats it the same.
        """
        # The behaviour exposes ``_get_safe_tx_hash`` as the shared helper
        # for Safe-routed contract calls; routing through it preserves
        # the gas estimation, nonce handling, and signature-aggregation
        # path the on-chain mech request already uses.
        return (
            yield from self._b._get_safe_tx_hash(  # pylint: disable=protected-access
                to_address=to_address,
                value=value,
                data=data,
                operation=operation,
            )
        )

    @staticmethod
    def _classify_payment_type(payment_type: bytes) -> str:
        """Classify the 32-byte ``paymentType`` selector against the deployed set.

        Hash values mirror the ``PaymentType`` enum in
        :mod:`mech_interact_abci.behaviours.request` — a single source of
        truth across the on-chain and offchain branches. An unknown hash
        returns the empty string and is treated as "no auto-resolve
        possible" by the deposit builder.
        """
        as_hex = "0x" + payment_type.hex()
        if as_hex == _PAYMENT_TYPE_HASH_NATIVE:
            return _PAYMENT_NATIVE
        if as_hex in _PAYMENT_TYPE_HASHES_TOKEN:
            return _PAYMENT_TOKEN
        if as_hex == _PAYMENT_TYPE_HASH_NVM_NATIVE:
            return _PAYMENT_NVM_NATIVE
        if as_hex == _PAYMENT_TYPE_HASH_NVM_TOKEN:
            return _PAYMENT_NVM_TOKEN
        return ""

    # ---------- serialisation helpers --------------------------------------

    def _serialise_mech_requests(self, requests: List[MechMetadata]) -> str:
        """Mirror the JSON shape ``MechRequestBehaviour`` writes today."""
        return json.dumps(
            [dataclasses.asdict(r) for r in requests],
            ensure_ascii=True,
        )

    def _serialise_pending_response(self, request_id_bytes: bytes, nonce: int) -> str:
        """Initial ``MechInteractionResponse`` placeholder for the polling round."""
        placeholder = MechInteractionResponse(
            data=request_id_bytes.hex(),
            requestId=int.from_bytes(request_id_bytes, "big"),
            requestIds=[int.from_bytes(request_id_bytes, "big")],
            numRequests=1,
            nonce=str(nonce),
            result=None,
            error="Unknown",
        )
        return json.dumps([dataclasses.asdict(placeholder)], ensure_ascii=True)

    # ---------- state-on-the-wire helpers ----------------------------------

    def _load_pending_request(self) -> Optional[PendingRequest]:
        raw = self._synced.offchain_pending_request
        if not raw:
            return None
        return PendingRequest.from_dict(raw)

    def _safe_address(self) -> str:
        return str(self._synced.safe_contract_address)

    @staticmethod
    def _failure_label_for(outcome: Optional[OffchainAttemptOutcome]) -> str:
        if outcome is OffchainAttemptOutcome.TIMEOUT:
            return OFFCHAIN_TIMEOUT_ALL_MECHS
        if outcome is OffchainAttemptOutcome.SERVER_BUSY:
            return OFFCHAIN_503_ALL_MECHS
        if outcome is OffchainAttemptOutcome.BAD_RESPONSE:
            return OFFCHAIN_BAD_RESPONSE
        return OFFCHAIN_TIMEOUT_ALL_MECHS


__all__ = [
    "OffchainAttemptOutcome",
    "OffchainAttemptResult",
    "OffchainCycleResult",
    "OffchainRequestExecutor",
    "PaymentChallenge",
    "PendingRequest",
    "build_request_metadata",
    "compute_cidv1_bytes",
    "derive_request_id_bytes",
    "parse_payment_challenge",
]
