"""
fetcher.py
==========
メール取得の抽象化レイヤー。

GmailFetcher  : Gmail API (format=raw) で生MIMEバイト列を取得
ImapFetcher   : IMAP FETCH RFC822 で生MIMEバイト列を取得 (Outlook / iCloud)
"""

from __future__ import annotations

import base64
import email.header
import imaplib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from mailprobe.auth_gmail import get_gmail_service
from mailprobe.auth_outlook import build_xoauth2_bytes, get_outlook_token
from mailprobe.config import Config

logger = logging.getLogger(__name__)

# =====================================================================
# プロバイダーヒント
# =====================================================================


@dataclass
class ImapProviderHints:
    """IMAP プロバイダー固有の受信ヘッダー解析ヒント。

    外部パッケージが独自プロバイダー（キャリアメール等）を実装する際に
    このクラスを使って verifier に解析ルールを注入できる。

    Attributes
    ----------
    mx_boundary_pattern:
        外部MTA境界を示す Received: ヘッダーの正規表現。
        Gmail境界が見つからない場合のフォールバックとして使用される。
        例: Outlook → r"\\bby\\s+\\S+\\.mail\\.protection\\.outlook\\.com\\b"
    tls_server_pattern:
        標準の "with ESMTPS" 形式を使わないサーバーの識別正規表現。
        例: Outlook → r"\\bMicrosoft SMTP Server\\b"
    tls_protocol_name:
        tls_server_pattern にマッチしたときに設定するプロトコル表示名。
        例: "Microsoft SMTP"
    tls_by_version:
        True のとき、"version=TLS..." フィールドの存在をTLS有りと判定する。
        Outlook のように with ESMTPS を使わないサーバー向け。
    """

    mx_boundary_pattern: str
    tls_server_pattern: str = ""
    tls_protocol_name: str = ""
    tls_by_version: bool = False


# Outlook 組み込みヒント (外部パッケージの実装参考にもなる)
OUTLOOK_HINTS = ImapProviderHints(
    mx_boundary_pattern=r"\bby\s+\S+\.(?:mail\.protection\.outlook|outlook)\.com\b",
    tls_server_pattern=r"\bMicrosoft SMTP Server\b",
    tls_protocol_name="Microsoft SMTP",
    tls_by_version=True,
)


# =====================================================================
# 検索条件
# =====================================================================


@dataclass
class SearchCriteria:
    subject: str = ""
    from_addr: str = ""
    after_date: str = ""
    before_date: str = ""
    max_results: int = 10

    @classmethod
    def from_config(cls, config: Config) -> SearchCriteria:
        return cls(
            subject=config.subject,
            from_addr=config.from_addr,
            after_date=config.after_date,
            before_date=config.before_date,
            max_results=config.max_results,
        )


# =====================================================================
# プロトコル
# =====================================================================


class MailFetcher(Protocol):
    def fetch_messages(self, criteria: SearchCriteria) -> list[bytes]:
        """検索条件に合致するメールを生MIMEバイト列のリストで返す"""
        ...


# =====================================================================
# Gmail 実装
# =====================================================================


class GmailFetcher:
    """Gmail API (format=raw) で生MIMEバイト列を取得する"""

    def __init__(self, config: Config) -> None:
        self._service = get_gmail_service(config)

    def fetch_messages(self, criteria: SearchCriteria) -> list[bytes]:
        query_parts = []
        if criteria.subject:
            query_parts.append(f'subject:"{criteria.subject}"')
        if criteria.from_addr:
            query_parts.append(f"from:{criteria.from_addr}")
        if criteria.after_date:
            query_parts.append(f"after:{criteria.after_date}")
        if criteria.before_date:
            query_parts.append(f"before:{criteria.before_date}")

        query = " ".join(query_parts)
        logger.info("[検索] Gmail query: %s", query)

        result = (
            self._service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=criteria.max_results,
            )
            .execute()
        )
        msg_list = result.get("messages", [])

        raw_messages = []
        for m in msg_list:
            raw = (
                self._service.users()
                .messages()
                .get(userId="me", id=m["id"], format="raw")
                .execute()
            )
            raw_messages.append(base64.urlsafe_b64decode(raw["raw"]))

        return raw_messages


# =====================================================================
# IMAP 実装
# =====================================================================

# IMAP の月名 (datetime.strftime の %b はロケール依存のため固定で定義)
_IMAP_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _decode_header_subject(header_bytes: bytes) -> str:
    """BODY[HEADER.FIELDS (SUBJECT)] のバイト列から件名文字列を返す。"""
    # "Subject: =?UTF-8?B?...?=\r\n" 形式をデコード
    text = header_bytes.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if line.lower().startswith("subject:"):
            raw = line[len("subject:") :].strip()
            parts = email.header.decode_header(raw)
            decoded = []
            for part, charset in parts:
                if isinstance(part, bytes):
                    decoded.append(part.decode(charset or "utf-8", errors="replace"))
                else:
                    decoded.append(part)
            return "".join(decoded)
    return ""


def _to_imap_date(date_str: str) -> str:
    """YYYY/MM/DD → DD-Mon-YYYY (IMAP SEARCH 形式) に変換する。
    変換に失敗した場合はそのまま返す。
    """
    try:
        dt = datetime.strptime(date_str, "%Y/%m/%d")
        return f"{dt.day:02d}-{_IMAP_MONTHS[dt.month - 1]}-{dt.year}"
    except ValueError:
        return date_str


