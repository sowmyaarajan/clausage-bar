from datetime import datetime, timedelta, timezone

from clausage_bar import config, merge
from clausage_bar.model import Snapshot, WindowUsage
from clausage_bar.provider_statusline import StatuslineProvider

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def make(source, age_s, five=40.0, seven=10.0):
    return Snapshot(
        captured_at=NOW - timedelta(seconds=age_s),
        source=source,
        windows={"five_hour": WindowUsage(five, NOW + timedelta(hours=2)),
                 "seven_day": WindowUsage(seven, NOW + timedelta(days=4))},
    )


class TestPrecedence:
    def test_fresh_api_wins(self):
        chosen = merge.choose(make("api", 10), make("statusline", 1), None, NOW)
        assert chosen.source == "api"

    def test_stale_api_yields_to_newer_statusline(self):
        api = make("api", config.API_PREFERRED_S + 60)
        chosen = merge.choose(api, make("statusline", 30), None, NOW)
        assert chosen.source == "statusline"

    def test_stale_api_still_wins_over_older_statusline(self):
        api = make("api", config.API_PREFERRED_S + 60)
        chosen = merge.choose(api, make("statusline", 9999), None, NOW)
        assert chosen.source == "api"

    def test_statusline_only(self):
        chosen = merge.choose(None, make("statusline", 30), None, NOW)
        assert chosen.source == "statusline"

    def test_falls_back_to_cache(self):
        chosen = merge.choose(None, None, make("cache", 4000), NOW)
        assert chosen.source == "cache"

    def test_nothing_at_all(self):
        assert merge.choose(None, None, None, NOW) is None

    def test_records_every_source_seen(self):
        chosen = merge.choose(make("api", 10), make("statusline", 20), None, NOW)
        assert chosen.sources == ["api", "statusline"]


class TestCrossCheck:
    def test_agreement_is_quiet(self, caplog):
        merge._warned.clear()
        with caplog.at_level("WARNING"):
            merge.choose(make("api", 10, five=40.0),
                         make("statusline", 10, five=41.0), None, NOW)
        assert "disagreement" not in caplog.text

    def test_disagreement_is_logged_once(self, caplog):
        merge._warned.clear()
        with caplog.at_level("WARNING"):
            merge.choose(make("api", 10, five=40.0),
                         make("statusline", 10, five=75.0), None, NOW)
        assert "disagreement" in caplog.text

        caplog.clear()
        with caplog.at_level("WARNING"):
            merge.choose(make("api", 10, five=40.0),
                         make("statusline", 10, five=75.0), None, NOW)
        assert "disagreement" not in caplog.text

    def test_stale_sources_are_not_compared(self, caplog):
        merge._warned.clear()
        with caplog.at_level("WARNING"):
            merge.choose(make("api", 10, five=40.0),
                         make("statusline", 99999, five=90.0), None, NOW)
        assert "disagreement" not in caplog.text


class TestStatuslineProvider:
    def _write(self, tmp_path, payload):
        import json
        path = tmp_path / "state.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_reads_collector_output(self, tmp_path):
        path = self._write(tmp_path, {
            "schema": 1,
            "captured_at": "2026-09-07T11:59:30+00:00",
            "source": "statusline",
            "windows": {
                "five_hour": {"utilization": 41.0,
                              "resets_at": "2026-09-07T14:00:00+00:00",
                              "raw_key_path": "rate_limits.five_hour"},
                "seven_day": {"utilization": 64.0, "resets_at": None},
                "seven_day_opus": None,
                "spend_limit": None,
            },
            "model": "Opus 5",
        })
        snap = StatuslineProvider(path=path).read()
        assert snap.source == "statusline"
        assert snap.windows["five_hour"].utilization == 41.0
        assert snap.windows["seven_day"].utilization == 64.0
        assert snap.model_name == "Opus 5"
        assert "spend_limit" not in snap.windows

    def test_missing_file_yields_none(self, tmp_path):
        assert StatuslineProvider(path=tmp_path / "nope.json").read() is None

    def test_torn_read_keeps_previous_value(self, tmp_path):
        path = self._write(tmp_path, {
            "schema": 1, "captured_at": "2026-09-07T11:59:30+00:00",
            "windows": {"five_hour": {"utilization": 41.0}},
        })
        provider = StatuslineProvider(path=path)
        assert provider.read().windows["five_hour"].utilization == 41.0

        path.write_text('{"schema": 1, "wind', encoding="utf-8")
        assert provider.read().windows["five_hour"].utilization == 41.0

    def test_unknown_schema_is_refused(self, tmp_path):
        path = self._write(tmp_path, {"schema": 99, "windows": {}})
        assert StatuslineProvider(path=path).read() is None

    def test_payload_without_rate_limits_is_refused(self, tmp_path):
        """The degraded case: the status line gave us no rate limits at all."""
        path = self._write(tmp_path, {
            "schema": 1, "captured_at": "2026-09-07T11:59:30+00:00",
            "windows": {"five_hour": None, "seven_day": None},
            "probe": {"rate_limits_present": False,
                      "top_level_keys": ["model", "context_window"],
                      "fallback_hits": []},
        })
        assert StatuslineProvider(path=path).read() is None
