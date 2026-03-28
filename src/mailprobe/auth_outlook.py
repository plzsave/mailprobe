"""
auth_outlook.py
===============
Outlook (Microsoft) の OAuth2 認証を行い、IMAP XOAUTH2 用のアクセストークンを返す。

フロー:
  1. outlook_token.json（MSALトークンキャッシュ）が存在 → サイレント更新を試みる
  2. キャッシュがない / 期限切れ → デバイスコードフロー（ブラウザ不要）
     → ターミナルに表示されたコードを https://aka.ms/devicelogin で入力
"""

from __future__ import annotations

import logging
from pathlib import Path

import msal

logger = logging.getLogger(__name__)

# IMAP アクセスに必要なスコープ
_SCOPES = ["https://outlook.office.com/IMAP.AccessAsUser.All"]

# personal + org どちらの Microsoft アカウントにも対応
_AUTHORITY = "https://login.microsoftonline.com/consumers"


def get_outlook_token(client_id: str, email: str, token_cache_path: str) -> str:
    """
    IMAP XOAUTH2 用のアクセストークンを返す。

    Parameters
    ----------
    client_id:
        Azure Portal で取得したアプリケーション (クライアント) ID
    email:
        認証する Outlook メールアドレス
    token_cache_path:
        トークンキャッシュファイルのパス (例: "outlook_token.json")
        存在しない場合は初回認証後に自動生成される
    """
    cache = msal.SerializableTokenCache()
    cache_file = Path(token_cache_path)
    if cache_file.exists():
        cache.deserialize(cache_file.read_text(encoding="utf-8"))

    app = msal.PublicClientApplication(
        client_id,
        authority=_AUTHORITY,
        token_cache=cache,
    )

    # キャッシュから取得を試みる
    accounts = app.get_accounts(username=email)
    result = None
    if accounts:
        result = app.acquire_token_silent(_SCOPES, account=accounts[0])

    # キャッシュになければデバイスコードフローで対話認証
    if not result:
        flow = app.initiate_device_flow(scopes=_SCOPES)
        if "error" in flow:
            raise RuntimeError(
                f"Outlook デバイスコードフロー開始に失敗しました: {flow.get('error_description', flow['error'])}"
            )
        # ユーザーにコードを表示 (例: "Enter the code XXXXXXXX at https://aka.ms/devicelogin")
        logger.info("[認証] Outlook: %s", flow["message"])
        result = app.acquire_token_by_device_flow(flow)  # ユーザーがコードを入力するまでブロック

    if "error" in result:
        raise RuntimeError(
            f"Outlook OAuth2 認証に失敗しました: {result.get('error_description', result['error'])}"
        )

    # キャッシュを保存（次回はサイレント更新）
    if cache.has_state_changed:
        cache_file.write_text(cache.serialize(), encoding="utf-8")
        logger.info("[認証] Outlook トークンを保存しました: %s", cache_file)

    return result["access_token"]


def build_xoauth2_bytes(email: str, access_token: str) -> bytes:
    """
    IMAP AUTHENTICATE XOAUTH2 用のペイロードバイト列を返す。

    imaplib.IMAP4.authenticate() は戻り値を自動でbase64エンコードするため、
    ここでは生のバイト列（エンコード前）を返す。
    """
    auth_string = f"user={email}\x01auth=Bearer {access_token}\x01\x01"
    return auth_string.encode("utf-8")
