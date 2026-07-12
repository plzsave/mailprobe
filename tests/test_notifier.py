"""
tests/test_notifier.py
======================
Slack通知と横断サマリー組み立てのユニットテスト

対象:
  - build_batch_summary (all_ok 判定を中心に)
  - build_slack_payload
  - post_slack (urlopen をモック)
  - Config.load の notify セクション読み込み
"""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from mailprobe.__main__ import entry
from mailprobe.config import Config
from mailprobe.models import MtaResult, VerifyResult
from mailprobe.notifier import build_slack_payload, post_slack
from mailprobe.reporter import BatchSummary, build_batch_summary

# =====================================================================
# テスト用ヘルパー
# =====================================================================


def _make_result(*, overall_ok: bool = True, hostname: str = "mail.example.com") -> VerifyResult:
    return VerifyResult(
        message_id="abc123",
        subject="テストメール",
        from_addr="sender@example.com",
        date="Thu, 20 Mar 2025 10:00:00 +0900",
        overall_ok=overall_ok,
        mta=MtaResult(hostname=hostname, tls_ok=overall_ok),
    )


def _make_summary(*, all_ok: bool = True, ng_count: int = 0) -> BatchSummary:
    return BatchSummary(
        lines=["  ✅  条件A  MTA:3/3台 ✅  NG:0件", "", "  全体: 1条件 / 3件検証 / 0件NG"],
        condition_count=1,
        result_count=3,
        ng_count=ng_count,
        all_ok=all_ok,
    )


# =====================================================================
# build_batch_summary
# =====================================================================


class TestBuildBatchSummary:
    def test_all_ok(self):
        batch = [("条件A", [_make_result()], Config())]
        summary = build_batch_summary(batch)
        assert summary.all_ok is True
        assert summary.condition_count == 1
        assert summary.result_count == 1
        assert summary.ng_count == 0

    def test_ng_result_makes_all_ok_false(self):
        batch = [("条件A", [_make_result(overall_ok=False)], Config())]
        summary = build_batch_summary(batch)
        assert summary.all_ok is False
        assert summary.ng_count == 1

    def test_missing_mta_makes_all_ok_false(self):
        """NGが0件でも期待MTA台数に満たなければ all_ok は False"""
        batch = [("条件A", [_make_result()], Config(expected_mta_count=3))]
        summary = build_batch_summary(batch)
        assert summary.ng_count == 0
        assert summary.all_ok is False

    def test_lines_match_terminal_output(self):
        """lines がターミナル表示の本文と同じ内容を持つ"""
        batch = [("条件A", [_make_result(overall_ok=False, hostname="bad.example.com")], Config())]
        summary = build_batch_summary(batch)
        text = "\n".join(summary.lines)
        assert "条件A" in text
        assert "bad.example.com" in text
        assert "全体: 1条件 / 1件検証 / 1件NG" in text

    def test_multiple_conditions_totals(self):
        batch = [
            ("条件A", [_make_result(), _make_result()], Config()),
            ("条件B", [_make_result(overall_ok=False)], Config()),
        ]
        summary = build_batch_summary(batch)
        assert summary.condition_count == 2
        assert summary.result_count == 3
        assert summary.ng_count == 1
        assert summary.all_ok is False


# =====================================================================
# build_slack_payload
# =====================================================================


class TestBuildSlackPayload:
    def test_ok_headline(self):
        payload = build_slack_payload(_make_summary(all_ok=True))
        assert payload["text"].startswith("✅ mailprobe 検証結果: 1条件 / 3件検証 / NG 0件")

    def test_ng_headline(self):
        payload = build_slack_payload(_make_summary(all_ok=False, ng_count=2))
        assert payload["text"].startswith("❌ mailprobe 検証結果:")
        assert "NG 2件" in payload["text"]

    def test_body_in_code_block(self):
        payload = build_slack_payload(_make_summary())
        assert "```" in payload["text"]
        assert "条件A" in payload["text"]

    def test_mrkdwn_escaped(self):
        """本文中の & < > がエスケープされる"""
        summary = _make_summary()
        summary.lines = ["  ❌  <条件A&B>  NG:1件"]
        payload = build_slack_payload(summary)
        assert "&lt;条件A&amp;B&gt;" in payload["text"]
        assert "<条件A" not in payload["text"]


