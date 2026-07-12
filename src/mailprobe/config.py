"""
config.py
=========
config.yaml を読み込み、設定値を提供する。
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ProviderConfig:
    enabled: bool = False
    email: str = ""
    app_password: str = ""
    client_id: str = ""  # Outlook OAuth2 用 Azure AD アプリケーション ID


@dataclass
class Config:
    # 検索条件
    subject: str = ""
    from_addr: str = ""
    after_date: str = ""
    before_date: str = ""
    max_results: int = 10

    # パス
    credentials_path: str = "credentials.json"
    token_path: str = "token.json"
    outlook_token_path: str = "outlook_token.json"
    originals_dir: str = "originals"
    results_dir: str = "results"

    # 文字化け判定閾値
    garbled_threshold: int = 3

    # 期待するcharset (空文字列のときはチェックしない)
    # 例: "utf-8" / "iso-2022-jp" / "shift_jis"
    expected_charset: str = ""

    # 期待するMTA台数 (0のときはチェックしない)
    expected_mta_count: int = 0

    # 購読解除ヘッダーチェック (False のときはスキップ)
    check_unsubscribe: bool = False

    # 記録するカスタムヘッダー名のリスト (空リストで記録しない)
    # 例: ["X-Campaign-ID", "X-Mailer-ID"]
    custom_headers: list[str] = field(default_factory=list)

    # プロバイダー設定 (providers: セクション)
    # 未設定時は Gmail のみ有効
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    # Slack Incoming Webhook URL (--notify で使用。空文字列なら未設定)
    slack_webhook_url: str = ""

    @classmethod
    def load(cls, path: str = "config.yaml") -> Config:
        config_file = Path(path)
        if not config_file.exists():
            raise FileNotFoundError(
                f"config.yaml が見つかりません: {config_file.resolve()}\n"
                "config.yaml.example をコピーして設定してください。"
            )
        with config_file.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        c = cls()
        search = data.get("search", {})
        c.subject = search.get("subject", "")
        c.from_addr = search.get("from", "")
        c.after_date = search.get("after_date", "")
        c.before_date = search.get("before_date", "")
        c.max_results = search.get("max_results", 10)

        paths = data.get("paths", {})
        c.credentials_path = paths.get("credentials", "credentials.json")
        c.token_path = paths.get("token", "token.json")
        c.outlook_token_path = paths.get("outlook_token", "outlook_token.json")
        c.originals_dir = paths.get("originals", "originals")
        c.results_dir = paths.get("results", "results")

        c.garbled_threshold = data.get("garbled_threshold", 3)
        c.expected_charset = search.get("expected_charset", "")
        c.expected_mta_count = search.get("expected_mta_count", 0)
        c.check_unsubscribe = data.get("check_unsubscribe", False)
        c.custom_headers = data.get("custom_headers", []) or []

        providers_data = data.get("providers", {})
        c.providers = {}
        for name, pdata in (providers_data or {}).items():
            if isinstance(pdata, dict):
                env_key = f"MAILPROBE_{name.upper()}_APP_PASSWORD"
                c.providers[name] = ProviderConfig(
                    enabled=pdata.get("enabled", False),
                    email=pdata.get("email", ""),
                    app_password=pdata.get("app_password", "") or os.environ.get(env_key, ""),
                    client_id=pdata.get("client_id", ""),
                )
        # providers: セクションが未記載でも gmail は常に利用可能
        if "gmail" not in c.providers:
            c.providers["gmail"] = ProviderConfig(enabled=True)

        notify = data.get("notify", {}) or {}
        c.slack_webhook_url = notify.get("slack_webhook_url", "") or os.environ.get(
            "MAILPROBE_SLACK_WEBHOOK_URL", ""
        )

        # バリデーション
        if c.max_results < 1:
            raise ValueError(
                f"config.yaml: search.max_results は1以上を指定してください (現在: {c.max_results})"
            )
        if c.garbled_threshold < 0:
            raise ValueError(
                f"config.yaml: garbled_threshold は0以上を指定してください (現在: {c.garbled_threshold})"
            )
        if c.expected_mta_count < 0:
            raise ValueError(
                f"config.yaml: search.expected_mta_count は0以上を指定してください (現在: {c.expected_mta_count})"
            )

        return c

    def with_overrides(self, row: dict) -> Config:
        """CSV/TSVの1行をsearch関連フィールドに上書きした新しいConfigを返す。

        空文字列・未指定のフィールドは config.yaml の値をそのまま引き継ぐ。
        パス系フィールド (credentials_path 等) は上書きしない。
        """
        c = copy.copy(self)
        str_fields = {
            "subject": "subject",
            "from_addr": "from_addr",
            "from": "from_addr",  # CSVヘッダーの揺れを吸収
            "after_date": "after_date",
            "before_date": "before_date",
            "expected_charset": "expected_charset",
        }
        int_fields = {
            "max_results": "max_results",
            "expected_mta_count": "expected_mta_count",
        }
        for csv_key, attr in str_fields.items():
            val = row.get(csv_key, "").strip()
            if val:
                setattr(c, attr, val)
        for csv_key, attr in int_fields.items():
            val = row.get(csv_key, "").strip()
            if val:
                try:
                    setattr(c, attr, int(val))
                except ValueError:
                    raise ValueError(
                        f"条件ファイルの {csv_key!r} に整数以外の値が指定されています: {val!r}"
                    ) from None
        # bool フィールド: "true" / "false" (大文字小文字不問)。空欄は config.yaml の値を引き継ぐ
        val = row.get("check_unsubscribe", "").strip().lower()
        if val in ("true", "false"):
            c.check_unsubscribe = val == "true"
        return c
