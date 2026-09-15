from datetime import datetime, timezone

import pytest

from clausage_bar.model import Snapshot, Spend, WindowUsage, _parse_dt

# 2026-09-07T13:30:00Z, the reset boundary observed live from both sources.
EPOCH_S = 1788787800
EXPECTED = datetime(2026, 9, 7, 13, 30, tzinfo=timezone.utc)


class TestParseTimestamps:
    """The two sources send resets_at in different formats.

    The HTTP endpoint sends ISO-8601 with an offset; the status line sends a
    Unix epoch in seconds. Both must land on the same instant, or the tray and
    the status line would disagree about when a window rolls over.
    """

    def test_epoch_seconds(self):
        assert _parse_dt(EPOCH_S) == EXPECTED

    def test_epoch_milliseconds(self):
        assert _parse_dt(EPOCH_S * 1000) == EXPECTED

    def test_epoch_as_string(self):
        assert _parse_dt(str(EPOCH_S)) == EXPECTED

    def test_iso_with_offset(self):
        assert _parse_dt("2026-09-07T13:30:00+00:00") == EXPECTED

    def test_iso_with_zulu(self):
        assert _parse_dt("2026-09-07T13:30:00Z") == EXPECTED

    def test_iso_naive_is_assumed_utc(self):
        assert _parse_dt("2026-09-07T13:30:00") == EXPECTED

    def test_both_source_formats_agree(self):
        api = _parse_dt("2026-09-07T13:30:00.798800+00:00")
        statusline = _parse_dt(EPOCH_S)
        assert api.replace(microsecond=0) == statusline

    def test_result_is_always_tz_aware(self):
        for value in (EPOCH_S, "2026-09-07T13:30:00", "2026-09-07T13:30:00Z"):
            assert _parse_dt(value).tzinfo is not None

    @pytest.mark.parametrize("value", [None, 0, -5, True, False, "", "  ",
                                       "nonsense", [], {}, float("nan")])
    def test_junk_yields_none(self, value):
        assert _parse_dt(value) is None


class TestWindowUsage:
    def test_statusline_shape(self):
        """used_percentage + epoch resets_at, as the status line sends it."""
        window = WindowUsage.from_payload(
            {"used_percentage": 56.99999999999999, "resets_at": EPOCH_S})
        assert window.utilization == pytest.approx(57.0)
        assert window.resets_at == EXPECTED

    def test_endpoint_shape(self):
        window = WindowUsage.from_payload(
            {"utilization": 45.0, "resets_at": "2026-09-07T13:30:00.798800+00:00"})
        assert window.utilization == 45.0
        assert window.resets_at.replace(microsecond=0) == EXPECTED

    def test_limits_array_shape(self):
        window = WindowUsage.from_payload(
            {"kind": "session", "percent": 45, "severity": "normal",
             "is_active": True, "resets_at": EPOCH_S})
        assert window.utilization == 45.0
        assert window.severity == "normal"
        assert window.is_active is True

    def test_derived_from_used_and_limit(self):
        window = WindowUsage.from_payload({"used": 25, "limit": 100})
        assert window.utilization == 25.0
        assert window.derived is True

    def test_no_percentage_yields_none(self):
        assert WindowUsage.from_payload({"resets_at": EPOCH_S}) is None
        assert WindowUsage.from_payload(None) is None
        assert WindowUsage.from_payload("nope") is None

    def test_json_round_trip(self):
        original = WindowUsage(57.0, EXPECTED, raw_key_path="rate_limits.five_hour",
                               severity="normal", is_active=True)
        restored = WindowUsage.from_json(original.to_json())
        assert restored.utilization == original.utilization
        assert restored.resets_at == original.resets_at
        assert restored.severity == "normal"
        assert restored.is_active is True


class TestSpend:
    def test_minor_units_become_currency(self):
        spend = Spend.from_payload({
            "used": {"amount_minor": 3400, "currency": "USD", "exponent": 2},
            "limit": {"amount_minor": 5000, "exponent": 2},
            "percent": 68})
        assert spend.used_amount == 34.00
        assert spend.limit_amount == 50.0
        assert spend.percent == 68.0

    def test_extra_usage_credits_fallback(self):
        spend = Spend.from_payload(None, {
            "is_enabled": True, "monthly_limit": 5000, "used_credits": 3300.0,
            "utilization": 66.0, "decimal_places": 2, "currency": "USD"})
        assert spend.used_amount == 33.00
        assert spend.percent == 66.0

    def test_neither_source_yields_none(self):
        assert Spend.from_payload(None, None) is None
        assert Spend.from_payload({}, {}) is None


class TestSnapshotRoundTrip:
    def test_survives_json(self):
        original = Snapshot(
            captured_at=EXPECTED, source="api",
            windows={"five_hour": WindowUsage(57.0, EXPECTED),
                     "seven_day_opus": None},
            spend=Spend(percent=68.0, used_amount=34.00, limit_amount=50.0),
            account={"seat_tier": "example_tier"}, sources=["api"])
        restored = Snapshot.from_json(original.to_json())
        assert restored.source == "api"
        assert restored.captured_at == EXPECTED
        assert restored.windows["five_hour"].utilization == 57.0
        assert restored.windows["seven_day_opus"] is None
        assert restored.spend.used_amount == 34.00
        assert restored.account["seat_tier"] == "example_tier"

    def test_wrong_schema_is_refused(self):
        assert Snapshot.from_json({"schema": 999}) is None
        assert Snapshot.from_json(None) is None

    def test_null_window_is_not_zero(self):
        """A window we could not resolve must never read as 0% used."""
        snap = Snapshot(captured_at=EXPECTED, source="api",
                        windows={"seven_day_opus": None})
        assert snap.to_json()["windows"]["seven_day_opus"] is None
        assert snap.window("seven_day_opus") is None