# =====================================================================
# post_slack
# =====================================================================


def _mock_response(status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestPostSlack:
    WEBHOOK = "https://hooks.slack.com/services/T00/B00/XXX"

    def test_success_returns_true(self):
        with patch("urllib.request.urlopen", return_value=_mock_response(200)) as mock_open:
            assert post_slack(self.WEBHOOK, {"text": "hi"}) is True
        req = mock_open.call_args[0][0]
        assert req.full_url == self.WEBHOOK
        assert req.get_header("Content-type") == "application/json"

    def test_http_error_returns_false(self):
        err = urllib.error.HTTPError(
            url=self.WEBHOOK, code=404, msg="Not Found", hdrs=None, fp=None
        )
        err.read = MagicMock(return_value=b"no_service")
        with patch("urllib.request.urlopen", side_effect=err):
            assert post_slack(self.WEBHOOK, {"text": "hi"}) is False

    def test_url_error_returns_false(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("dns")):
            assert post_slack(self.WEBHOOK, {"text": "hi"}) is False

    def test_non_2xx_status_returns_false(self):
        with patch("urllib.request.urlopen", return_value=_mock_response(302)):
            assert post_slack(self.WEBHOOK, {"text": "hi"}) is False


# =====================================================================
# entry: --notify / --fail-on-ng
# =====================================================================


class TestEntryFlags:
    def _run_entry(self, argv: list[str], summary: BatchSummary, config: Config):
        with (
            patch("sys.argv", ["mailprobe", *argv]),
            patch("mailprobe.__main__.Config.load", return_value=config),
            patch("mailprobe.__main__.run_single", return_value=summary),
        ):
            entry()

    def test_fail_on_ng_exits_2(self):
        with pytest.raises(SystemExit) as exc_info:
            self._run_entry(["--fail-on-ng"], _make_summary(all_ok=False, ng_count=1), Config())
        assert exc_info.value.code == 2

    def test_fail_on_ng_all_ok_exits_normally(self):
        self._run_entry(["--fail-on-ng"], _make_summary(all_ok=True), Config())

    def test_notify_without_webhook_exits_1(self):
        with pytest.raises(SystemExit) as exc_info:
            self._run_entry(["--notify"], _make_summary(), Config(slack_webhook_url=""))
        assert exc_info.value.code == 1

    def test_notify_posts_summary(self):
        config = Config(slack_webhook_url="https://hooks.slack.com/services/T00/B00/XXX")
        with patch("mailprobe.__main__.post_slack", return_value=True) as mock_post:
            self._run_entry(["--notify"], _make_summary(), config)
        assert mock_post.call_count == 1
        assert mock_post.call_args[0][0] == config.slack_webhook_url

    def test_notify_failure_exits_1(self):
        config = Config(slack_webhook_url="https://hooks.slack.com/services/T00/B00/XXX")
        with (
            patch("mailprobe.__main__.post_slack", return_value=False),
            pytest.raises(SystemExit) as exc_info,
        ):
            self._run_entry(["--notify"], _make_summary(), config)
        assert exc_info.value.code == 1


# =====================================================================
# Config: notify セクション
# =====================================================================


class TestNotifyConfig:
    def test_webhook_from_yaml(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MAILPROBE_SLACK_WEBHOOK_URL", raising=False)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            'notify:\n  slack_webhook_url: "https://hooks.slack.com/services/T00/B00/YAML"\n',
            encoding="utf-8",
        )
        config = Config.load(str(config_file))
        assert config.slack_webhook_url == "https://hooks.slack.com/services/T00/B00/YAML"

    def test_webhook_from_env_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "MAILPROBE_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T00/B00/ENV"
        )
        config_file = tmp_path / "config.yaml"
        config_file.write_text("search:\n  subject: test\n", encoding="utf-8")
        config = Config.load(str(config_file))
        assert config.slack_webhook_url == "https://hooks.slack.com/services/T00/B00/ENV"

    def test_webhook_default_empty(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MAILPROBE_SLACK_WEBHOOK_URL", raising=False)
        config_file = tmp_path / "config.yaml"
        config_file.write_text("search:\n  subject: test\n", encoding="utf-8")
        config = Config.load(str(config_file))
        assert config.slack_webhook_url == ""