class ImapFetcher:
    """IMAP FETCH RFC822 で生MIMEバイト列を取得する (Outlook / iCloud)"""

    HOSTS: dict[str, str] = {  # noqa: RUF012
        "outlook": "outlook.office365.com",
        "icloud": "imap.mail.me.com",
    }

    # プロバイダー名 → 組み込みヒントのマッピング。
    # 外部パッケージは ImapFetcher(provider_hints=...) で独自ヒントを渡せる。
    HINTS: dict[str, ImapProviderHints] = {  # noqa: RUF012
        "outlook": OUTLOOK_HINTS,
    }

    def __init__(
        self,
        provider: str,
        email_addr: str,
        app_password: str = "",
        client_id: str = "",
        token_cache_path: str = "outlook_token.json",
        provider_hints: ImapProviderHints | None = None,
        imap_host: str = "",
    ) -> None:
        if imap_host:
            # 外部パッケージが独自プロバイダーを登録する場合はホストを直接指定
            self._host = imap_host
        elif provider in self.HOSTS:
            self._host = self.HOSTS[provider]
        else:
            raise ValueError(
                f"未対応のプロバイダー: {provider!r}。対応プロバイダー: {list(self.HOSTS)}"
                " または imap_host で直接ホストを指定してください。"
            )
        self._provider = provider
        self._email = email_addr
        self._password = app_password
        self._client_id = client_id
        self._token_cache_path = token_cache_path
        # 明示的に渡されなければ組み込みヒントを使う
        self.provider_hints: ImapProviderHints | None = (
            provider_hints if provider_hints is not None else self.HINTS.get(provider)
        )

    def fetch_messages(self, criteria: SearchCriteria) -> list[bytes]:
        # OAuth2 (Outlook) の場合は先にトークンを取得してからIMAP接続
        access_token: str = ""
        if self._client_id:
            access_token = get_outlook_token(self._client_id, self._email, self._token_cache_path)

        with imaplib.IMAP4_SSL(self._host) as imap:
            if self._client_id:
                imap.authenticate(
                    "XOAUTH2",
                    lambda _: build_xoauth2_bytes(self._email, access_token),
                )
            else:
                imap.login(self._email, self._password)
            imap.select("INBOX")

            search_parts = []
            if criteria.subject:
                search_parts.append(f'SUBJECT "{criteria.subject}"')
            if criteria.from_addr:
                search_parts.append(f'FROM "{criteria.from_addr}"')
            if criteria.after_date:
                search_parts.append(f'SINCE "{_to_imap_date(criteria.after_date)}"')
            if criteria.before_date:
                search_parts.append(f'BEFORE "{_to_imap_date(criteria.before_date)}"')

            search_query = "(" + " ".join(search_parts) + ")" if search_parts else "ALL"
            logger.info("[検索] IMAP (%s) query: %s", self._host, search_query)

            imap._encoding = "utf-8"  # type: ignore[attr-defined]
            subject_client_filter = ""
            try:
                _, data = imap.search("UTF-8", search_query)
            except imaplib.IMAP4.error as e:
                # CHARSET UTF-8 非対応サーバー (Outlookなど) は BAD を返す。
                # それ以外のエラー (認証失敗・接続断など) は再送出する。
                if "BAD" not in str(e).upper() and "CHARSET" not in str(e).upper():
                    raise
                # CHARSET UTF-8 非対応 (Outlook等): ASCII安全な条件のみでSEARCH
                # 非ASCII件名はヘッダー取得後にPython側でフィルタ
                ascii_parts = []
                if criteria.subject and criteria.subject.isascii():
                    ascii_parts.append(f'SUBJECT "{criteria.subject}"')
                elif criteria.subject:
                    subject_client_filter = criteria.subject
                if criteria.from_addr and criteria.from_addr.isascii():
                    ascii_parts.append(f'FROM "{criteria.from_addr}"')
                if criteria.after_date:
                    ascii_parts.append(f'SINCE "{_to_imap_date(criteria.after_date)}"')
                if criteria.before_date:
                    ascii_parts.append(f'BEFORE "{_to_imap_date(criteria.before_date)}"')
                imap._encoding = "ascii"  # type: ignore[attr-defined]
                ascii_query = "(" + " ".join(ascii_parts) + ")" if ascii_parts else "ALL"
                logger.info("[検索] IMAP fallback query: %s", ascii_query)
                _, data = imap.search(None, ascii_query)

            msg_ids = data[0].split()
            if not msg_ids:
                return []

            # max_results 件に絞る (新着優先: リスト末尾から取得)
            msg_ids = msg_ids[-criteria.max_results :]

            raw_messages = []
            for msg_id in msg_ids:
                # 件名クライアントフィルタが必要な場合はヘッダーのみ先に取得して照合
                if subject_client_filter:
                    _, hdr_data = imap.fetch(msg_id, "(BODY[HEADER.FIELDS (SUBJECT)])")
                    subject_bytes = b""
                    for part in hdr_data or []:
                        if (
                            isinstance(part, tuple)
                            and len(part) >= 2
                            and isinstance(part[1], bytes)
                        ):
                            subject_bytes = part[1]
                            break
                    decoded_subject = _decode_header_subject(subject_bytes)
                    if subject_client_filter not in decoded_subject:
                        continue

                _, msg_data = imap.fetch(msg_id, "(BODY[])")
                for part in msg_data or []:
                    if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
                        raw_messages.append(part[1])
                        break

            return raw_messages
