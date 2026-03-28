"""
auth_gmail.py
=============
Gmail API の OAuth2 認証を行い、サービスオブジェクトを返す。

初回実行時はブラウザが開き Google アカウントでログインする。
認証トークンは token.json にローカル保存され、2回目以降は自動認証される。
"""

from __future__ import annotations

import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from mailprobe.config import Config

logger = logging.getLogger(__name__)

# 必要なスコープ (read-only で十分)
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_gmail_service(config: Config):
    """
    OAuth2 認証を行い Gmail サービスオブジェクトを返す。

    初回実行時: ブラウザが開き Googleアカウントでログイン → token.json 保存
    2回目以降: token.json を使って自動認証
    """
    creds_path = Path(config.credentials_path)  # credentials.json
    token_path = Path(config.token_path)  # token.json (初回後自動生成)

    if not creds_path.exists():
        raise FileNotFoundError(
            f"credentials.json が見つかりません: {creds_path}\n"
            "Google Cloud Console から OAuth クライアント ID をダウンロードして配置してください。\n"
            "詳細: README.md の「セットアップ手順」を参照"
        )

    creds = None

    # 既存 token.json の読み込み
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    # token が無効 or 期限切れ → 再認証
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # refresh token で自動更新 (ブラウザ不要)
            creds.refresh(Request())
        else:
            # 初回: ブラウザを開いて認証
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)

        # token を保存 (次回以降はブラウザ不要)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        logger.info("[認証] token を保存しました: %s", token_path)

    service = build("gmail", "v1", credentials=creds)
    logger.info("[認証] Gmail API 接続OK (token: %s)", token_path)
    return service
