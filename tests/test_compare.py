"""
tests/test_compare.py
=====================
compare_with_original / _find_original_files / _parse_original_eml のユニットテスト

対象:
  - .eml 形式による比較 (新形式)
  - .txt / .html 形式による比較 (従来形式・後退互換)
  - .eml が .txt/.html より優先されること
  - サブディレクトリ構成とフラット構成の両方
"""

from __future__ import annotations

import base64
import email
import email.message
import email.policy
from pathlib import Path

from mailprobe.verifier import (
    _find_original_files,
    _parse_original_eml,
    compare_with_original,
)

# =====================================================================
# テスト用ヘルパー
# =====================================================================

_PLAIN = "テストメールです。\r\nこちらはテキスト本文になります。\r\nよろしくお願いいたします。\r\n"
_HTML = """\
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"></head>
<body>
  <p>テストメールです。</p>
  <p>こちらはテキスト本文になります。</p>
  <p>よろしくお願いいたします。</p>
</body>
</html>
"""


def _make_eml(
    plain: str = _PLAIN,
    html: str = _HTML,
    charset: str = "UTF-8",
    subject: str = "テストメール",
) -> bytes:
    """マルチパートEMLバイト列を生成する"""
    subject_b64 = base64.b64encode(subject.encode("utf-8")).decode("ascii")
    plain_b64 = base64.encodebytes(plain.encode(charset)).decode("ascii").strip()
    html_b64 = base64.encodebytes(html.encode(charset)).decode("ascii").strip()
    raw = (
        f"MIME-Version: 1.0\r\n"
        f"Subject: =?UTF-8?B?{subject_b64}?=\r\n"
        f"Content-Type: multipart/alternative;\r\n"
        f'\tboundary="----=_Part_test"\r\n'
        f"\r\n"
        f"------=_Part_test\r\n"
        f"Content-Type: text/plain; charset={charset}\r\n"
        f"Content-Transfer-Encoding: base64\r\n"
        f"\r\n"
        f"{plain_b64}\r\n"
        f"\r\n"
        f"------=_Part_test\r\n"
        f"Content-Type: text/html; charset={charset}\r\n"
        f"Content-Transfer-Encoding: base64\r\n"
        f"\r\n"
        f"{html_b64}\r\n"
        f"\r\n"
        f"------=_Part_test--\r\n"
    )
    return raw.encode("ascii")


def _make_received_msg(plain: str = _PLAIN, html: str = _HTML) -> email.message.Message:
    """受信メール (email.Message) を生成する"""
    return email.message_from_bytes(_make_eml(plain=plain, html=html), policy=email.policy.compat32)


def _write_eml(directory: Path, filename: str, content: bytes) -> Path:
    path = directory / filename
    path.write_bytes(content)
    return path


# =====================================================================
# _find_original_files: EML 優先
# =====================================================================


class TestFindOriginalFilesEml:
    def test_eml_found_in_subdir(self, tmp_path):
        subdir = tmp_path / "テストメール"
        subdir.mkdir()
        _write_eml(subdir, "body.eml", _make_eml())

        txt, html, eml = _find_original_files(tmp_path, "テストメール")
        assert eml is not None
        assert eml.name == "body.eml"
        assert txt is None
        assert html is None

    def test_eml_takes_priority_over_txt_html(self, tmp_path):
        """.eml と .txt/.html が共存する場合は .eml を返す"""
        subdir = tmp_path / "テストメール"
        subdir.mkdir()
        _write_eml(subdir, "body.eml", _make_eml())
        (subdir / "body.txt").write_text(_PLAIN, encoding="utf-8")
        (subdir / "body.html").write_text(_HTML, encoding="utf-8")

        txt, html, eml = _find_original_files(tmp_path, "テストメール")
        assert eml is not None
        assert txt is None
        assert html is None

    def test_eml_flat_layout(self, tmp_path):
        """フラット構成でも .eml が見つかる"""
        _write_eml(tmp_path, "テストメール.eml", _make_eml())

        txt, html, eml = _find_original_files(tmp_path, "テストメール")
        assert eml is not None
        assert txt is None
        assert html is None

    def test_no_files_returns_all_none(self, tmp_path):
        txt, html, eml = _find_original_files(tmp_path, "テストメール")
        assert txt is None
        assert html is None
        assert eml is None


class TestFindOriginalFilesTxtHtml:
    def test_txt_and_html_found(self, tmp_path):
        subdir = tmp_path / "テストメール"
        subdir.mkdir()
        (subdir / "body.txt").write_text(_PLAIN, encoding="utf-8")
        (subdir / "body.html").write_text(_HTML, encoding="utf-8")

        txt, html, eml = _find_original_files(tmp_path, "テストメール")
        assert txt is not None
        assert html is not None
        assert eml is None

    def test_txt_only(self, tmp_path):
        subdir = tmp_path / "テストメール"
        subdir.mkdir()
        (subdir / "body.txt").write_text(_PLAIN, encoding="utf-8")

        txt, html, eml = _find_original_files(tmp_path, "テストメール")
        assert txt is not None
        assert html is None
        assert eml is None


