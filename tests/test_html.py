"""
validate_html のテスト

HTML メールの構造検証ロジックを検証する。
"""

from __future__ import annotations

from mailprobe.verifier import validate_html

_VALID_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body>
<p>Hello</p>
</body>
</html>
"""


# =====================================================================
# 正常系
# =====================================================================


class TestValidHtml:
    def test_valid_html_ok(self):
        result = validate_html(_VALID_HTML)
        assert result.ok is True
        assert result.issue_count == 0
        assert result.issues == []

    def test_charset_in_meta_extracted(self):
        result = validate_html(_VALID_HTML)
        assert result.charset_in_meta == "UTF-8"


# =====================================================================
# 基本構造の欠落
# =====================================================================


class TestMissingStructure:
    def test_missing_doctype(self):
        html = "<html><head><meta charset='UTF-8'></head><body></body></html>"
        result = validate_html(html)
        assert any("DOCTYPE" in i for i in result.issues)

    def test_missing_html_tag(self):
        html = "<!DOCTYPE html><head><meta charset='UTF-8'></head><body></body></html>"
        result = validate_html(html)
        assert any("<html>" in i for i in result.issues)

    def test_missing_html_closing_tag(self):
        html = "<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body></body>"
        result = validate_html(html)
        assert any("</html>" in i for i in result.issues)

    def test_missing_head_tag(self):
        html = "<!DOCTYPE html><html><meta charset='UTF-8'><body></body></html>"
        result = validate_html(html)
        assert any("<head>" in i for i in result.issues)

    def test_missing_body_tag(self):
        html = "<!DOCTYPE html><html><head><meta charset='UTF-8'></head></html>"
        result = validate_html(html)
        assert any("<body>" in i for i in result.issues)

    def test_missing_charset_meta(self):
        html = "<!DOCTYPE html><html><head></head><body></body></html>"
        result = validate_html(html)
        assert any("charset" in i for i in result.issues)
        assert result.charset_in_meta == "なし"


# =====================================================================
# タグの開閉不一致
# =====================================================================


class TestTagImbalance:
    def test_unclosed_div(self):
        html = (
            "<!DOCTYPE html><html><head><meta charset='UTF-8'></head>"
            "<body><div><p>text</p></body></html>"
        )
        result = validate_html(html)
        assert any("<div>" in i and "不一致" in i for i in result.issues)

    def test_extra_closing_p(self):
        html = (
            "<!DOCTYPE html><html><head><meta charset='UTF-8'></head>"
            "<body><p>text</p></p></body></html>"
        )
        result = validate_html(html)
        assert any("<p>" in i and "不一致" in i for i in result.issues)

    def test_comment_not_counted_as_tag(self):
        """コメント内のタグはカウントしない"""
        html = (
            "<!DOCTYPE html><html><head><meta charset='UTF-8'></head>"
            "<body><!-- <div> --></body></html>"
        )
        result = validate_html(html)
        assert not any("div" in i for i in result.issues)


# =====================================================================
# img / CSS / & のチェック
# =====================================================================


class TestAccessibilityAndBestPractices:
    def test_img_without_alt(self):
        html = (
            "<!DOCTYPE html><html><head><meta charset='UTF-8'></head>"
            '<body><img src="photo.jpg"></body></html>'
        )
        result = validate_html(html)
        assert any("alt" in i for i in result.issues)

    def test_img_with_alt_ok(self):
        html = (
            "<!DOCTYPE html><html><head><meta charset='UTF-8'></head>"
            '<body><img src="photo.jpg" alt="写真"></body></html>'
        )
        result = validate_html(html)
        assert not any("alt" in i for i in result.issues)

    def test_external_css_link_flagged(self):
        html = (
            "<!DOCTYPE html><html>"
            "<head><meta charset='UTF-8'>"
            "<link rel='stylesheet' href='style.css'></head>"
            "<body></body></html>"
        )
        result = validate_html(html)
        assert any("外部CSS" in i for i in result.issues)

    def test_unescaped_ampersand_flagged(self):
        html = "<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body>A & B</body></html>"
        result = validate_html(html)
        assert any("&" in i for i in result.issues)

    def test_escaped_ampersand_ok(self):
        html = (
            "<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body>A &amp; B</body></html>"
        )
        result = validate_html(html)
        assert not any("エスケープ" in i for i in result.issues)
