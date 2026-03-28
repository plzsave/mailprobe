"""
analyze_received のテスト

Received: ヘッダーから MTA ホスト名・IP・TLS 情報を正しく抽出できるか検証する。
"""

from __future__ import annotations

import email
import email.policy
from email.message import Message

from mailprobe.verifier import analyze_received


def _make_msg(*received_headers: str) -> Message:
    """Received: ヘッダーを持つ最小限のメッセージを生成する。
    引数は新着順（先頭が最新）で渡す。
    """
    lines = ["From: sender@example.com"]
    for h in received_headers:
        # 複数行ヘッダーは折り畳みをそのまま維持
        lines.append(f"Received: {h}")
    lines += ["", "Body"]
    return email.message_from_string("\r\n".join(lines), policy=email.policy.compat32)


# =====================================================================
# TLS 有無
# =====================================================================


class TestTlsDetection:
    _GOOGLE_ESMTPS = (
        "from mail.example.com (mail.example.com [203.0.113.1])"
        " by mx.google.com with ESMTPS id abc123"
        " (version=TLSv1.3 cipher=TLS_AES_256_GCM_SHA384)"
    )
    _GOOGLE_ESMTP = (
        "from mail.example.com (mail.example.com [203.0.113.1])"
        " by mx.google.com with ESMTP id abc123"
    )
    _GOOGLE_ESMTPSA = (
        "from mail.example.com (mail.example.com [203.0.113.1])"
        " by mx.google.com with ESMTPSA id abc123"
        " (version=TLSv1.2 cipher=ECDHE-RSA-AES256-GCM-SHA384)"
    )
    _GOOGLE_SMTPS = (
        "from mail.example.com (mail.example.com [203.0.113.1])"
        " by smtp.gmail.com with SMTPS id abc123"
        " (version=TLSv1.3 cipher=TLS_AES_128_GCM_SHA256)"
    )

    def test_esmtps_is_tls_ok(self):
        result = analyze_received(_make_msg(self._GOOGLE_ESMTPS))
        assert result.tls_ok is True
        assert result.tls_protocol == "ESMTPS"

    def test_esmtp_is_not_tls(self):
        result = analyze_received(_make_msg(self._GOOGLE_ESMTP))
        assert result.tls_ok is False
        assert result.tls_protocol == "ESMTP"

    def test_esmtpsa_is_tls_ok(self):
        result = analyze_received(_make_msg(self._GOOGLE_ESMTPSA))
        assert result.tls_ok is True
        assert result.tls_protocol == "ESMTPSA"

    def test_smtps_is_tls_ok(self):
        result = analyze_received(_make_msg(self._GOOGLE_SMTPS))
        assert result.tls_ok is True
        assert result.tls_protocol == "SMTPS"


# =====================================================================
# TLS バージョン・暗号スイート
# =====================================================================


class TestTlsVersionAndCipher:
    def test_tlsv13_extracted(self):
        header = (
            "from mail.example.com (mail.example.com [203.0.113.1])"
            " by mx.google.com with ESMTPS id abc123"
            " (version=TLSv1.3 cipher=TLS_AES_256_GCM_SHA384)"
        )
        result = analyze_received(_make_msg(header))
        assert result.tls_version == "TLSv1.3"
        assert result.tls_cipher == "TLS_AES_256_GCM_SHA384"

    def test_tlsv12_extracted(self):
        header = (
            "from mail.example.com (mail.example.com [203.0.113.1])"
            " by mx.google.com with ESMTPS id abc123"
            " (version=TLSv1.2 cipher=ECDHE-RSA-AES256-GCM-SHA384)"
        )
        result = analyze_received(_make_msg(header))
        assert result.tls_version == "TLSv1.2"

    def test_tls1_3_notation_normalized(self):
        """TLS1_3 形式は TLSv1.3 に正規化される"""
        header = (
            "from mail.example.com (mail.example.com [203.0.113.1])"
            " by mx.google.com with ESMTPS id abc123"
            " (version=TLS1_3 cipher=TLS_AES_256_GCM_SHA384)"
        )
        result = analyze_received(_make_msg(header))
        assert result.tls_version == "TLSv1.3"

    def test_no_version_or_cipher_when_absent(self):
        header = (
            "from mail.example.com (mail.example.com [203.0.113.1])"
            " by mx.google.com with ESMTPS id abc123"
        )
        result = analyze_received(_make_msg(header))
        assert result.tls_version == ""
        assert result.tls_cipher == ""


# =====================================================================
# ホスト名・IP 抽出
# =====================================================================


class TestHostAndIp:
    def test_ipv4_extracted(self):
        header = (
            "from mail.example.com (mail.example.com [203.0.113.1])"
            " by mx.google.com with ESMTPS id abc123"
        )
        result = analyze_received(_make_msg(header))
        assert result.hostname == "mail.example.com"
        assert result.ip == "203.0.113.1"

    def test_ipv6_extracted(self):
        header = (
            "from mail.example.com (mail.example.com [IPv6:2001:db8::1])"
            " by mx.google.com with ESMTPS id abc123"
        )
        result = analyze_received(_make_msg(header))
        assert result.hostname == "mail.example.com"
        assert result.ip == "2001:db8::1"


# =====================================================================
# 複数 Received ヘッダーの選択ロジック
# =====================================================================


class TestReceivedHeaderSelection:
    # Gmail 内部ホップ（最新）
    _GOOGLE_INTERNAL = "from 2002:a05:6214:a::0 by 2002:a05:6214:a::0 with SMTP id internal123"
    # Gmail 外部受信境界（外部 MTA → Gmail）
    _GOOGLE_BOUNDARY = (
        "from mail.example.com (mail.example.com [203.0.113.1])"
        " by mx.google.com with ESMTPS id boundary123"
        " (version=TLSv1.3 cipher=TLS_AES_256_GCM_SHA384)"
    )
    # 送信側 MTA の内部ホップ（最古）
    _SENDER_INTERNAL = (
        "from internal.example.com (internal.example.com [192.168.1.1])"
        " by mail.example.com with ESMTP id sender123"
    )

    def test_gmail_boundary_is_selected(self):
        """Google MX が複数あるとき最古（外部 MTA との境界）を選ぶ"""
        # 新着順で渡す: 内部 → 境界 → 送信側
        msg = _make_msg(self._GOOGLE_INTERNAL, self._GOOGLE_BOUNDARY, self._SENDER_INTERNAL)
        result = analyze_received(msg)
        assert result.hostname == "mail.example.com"
        assert result.ip == "203.0.113.1"
        assert result.tls_ok is True

    def test_no_google_header_falls_back_to_last(self):
        """Google MX ヘッダーがなければ最後の Received をフォールバックとして使う"""
        header = (
            "from mail.example.com (mail.example.com [203.0.113.1])"
            " by relay.isp.com with ESMTPS id relay123"
        )
        result = analyze_received(_make_msg(header))
        assert result.hostname == "mail.example.com"
        assert result.tls_ok is True

    def test_no_received_headers_returns_empty(self):
        msg = email.message_from_string(
            "From: sender@example.com\r\n\r\nBody", policy=email.policy.compat32
        )
        result = analyze_received(msg)
        assert result.hostname == ""
        assert result.ip == ""
        assert result.tls_ok is False
        assert result.tls_protocol == ""