# =====================================================================
# _parse_original_eml
# =====================================================================


class TestParseOriginalEml:
    def test_extracts_plain_and_html(self, tmp_path):
        eml_path = _write_eml(tmp_path, "body.eml", _make_eml())
        plain, html = _parse_original_eml(eml_path)
        assert plain is not None
        assert "テストメール" in plain
        assert html is not None
        assert "テストメール" in html

    def test_plain_only_eml(self, tmp_path):
        """text/plain のみの EML は html が None になる"""
        subject_b64 = base64.b64encode("テスト".encode()).decode("ascii")
        plain_b64 = base64.encodebytes(_PLAIN.encode("utf-8")).decode("ascii").strip()
        raw = (
            f"MIME-Version: 1.0\r\n"
            f"Subject: =?UTF-8?B?{subject_b64}?=\r\n"
            f"Content-Type: text/plain; charset=UTF-8\r\n"
            f"Content-Transfer-Encoding: base64\r\n"
            f"\r\n"
            f"{plain_b64}\r\n"
        ).encode("ascii")
        eml_path = tmp_path / "body.eml"
        eml_path.write_bytes(raw)

        plain, html = _parse_original_eml(eml_path)
        assert plain is not None
        assert html is None

    def test_normalized_text_returned(self, tmp_path):
        """返値は正規化済み (小文字・CRLF除去・NFKC) になっている"""
        eml_path = _write_eml(tmp_path, "body.eml", _make_eml(plain="Ａ１２３\r\n"))
        plain, _ = _parse_original_eml(eml_path)
        assert plain == "a123"


# =====================================================================
# compare_with_original: EML 形式
# =====================================================================


class TestCompareWithOriginalEml:
    def test_matching_eml_returns_ok(self, tmp_path):
        """受信メールと原稿EMLの内容が一致する場合 plain_match / html_match が True"""
        subdir = tmp_path / "テストメール"
        subdir.mkdir()
        _write_eml(subdir, "body.eml", _make_eml())

        msg = _make_received_msg()
        result = compare_with_original(msg, tmp_path, "テストメール")

        assert not result.skipped
        assert result.plain_match
        assert result.html_match
        assert result.error == ""

    def test_eml_filename_in_note(self, tmp_path):
        """result.note に EML ファイル名が含まれる"""
        subdir = tmp_path / "テストメール"
        subdir.mkdir()
        _write_eml(subdir, "body.eml", _make_eml())

        msg = _make_received_msg()
        result = compare_with_original(msg, tmp_path, "テストメール")

        assert "body.eml" in result.note

    def test_different_content_returns_ng(self, tmp_path):
        """受信メールと原稿EMLの内容が大きく異なる場合 plain_match が False"""
        subdir = tmp_path / "テストメール"
        subdir.mkdir()
        _write_eml(subdir, "body.eml", _make_eml(plain="全く異なる内容のテキストです。" * 10))

        msg = _make_received_msg()
        result = compare_with_original(msg, tmp_path, "テストメール")

        assert not result.plain_match
        assert result.diff_plain != ""

    def test_eml_preferred_over_txt(self, tmp_path):
        """EML と TXT が共存する場合 EML を使う"""
        subdir = tmp_path / "テストメール"
        subdir.mkdir()
        _write_eml(subdir, "body.eml", _make_eml())
        # TXT には全く異なる内容を置く (EMLが使われれば一致するはず)
        (subdir / "body.txt").write_text("全く異なる内容です。" * 10, encoding="utf-8")

        msg = _make_received_msg()
        result = compare_with_original(msg, tmp_path, "テストメール")

        assert result.plain_match  # EML の内容で比較されている


# =====================================================================
# compare_with_original: 従来形式 (.txt / .html) の後退互換
# =====================================================================


class TestCompareWithOriginalLegacy:
    def test_matching_txt_returns_ok(self, tmp_path):
        subdir = tmp_path / "テストメール"
        subdir.mkdir()
        (subdir / "body.txt").write_text(_PLAIN, encoding="utf-8")

        msg = _make_received_msg()
        result = compare_with_original(msg, tmp_path, "テストメール")

        assert not result.skipped
        assert result.plain_match

    def test_no_original_file_is_skipped(self, tmp_path):
        msg = _make_received_msg()
        result = compare_with_original(msg, tmp_path, "テストメール")

        assert result.skipped
        assert "見つかりません" in result.note
