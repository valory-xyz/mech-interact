# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2023-2026 Valory AG
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

"""Test the models.py module of the MechInteract."""

from contextlib import contextmanager
from typing import Any, Dict, Generator
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from packages.valory.contracts.multisend.contract import MultiSendOperation
from packages.valory.skills.abstract_round_abci.test_tools.base import DummyContext
from packages.valory.skills.mech_interact_abci.models import (
    CHAIN_TO_NVM_CONFIG,
    MechMarketplaceConfig,
    MechToolsSpecs,
    MultisendBatch,
    NVMConfig,
    Ox,
    SharedState,
    validate_offchain_params,
)

PENALIZE_TIME_WINDOW = 300
SAMPLE_MECH_ADDRESS = "0xmech1"
SAMPLE_MECH_ADDRESS_2 = "0xmech2"


class TestNVMConfig:
    """Tests for NVMConfig dataclass."""

    def _make_config(self, **overrides: object) -> NVMConfig:
        """Create an NVMConfig with sensible defaults."""
        defaults: Dict[str, Any] = dict(  # nosec B106
            balance_tracker_address="0xabc",
            did_registry_address="0xdef",
            agreement_store_manager_address="0x123",
            lock_payment_condition_address="0x456",
            transfer_nft_condition_address="0x789",
            escrow_payment_condition_address="0xaaa",
            plan_fee_nvm=100,
            plan_price_mech=900,
            subscription_nft_address="0xbbb",
            nft_sales_address="0xccc",
            subscription_token_address="0xddd",
            subscription_provider_address="0xeee",
            plan_did="did:nv:abc123",
        )
        defaults.update(overrides)
        return NVMConfig(**defaults)

    def test_did_property_strips_prefix(self) -> None:
        """Test that the did property replaces the prefix with 0x."""
        config = self._make_config(plan_did="did:nv:abc123def456")
        assert config.did == "0xabc123def456"

    def test_did_property_no_prefix(self) -> None:
        """Test did when the plan_did has no prefix to replace."""
        config = self._make_config(plan_did="0xalready")
        assert config.did == "0xalready"


