"""
models.py
=========
mailprobe が扱うデータクラス定義。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuthResult:
    spf_status: str = "unknown"
    spf_pass: bool = False
    spf_detail: str = ""
    spf_advice: str = ""

    dkim_status: str = "unknown"
    dkim_pass: bool = False
    dkim_detail: str = ""
    dkim_advice: str = ""

    dmarc_status: str = "unknown"
    dmarc_pass: bool = False
    dmarc_detail: str = ""
    dmarc_advice: str = ""

    all_passed: bool = False
    raw_auth_header: str = ""


@dataclass
class EncodingResult:
    ok: bool = True
    declared_charset: str = "unknown"
    charset_valid: bool = False
    charset_note: str = ""

    # charset_normalizer による推定
    detected_charset: str = ""
    detection_confidence: float = 0.0
    charset_match: bool = False  # 宣言と推定が一致しているか

    # 期待charsetとの照合 (config.expected_charset が空のときはスキップ)
    expected_charset: str = ""
    expected_charset_ok: bool = True  # 期待値未指定時は True 扱い

    plain_garbled: bool = False
    plain_score: int = 0
    plain_details: list[str] = field(default_factory=list)

    html_garbled: bool = False
    html_score: int = 0
    html_details: list[str] = field(default_factory=list)


@dataclass
class HtmlResult:
    ok: bool = True
    issue_count: int = 0
    issues: list[str] = field(default_factory=list)
    charset_in_meta: str = "なし"


@dataclass
class MtaResult:
    hostname: str = ""
    ip: str = ""
    tls_ok: bool = False
    tls_protocol: str = ""  # ESMTPS / ESMTP など
    tls_version: str = ""  # TLSv1.2 / TLSv1.3 など
    tls_cipher: str = ""
    raw_received: str = ""  # 解析に使った Received: ヘッダー原文


@dataclass
class DriveCompareResult:
    skipped: bool = True
    note: str = ""
    plain_match: bool = False
    html_match: bool = False
    similarity: float = 0.0
    error: str = ""
    diff_plain: str = ""  # NG時のみ設定 (unified diff)
    diff_html: str = ""  # NG時のみ設定 (unified diff)


@dataclass
class UnsubscribeResult:
    checked: bool = False  # check_unsubscribe が True のときのみ True
    ok: bool = False
    has_list_unsubscribe: bool = False
    has_list_unsubscribe_post: bool = False
    list_unsubscribe_value: str = ""
    list_unsubscribe_post_value: str = ""


@dataclass
class VerifyResult:
    message_id: str
    subject: str
    from_addr: str
    date: str
    overall_ok: bool = True
    provider: str = ""  # gmail / outlook / icloud など
    mta: MtaResult = field(default_factory=MtaResult)
    auth: AuthResult = field(default_factory=AuthResult)
    encoding: EncodingResult = field(default_factory=EncodingResult)
    html: HtmlResult = field(default_factory=HtmlResult)
    compare: DriveCompareResult = field(default_factory=DriveCompareResult)
    unsubscribe: UnsubscribeResult = field(default_factory=UnsubscribeResult)
    custom_headers: dict[str, str] = field(default_factory=dict)  # カスタムヘッダーの値 (記録のみ)
