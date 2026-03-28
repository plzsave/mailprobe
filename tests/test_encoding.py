"""
tests/test_encoding.py
======================
文字化け検出 (_detect_garbled) と analyze_encoding のユニットテスト
"""

import base64
import email
import email.policy

from mailprobe.config import Config
from mailprobe.verifier import _detect_garbled, analyze_encoding

# =====================================================================
# テスト用ヘルパー
# =====================================================================


def _make_msg(body: str, declared_charset: str) -> tuple:
    """指定 charset でエンコードした本文を、同じ charset と宣言した MIME メッセージを作る"""
    body_bytes = body.encode(declared_charset)
    b64 = base64.b64encode(body_bytes).decode("ascii")
    raw = (
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset={declared_charset}\r\n"
        f"Content-Transfer-Encoding: base64\r\n"
        f"\r\n"
        f"{b64}\r\n"
    ).encode("ascii")
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    return msg, raw


def _make_garbled_msg(body: str) -> tuple:
    """Shift-JIS バイト列を utf-8 と宣言した MIME メッセージを作る (意図的な文字化け)"""
    body_bytes = body.encode("shift_jis")
    b64 = base64.b64encode(body_bytes).decode("ascii")
    raw = (
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Transfer-Encoding: base64\r\n"
        f"\r\n"
        f"{b64}\r\n"
    ).encode("ascii")
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    return msg, raw


# =====================================================================
# _detect_garbled のテスト
# =====================================================================


class TestDetectGarbled:
    def test_normal_japanese_ok(self):
        """正常な日本語テキストは文字化けなし"""
        text = "テストメールです。正常に受信できました。"
        garbled, score, _details = _detect_garbled(text, "text/plain", threshold=3)
        assert not garbled
        assert score == 0

    def test_replacement_char_detected(self):
        """U+FFFD (Unicode 置換文字) は文字化けとして検出される"""
        text = "テスト\ufffd\ufffdメール"
        garbled, score, details = _detect_garbled(text, "text/plain", threshold=3)
        assert garbled
        assert score >= 3
        assert any("U+FFFD" in d for d in details)

    def test_iso2022jp_escape_detected(self):
        """ISO-2022-JP エスケープシーケンス残存は文字化けとして検出される"""
        text = "テスト\x1b$Bメール"
        garbled, _score, details = _detect_garbled(text, "text/plain", threshold=3)
        assert garbled
        assert any("ISO-2022-JP" in d for d in details)

    def test_consecutive_question_marks_detected(self):
        """'???' の連続はデコード失敗として検出される"""
        text = "件名????? 本文"
        garbled, _score, details = _detect_garbled(text, "text/plain", threshold=3)
        assert garbled
        assert any("?" in d for d in details)

    def test_threshold_controls_sensitivity(self):
        """threshold を上げると検出されにくくなる"""
        text = "\ufffd"  # 1文字のみ (score = 10)
        garbled_low, _, _ = _detect_garbled(text, "text/plain", threshold=3)
        garbled_high, _, _ = _detect_garbled(text, "text/plain", threshold=100)
        assert garbled_low
        assert not garbled_high

    def test_long_ascii_warns_no_japanese(self):
        """50文字超の ASCII のみテキストは日本語なし警告が付く"""
        text = "a" * 60
        _, _, details = _detect_garbled(text, "text/plain", threshold=3)
        assert any("日本語" in d for d in details)

    def test_short_ascii_no_warning(self):
        """50文字以下の ASCII テキストは日本語なし警告なし"""
        text = "hello"
        _, _, details = _detect_garbled(text, "text/plain", threshold=3)
        assert not any("日本語" in d for d in details)


# =====================================================================
# analyze_encoding のテスト
# =====================================================================


class TestAnalyzeEncoding:
    def test_utf8_email_ok(self):
        """正常な UTF-8 メールは ok=True"""
        msg, raw = _make_msg("テストメールです。", "utf-8")
        result = analyze_encoding(msg, raw, Config())
        assert result.declared_charset == "utf-8"
        assert result.charset_valid
        assert not result.plain_garbled
        assert result.ok

    def test_iso2022jp_email_ok(self):
        """正常な ISO-2022-JP メールは ok=True"""
        msg, raw = _make_msg("テストメールです。", "iso-2022-jp")
        result = analyze_encoding(msg, raw, Config())
        assert result.declared_charset == "iso-2022-jp"
        assert result.charset_valid
        assert result.ok

    def test_garbled_sjis_declared_utf8(self):
        """Shift-JIS バイト列を UTF-8 と宣言したメールは文字化けを検出する"""
        msg, raw = _make_garbled_msg("テストメールです。")
        result = analyze_encoding(msg, raw, Config())
        assert result.declared_charset == "utf-8"
        assert result.plain_garbled
        assert not result.ok

    def test_expected_charset_match(self):
        """期待 charset が宣言 charset と一致する場合は expected_charset_ok=True"""
        msg, raw = _make_msg("テストメールです。", "utf-8")
        result = analyze_encoding(msg, raw, Config(expected_charset="utf-8"))
        assert result.expected_charset_ok

    def test_expected_charset_mismatch(self):
        """期待 charset が宣言 charset と不一致の場合は expected_charset_ok=False"""
        msg, raw = _make_msg("テストメールです。", "utf-8")
        result = analyze_encoding(msg, raw, Config(expected_charset="iso-2022-jp"))
        assert not result.expected_charset_ok

    def test_no_expected_charset_skips_check(self):
        """期待 charset 未指定の場合は expected_charset_ok=True (チェックしない)"""
        msg, raw = _make_msg("テストメールです。", "utf-8")
        result = analyze_encoding(msg, raw, Config(expected_charset=""))
        assert result.expected_charset_ok
