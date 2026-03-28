"""
analyze_auth / _extract_auth_status のテスト

SPF・DKIM・DMARC の認証ステータスを正しく解析できるか検証する。
"""

from __future__ import annotations

import email
import email.policy
from email.message import Message

from mailprobe.verifier import analyze_auth


def _make_msg(
    auth_results: str = "",
    received_spf: str = "",
    dkim_signature: str = "",
) -> Message:
    lines = ["From: sender@example.com"]
    if auth_results:
        lines.append(f"Authentication-Results: {auth_results}")
    if received_spf:
        lines.append(f"Received-SPF: {received_spf}")
    if dkim_signature:
        lines.append(f"DKIM-Signature: {dkim_signature}")
    lines += ["", "Body"]
    return email.message_from_string("\r\n".join(lines), policy=email.policy.compat32)


_ALL_PASS_HEADER = (
    "mx.google.com;"
    " spf=pass smtp.mailfrom=example.com;"
    " dkim=pass header.i=@example.com;"
    " dmarc=pass (p=QUARANTINE) header.from=example.com"
)


# =====================================================================
# 正常系: 全通過
# =====================================================================


class TestAllPassed:
    def test_all_pass(self):
        result = analyze_auth(_make_msg(auth_results=_ALL_PASS_HEADER))
        assert result.spf_pass is True
        assert result.dkim_pass is True
        assert result.dmarc_pass is True
        assert result.all_passed is True

    def test_all_passed_requires_all_three(self):
        """DMARC が unknown でも all_passed は False"""
        header = (
            "mx.google.com; spf=pass smtp.mailfrom=example.com; dkim=pass header.i=@example.com"
        )
        result = analyze_auth(_make_msg(auth_results=header))
        assert result.spf_pass is True
        assert result.dkim_pass is True
        assert result.dmarc_pass is False
        assert result.all_passed is False


# =====================================================================
# SPF
# =====================================================================


class TestSpf:
    def test_spf_pass(self):
        result = analyze_auth(
            _make_msg(auth_results="mx.google.com; spf=pass smtp.mailfrom=example.com")
        )
        assert result.spf_pass is True
        assert result.spf_advice == ""

    def test_spf_fail(self):
        result = analyze_auth(
            _make_msg(auth_results="mx.google.com; spf=fail smtp.mailfrom=example.com")
        )
        assert result.spf_pass is False
        assert "SPF認証失敗" in result.spf_advice

    def test_spf_softfail(self):
        result = analyze_auth(
            _make_msg(auth_results="mx.google.com; spf=softfail smtp.mailfrom=example.com")
        )
        assert result.spf_pass is False
        assert "softfail" in result.spf_advice

    def test_spf_none(self):
        result = analyze_auth(
            _make_msg(auth_results="mx.google.com; spf=none smtp.mailfrom=example.com")
        )
        assert result.spf_pass is False
        assert "SPFレコード" in result.spf_advice

    def test_spf_fallback_to_received_spf(self):
        """Authentication-Results に SPF がなければ Received-SPF にフォールバック"""
        result = analyze_auth(
            _make_msg(
                auth_results="mx.google.com; dkim=pass header.i=@example.com",
                received_spf="pass (example.com: domain designates 203.0.113.1 as permitted sender)",
            )
        )
        assert result.spf_pass is True
        assert result.spf_status == "pass"

    def test_spf_unknown_when_no_headers(self):
        result = analyze_auth(_make_msg())
        assert result.spf_status == "unknown"
        assert result.spf_pass is False


# =====================================================================
# DKIM
# =====================================================================


class TestDkim:
    def test_dkim_pass(self):
        result = analyze_auth(
            _make_msg(auth_results="mx.google.com; dkim=pass header.i=@example.com")
        )
        assert result.dkim_pass is True
        assert result.dkim_advice == ""

    def test_dkim_fail(self):
        result = analyze_auth(
            _make_msg(auth_results="mx.google.com; dkim=fail header.i=@example.com")
        )
        assert result.dkim_pass is False
        assert "DKIM署名検証失敗" in result.dkim_advice

    def test_dkim_none(self):
        result = analyze_auth(_make_msg(auth_results="mx.google.com; dkim=none"))
        assert result.dkim_pass is False
        assert "DKIM署名がありません" in result.dkim_advice

    def test_dkim_signature_present_but_no_auth_result(self):
        """DKIM-Signature はあるが Authentication-Results に結果がない場合"""
        result = analyze_auth(
            _make_msg(
                auth_results="mx.google.com; spf=pass smtp.mailfrom=example.com",
                dkim_signature="v=1; a=rsa-sha256; d=example.com; s=default; ...",
            )
        )
        assert result.dkim_status == "signature_present_not_verified"
        assert result.dkim_pass is False


# =====================================================================
# DMARC
# =====================================================================


class TestDmarc:
    def test_dmarc_pass(self):
        result = analyze_auth(
            _make_msg(
                auth_results="mx.google.com; dmarc=pass (p=QUARANTINE) header.from=example.com"
            )
        )
        assert result.dmarc_pass is True
        assert result.dmarc_advice == ""

    def test_dmarc_fail(self):
        result = analyze_auth(
            _make_msg(auth_results="mx.google.com; dmarc=fail header.from=example.com")
        )
        assert result.dmarc_pass is False
        assert "DMARC認証失敗" in result.dmarc_advice

    def test_dmarc_none(self):
        result = analyze_auth(_make_msg(auth_results="mx.google.com; dmarc=none"))
        assert result.dmarc_pass is False
        assert "DMARCレコード" in result.dmarc_advice

    def test_dmarc_unknown_when_absent(self):
        result = analyze_auth(
            _make_msg(auth_results="mx.google.com; spf=pass smtp.mailfrom=example.com")
        )
        assert result.dmarc_status == "unknown"
        assert result.dmarc_pass is False


# =====================================================================
# ヘッダーなし
# =====================================================================


class TestNoAuthHeaders:
    def test_no_headers_all_unknown(self):
        result = analyze_auth(_make_msg())
        assert result.spf_status == "unknown"
        assert result.dkim_status == "unknown"
        assert result.dmarc_status == "unknown"
        assert result.all_passed is False
