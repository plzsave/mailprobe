"""
notifier.py
===========
検証結果サマリーの外部通知を担う。

現在は Slack Incoming Webhook のみ対応。
標準ライブラリ (urllib) のみで実装し、追加依存を持たない。

  build_slack_payload() : BatchSummary から Slack 送信ペイロードを組み立てる
  post_slack()          : Incoming Webhook にペイロードを POST する
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from mailprobe.reporter import BatchSummary

logger = logging.getLogger(__name__)


def _escape_mrkdwn(text: str) -> str:
    """Slack mrkdwn の制御文字 (& < >) をエスケープする"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_slack_payload(summary: BatchSummary) -> dict:
    """BatchSummary から Slack Incoming Webhook の送信ペイロードを組み立てる。

    1行目は通知プレビューにも表示されるため、結果の要約を先頭に置く。
    詳細はコードブロックで送り、ターミナル表示と同じ整形を保つ。
    """
    icon = "✅" if summary.all_ok else "❌"
    headline = (
        f"{icon} mailprobe 検証結果: "
        f"{summary.condition_count}条件 / {summary.result_count}件検証 / NG {summary.ng_count}件"
    )
    body = _escape_mrkdwn("\n".join(summary.lines))
    return {"text": f"{headline}\n```\n{body}\n```"}


def post_slack(webhook_url: str, payload: dict) -> bool:
    """Slack Incoming Webhook にペイロードを POST する。成功したら True を返す。

    通知失敗で検証結果 (JSON / CSV) が失われるわけではないため例外は投げず、
    ログ出力して False を返す。終了コードの扱いは呼び出し側の責務とする。
    """
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        logger.error("[エラー] Slack通知に失敗しました (HTTP %d): %s", e.code, detail)
        return False
    except (urllib.error.URLError, OSError) as e:
        logger.error("[エラー] Slack通知に失敗しました: %s", e)
        return False

    if not 200 <= status < 300:
        logger.error("[エラー] Slack通知に失敗しました (HTTP %d)", status)
        return False
    return True