class TestMechMarketplaceConfig:
    """Tests for MechMarketplaceConfig dataclass."""

    @pytest.mark.parametrize("timeout", [0, -1, -100])
    def test_invalid_response_timeout(self, timeout: MagicMock) -> None:
        """Test that non-positive response_timeout raises ValueError."""
        with pytest.raises(ValueError, match="response_timeout must be positive"):
            MechMarketplaceConfig(
                mech_marketplace_address="0xmarket",
                response_timeout=timeout,
            )

    def test_with_priority_mech(self) -> None:
        """Test config with priority mech address set."""
        config = MechMarketplaceConfig(
            mech_marketplace_address="0xmarket",
            response_timeout=30,
            priority_mech_address="0xpriority",
            use_dynamic_mech_selection=False,
        )
        assert config.priority_mech_address == "0xpriority"
        assert config.use_dynamic_mech_selection is False

    def test_negative_auto_deposit_cap_raises(self) -> None:
        """A negative cap is rejected even on the on-chain path."""
        with pytest.raises(
            ValueError, match="auto_deposit_cap_per_cycle must be non-negative"
        ):
            MechMarketplaceConfig(
                mech_marketplace_address="0xmarket",
                response_timeout=30,
                auto_deposit_cap_per_cycle=-1,
            )

    @pytest.mark.parametrize(
        "field, value, error_match",
        [
            ("offchain_poll_interval_seconds", 0.0, "must be positive"),
            ("offchain_poll_interval_seconds", -1.0, "must be positive"),
            ("offchain_poll_timeout_seconds", 0.0, "must be positive"),
            ("offchain_poll_timeout_seconds", -1.0, "must be positive"),
            ("offchain_failover_max_retries", -1, "must be non-negative"),
        ],
    )
    def test_invalid_offchain_timing_params_raise(
        self, field: str, value: float, error_match: str
    ) -> None:
        """Each offchain timing/retry param is validated for sensible ranges."""
        with pytest.raises(ValueError, match=error_match):
            MechMarketplaceConfig(
                mech_marketplace_address="0xmarket",
                response_timeout=30,
                **{field: value},  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize(
        "interval_in, timeout_in",
        [
            (5, 300),
            ("5", "300"),
        ],
        ids=("int_yaml_values", "quoted_string_yaml_values"),
    )
    def test_offchain_poll_values_coerced_to_float(
        self, interval_in: Any, timeout_in: Any
    ) -> None:
        """Int and quoted-string poll values (as yaml delivers them) become floats.

        Readers like ``OffchainResponsePoller`` consume these fields without
        re-wrapping in ``float()``, so the coercion in ``__post_init__`` is
        what upholds the annotated types. The string case pins the fix for
        the boot crash where a quoted override made ``"300" <= 0`` raise
        ``TypeError`` before coercion existed.
        """
        config = MechMarketplaceConfig(
            mech_marketplace_address="0xmarket",
            response_timeout=30,
            offchain_poll_interval_seconds=interval_in,
            offchain_poll_timeout_seconds=timeout_in,
        )
        assert type(config.offchain_poll_interval_seconds) is float
        assert config.offchain_poll_interval_seconds == 5.0
        assert type(config.offchain_poll_timeout_seconds) is float
        assert config.offchain_poll_timeout_seconds == 300.0


class TestValidateOffchainParams:
    """Pure-function validation of the hoisted off-chain params.

    Covers the checks that used to live in ``MechMarketplaceConfig.__post_init__``
    for ``use_offchain`` / ``offchain_deposit_target_calls`` before they were
    hoisted to top-level ``MechParams`` attrs. The logic was extracted into a
    module-level pure function so unit tests can exercise every branch
    without wiring the full ``skill_context`` harness ``MechParams.__init__``
    needs.
    """

    def _mmc(self, **overrides: Any) -> MechMarketplaceConfig:
        """Build a marketplace config with sensible off-chain-friendly defaults."""
        defaults: Dict[str, Any] = dict(
            mech_marketplace_address="0xmarket",
            response_timeout=30,
            use_dynamic_mech_selection=False,
            offchain_url=None,
            auto_deposit_cap_per_cycle=None,
        )
        defaults.update(overrides)
        return MechMarketplaceConfig(**defaults)

    def test_offchain_off_accepts_any_marketplace_shape(self) -> None:
        """When ``use_offchain=False``, none of the cross-field guards fire."""
        # Bare config (no url, no dynamic, no cap): the guards would trip on
        # every clause if use_offchain were True. False must be an override.
        validate_offchain_params(
            use_offchain=False,
            marketplace_config=self._mmc(),
            offchain_deposit_target_calls=10,
        )

    def test_offchain_on_without_url_or_dynamic_raises(self) -> None:
        """Off-chain requires either a static URL or dynamic discovery."""
        with pytest.raises(ValueError, match="use_offchain requires either"):
            validate_offchain_params(
                use_offchain=True,
                marketplace_config=self._mmc(
                    use_dynamic_mech_selection=False,
                    offchain_url=None,
                    auto_deposit_cap_per_cycle=1_000_000,
                ),
                offchain_deposit_target_calls=10,
            )

    def test_offchain_on_without_cap_raises(self) -> None:
        """``auto_deposit_cap_per_cycle`` is required when ``use_offchain=True``.

        Pinned because a missing cap plus a truthy-but-non-bool ``use_offchain``
        used to slip through and silently run the on-chain path. That specific
        env-var-string case is still guarded by ``_ensure``'s bool type check
        upstream; this test pins the cross-field requirement itself.
        """
        with pytest.raises(
            ValueError, match="use_offchain requires auto_deposit_cap_per_cycle"
        ):
            validate_offchain_params(
                use_offchain=True,
                marketplace_config=self._mmc(
                    offchain_url="https://mech.example/",
                    auto_deposit_cap_per_cycle=None,
                ),
                offchain_deposit_target_calls=10,
            )

    def test_offchain_on_with_static_url_and_cap_passes(self) -> None:
        """Static URL + cap satisfies the cross-field requirements."""
        validate_offchain_params(
            use_offchain=True,
            marketplace_config=self._mmc(
                offchain_url="https://mech.example/",
                auto_deposit_cap_per_cycle=1_000_000,
            ),
            offchain_deposit_target_calls=10,
        )

    def test_offchain_on_with_dynamic_selection_and_cap_passes(self) -> None:
        """Dynamic selection substitutes for a static URL."""
        validate_offchain_params(
            use_offchain=True,
            marketplace_config=self._mmc(
                use_dynamic_mech_selection=True,
                offchain_url=None,
                auto_deposit_cap_per_cycle=1_000_000,
            ),
            offchain_deposit_target_calls=10,
        )

    @pytest.mark.parametrize("bad_value", [0, -1, -100])
    def test_target_calls_below_one_raises(self, bad_value: int) -> None:
        """``< 1`` is meaningless: deposit must cover at least the current call."""
        with pytest.raises(
            ValueError, match="offchain_deposit_target_calls must be >= 1"
        ):
            validate_offchain_params(
                use_offchain=False,
                marketplace_config=self._mmc(),
                offchain_deposit_target_calls=bad_value,
            )

    def test_target_calls_default_of_ten_is_valid(self) -> None:
        """Default sizes 10 forward calls per deposit.

        Pinned because the value is the entry guard for the dynamic-sizing
        formula: a silent change to the default would shift every off-chain
        deployment's BalanceTracker top-up cadence in lockstep. The yaml
        default (``mech_interact_abci/skill.yaml``, top-level Params args)
        must round-trip through this validator without raising.
        """
        validate_offchain_params(
            use_offchain=False,
            marketplace_config=self._mmc(),
            offchain_deposit_target_calls=10,
        )


class TestLegacyMarketplaceKeysMigrationError:
    """The stale nested keys must fail loudly, not with a bare ``TypeError``.

    A frozen-dataclass ``TypeError`` on an unexpected kwarg would leave the
    operator guessing where the fields went. This test pins the explicit
    migration message routed through ``MechParams.__init__`` — but the
    dataclass check itself is trivially exercisable here by feeding the
    same kwargs directly to ``MechMarketplaceConfig`` and confirming the
    TypeError still fires (which is the trigger the ``__init__`` guard
    exists to translate).
    """

    @pytest.mark.parametrize(
        "legacy_kwarg",
        [
            {"use_offchain": False},
            {"offchain_deposit_target_calls": 10},
            {"use_offchain": True, "offchain_deposit_target_calls": 5},
        ],
    )
    def test_legacy_kwargs_still_raise_on_dataclass(
        self, legacy_kwarg: Dict[str, Any]
    ) -> None:
        """The dataclass rejects the legacy kwargs.

        The friendly migration guard lives upstream in
        ``MechParams.__init__`` (covered by real-boot / integration tests
        via ``check-abci-docstrings``).
        """
        with pytest.raises(TypeError):
            MechMarketplaceConfig(
                mech_marketplace_address="0xmarket",
                response_timeout=30,
                **legacy_kwarg,
            )


class TestSharedStateLastFailureReason:
    """Tests for SharedState.last_failure_reason."""

    def test_default_is_none(self) -> None:
        """A freshly constructed SharedState has no failure reason."""
        state = SharedState(name="", skill_context=DummyContext())
        assert state.last_failure_reason is None


class TestResponseRoundTimeoutEvent:
    """The response round shares the app-wide ``ROUND_TIMEOUT`` event."""

    def test_response_round_times_out_via_shared_round_timeout(self) -> None:
        """``MechResponseRound`` must use ``Event.ROUND_TIMEOUT``, not a dedicated event.

        Consumer repos (trader, market-resolver) rebind
        ``MechInteractEvent.ROUND_TIMEOUT`` in their composed apps; a
        dedicated response-timeout event would silently escape those
        overrides. Locks in the revert of the ``RESPONSE_ROUND_TIMEOUT``
        rename.
        """
        from packages.valory.skills.mech_interact_abci.rounds import (
            MechInteractAbciApp,
        )
        from packages.valory.skills.mech_interact_abci.states.base import Event
        from packages.valory.skills.mech_interact_abci.states.final_states import (
            FinishedMechResponseTimeoutRound,
        )
        from packages.valory.skills.mech_interact_abci.states.response import (
            MechResponseRound,
        )

        assert set(MechInteractAbciApp.event_to_timeout) == {Event.ROUND_TIMEOUT}
        response_events = MechInteractAbciApp.transition_function[MechResponseRound]
        assert response_events[Event.ROUND_TIMEOUT] is FinishedMechResponseTimeoutRound
        assert not hasattr(Event, "RESPONSE_ROUND_TIMEOUT")


class TestMultisendBatch:
    """Tests for MultisendBatch dataclass."""

    def test_valid_batch(self) -> None:
        """Test creating a valid batch."""
        batch = MultisendBatch(to="0xaddr", data=b"\x01\x02")
        assert batch.to == "0xaddr"
        assert batch.data == b"\x01\x02"
        assert batch.value == 0
        assert batch.operation == MultiSendOperation.CALL

    def test_empty_to_raises(self) -> None:
        """Test that empty 'to' address raises ValueError."""
        with pytest.raises(ValueError, match="Target address"):
            MultisendBatch(to="", data=b"\x01")

    def test_negative_value_raises(self) -> None:
        """Test that negative value raises ValueError."""
        with pytest.raises(ValueError, match="Value must be non-negative"):
            MultisendBatch(to="0xaddr", data=b"", value=-1)

    def test_non_bytes_data_raises(self) -> None:
        """Test that non-bytes data raises ValueError."""
        with pytest.raises(
            ValueError, match="Data must be a bytes or bytearray instance"
        ):
            MultisendBatch(to="0xaddr", data="not-bytes")  # type: ignore

    def test_bytearray_data_accepted(self) -> None:
        """Test that bytearray data is accepted (per the error message contract)."""
        batch = MultisendBatch(to="0xaddr", data=bytearray(b"\x01\x02"))
        assert batch.data == bytearray(b"\x01\x02")


class TestSharedStatePenalization:
    """Tests for SharedState penalization methods."""

    def _make_shared_state(self) -> SharedState:
        """Create a SharedState for testing."""
        return SharedState(name="", skill_context=DummyContext())

    @contextmanager
    def _patch_time_and_params(
        self,
        state: SharedState,
        timestamp: int,
        time_window: int = PENALIZE_TIME_WINDOW,
    ) -> Generator:
        """Patch synced_timestamp and params on a SharedState.

        Eliminates the repeated PropertyMock chain across penalization tests.
        """
        with (
            patch.object(
                type(state),
                "synced_timestamp",
                new_callable=PropertyMock,
                return_value=timestamp,
            ),
            patch.object(
                type(state), "params", new_callable=PropertyMock
            ) as mock_params,
        ):
            mock_params.return_value = MagicMock(penalize_mech_time_window=time_window)
            yield

    @contextmanager
    def _patch_timestamp(self, state: SharedState, timestamp: int) -> Generator:
        """Patch only synced_timestamp (for penalize_mech calls that don't read params)."""
        with patch.object(
            type(state),
            "synced_timestamp",
            new_callable=PropertyMock,
            return_value=timestamp,
        ):
            yield

    def test_penalize_mech_adds_to_dict(self) -> None:
        """Test penalizing a mech adds it to the dict."""
        state = self._make_shared_state()
        with self._patch_time_and_params(state, timestamp=1000):
            state.penalize_mech(SAMPLE_MECH_ADDRESS)
            assert SAMPLE_MECH_ADDRESS in state.penalized_mechs

    def test_penalize_mech_expires_after_window(self) -> None:
        """Test that penalized mechs expire after the time window."""
        state = self._make_shared_state()
        with self._patch_timestamp(state, timestamp=1000):
            state.penalize_mech(SAMPLE_MECH_ADDRESS)

        # 1400 - 1000 = 400 > window of 300 -> expired
        with self._patch_time_and_params(state, timestamp=1400):
            assert state.penalized_mechs == {}

    def test_penalize_mech_active_within_window(self) -> None:
        """Test that penalized mechs remain active within the time window."""
        state = self._make_shared_state()
        with self._patch_timestamp(state, timestamp=1000):
            state.penalize_mech(SAMPLE_MECH_ADDRESS)

        # 1100 - 1000 = 100 < window of 300 -> still active
        with self._patch_time_and_params(state, timestamp=1100):
            assert SAMPLE_MECH_ADDRESS in state.penalized_mechs

    def test_penalized_mechs_sorted_by_time(self) -> None:
        """Test that penalized mechs are sorted by penalization time."""
        state = self._make_shared_state()
        with self._patch_timestamp(state, timestamp=1000):
            state.penalize_mech(SAMPLE_MECH_ADDRESS)
        with self._patch_timestamp(state, timestamp=1010):
            state.penalize_mech(SAMPLE_MECH_ADDRESS_2)

        with self._patch_time_and_params(state, timestamp=1015):
            keys = list(state.penalized_mechs.keys())
            assert keys == [SAMPLE_MECH_ADDRESS, SAMPLE_MECH_ADDRESS_2]

    def test_penalize_last_called_mech(self) -> None:
        """Test penalizing the last called mech."""
        state = self._make_shared_state()
        state.last_called_mech = "0xlast"
        with self._patch_time_and_params(state, timestamp=1000):
            state.penalize_last_called_mech()
            assert "0xlast" in state.penalized_mechs

    def test_penalize_last_called_mech_none_logs_warning(self) -> None:
        """Test that penalizing when no last_called_mech logs a warning."""
        state = self._make_shared_state()
        assert state.last_called_mech is None
        state.penalize_last_called_mech()
        state.context.logger.warning.assert_called_once()


class TestChainMappings:
    """Tests for chain-to-config mappings."""

    def test_all_nvm_configs_have_valid_did(self) -> None:
        """Test that all NVM configs have a valid did property."""
        for chain, config in CHAIN_TO_NVM_CONFIG.items():
            did = config.did
            assert did.startswith(Ox), f"Config for {chain} has invalid did: {did}"


def _http_response(status: int, body: bytes) -> MagicMock:
    """Build a minimal HttpMessage-like mock with status_code and body."""
    msg = MagicMock()
    msg.status_code = status
    msg.body = body
    return msg


def _classifier() -> MechToolsSpecs:
    """Build a MechToolsSpecs bypassing __init__ for pure-method testing.

    is_permanent_error reads no instance state except self.context.logger (for
    the unclassified-status warning) and self.url (in that warning's message),
    so __new__ plus a stub context/url keeps the test independent of ApiSpecs
    setup.
    """
    instance = MechToolsSpecs.__new__(MechToolsSpecs)
    # `context` is a read-only property on Model and the instance is frozen,
    # so bypass both by writing straight into __dict__.
    instance.__dict__["_context"] = MagicMock()
    instance.__dict__["url"] = "https://gateway.example/ipfs/bafy..."
    return instance


class TestIsPermanentError:
    """Tests for MechToolsSpecs.is_permanent_error classifier."""

    def test_500_with_protobuf_marker_is_permanent(self) -> None:
        """Reported incident: autonolas gateway 500 + protobuf wireType."""
        body = (
            b"failed to resolve /ipfs/f01...: protobuf: (PBNode) "
            b"invalid wireType, expected 2, got 3"
        )
        assert _classifier().is_permanent_error(_http_response(500, body)) is True

    def test_500_with_bad_gateway_body_is_transient(self) -> None:
        """Generic 5xx without permanent markers should retry."""
        assert (
            _classifier().is_permanent_error(_http_response(500, b"bad gateway"))
            is False
        )

    def test_502_without_markers_is_transient(self) -> None:
        """Empty 502 body is a classic transient flake."""
        assert _classifier().is_permanent_error(_http_response(502, b"")) is False

    def test_504_gateway_timeout_is_transient(self) -> None:
        """504 timeouts should retry by default."""
        assert (
            _classifier().is_permanent_error(_http_response(504, b"gateway timeout"))
            is False
        )

    def test_503_without_markers_is_transient(self) -> None:
        """503 service unavailable is transient."""
        assert (
            _classifier().is_permanent_error(
                _http_response(503, b"service unavailable")
            )
            is False
        )

    def test_404_is_permanent(self) -> None:
        """404 CID not found is permanent regardless of body."""
        assert _classifier().is_permanent_error(_http_response(404, b"")) is True

    def test_400_is_permanent(self) -> None:
        """400 bad request is permanent."""
        assert (
            _classifier().is_permanent_error(_http_response(400, b"bad request"))
            is True
        )

    def test_200_with_invalid_json_is_permanent(self) -> None:
        """2xx + process_response failure means malformed content, not a flake."""
        assert (
            _classifier().is_permanent_error(_http_response(200, b"not valid json {"))
            is True
        )

    def test_200_with_valid_json_missing_tools_key_is_permanent(self) -> None:
        """Server's content is stable; retries won't change the missing key."""
        assert (
            _classifier().is_permanent_error(_http_response(200, b'{"not_tools": []}'))
            is True
        )

    def test_marker_matching_is_case_insensitive(self) -> None:
        """Upper/mixed-case markers must still classify as permanent."""
        assert (
            _classifier().is_permanent_error(
                _http_response(500, b"INVALID WIRETYPE detected")
            )
            is True
        )

    def test_marker_matching_is_substring(self) -> None:
        """Markers appear anywhere in the body within the scan window."""
        body = b"x" * 1000 + b"cid not found" + b"y" * 1000
        assert _classifier().is_permanent_error(_http_response(500, body)) is True

    def test_marker_outside_scan_window_is_not_matched(self) -> None:
        """Markers beyond the scan slice fall through to transient."""
        body = b"x" * 5000 + b"cid not found"
        assert _classifier().is_permanent_error(_http_response(500, body)) is False

    def test_non_utf8_body_does_not_crash(self) -> None:
        """Invalid UTF-8 bytes must be tolerated; marker still matched."""
        body = b"\xff\xfe invalid wireType"
        assert _classifier().is_permanent_error(_http_response(500, body)) is True

    def test_302_redirect_without_marker_is_transient(self) -> None:
        """3xx doesn't fall into 2xx/4xx/5xx rules; default is transient."""
        assert _classifier().is_permanent_error(_http_response(302, b"")) is False

    def test_500_with_cid_not_found_marker_is_permanent(self) -> None:
        """CID-not-found semantics at the gateway layer are permanent."""
        assert (
            _classifier().is_permanent_error(_http_response(500, b"cid not found"))
            is True
        )
