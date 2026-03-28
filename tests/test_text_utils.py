"""
テキスト処理ユーティリティ・購読解除ヘッダーのテスト

対象: _bigram_similarity, _normalize, _html_to_plain, _make_diff, analyze_unsubscribe
"""

from __future__ import annotations

import email
import email.policy
from email.message import Message

from mailprobe.verifier import (
    _bigram_similarity,
    _html_to_plain,
    _make_diff,
    _normalize,
    analyze_unsubscribe,
)

# =====================================================================
# _bigram_similarity
# =====================================================================


class TestBigramSimilarity:
    def test_identical_strings(self):
        assert _bigram_similarity("テスト文章です", "テスト文章です") == 1.0

    def test_completely_different(self):
        score = _bigram_similarity("abcde", "vwxyz")
        assert score == 0.0

    def test_empty_first_arg(self):
        assert _bigram_similarity("", "テスト") == 0.0

    def test_empty_second_arg(self):
        assert _bigram_similarity("テスト", "") == 0.0

    def test_both_empty(self):
        assert _bigram_similarity("", "") == 0.0

    def test_partial_overlap(self):
        """部分一致は 0 より大きく 1 より小さい"""
        score = _bigram_similarity("今日はいい天気です", "今日は悪い天気です")
        assert 0.0 < score < 1.0

    def test_high_similarity_above_threshold(self):
        """わずかな変更なら類似度 0.85 以上（原稿比較の閾値）"""
        a = "テストメールです。本文の内容はこちらになります。よろしくお願いします。"
        b = "テストメールです。本文の内容はこちらになります。よろしくお願いいたします。"
        assert _bigram_similarity(a, b) >= 0.85


# =====================================================================
# _normalize
# =====================================================================


class TestNormalize:
    def test_nfkc_normalization(self):
        """全角英数字は半角に変換される (NFKC)"""
        result = _normalize("Ａ１２３")
        assert result == "a123"

    def test_crlf_to_lf(self):
        result = _normalize("line1\r\nline2")
        assert "\r" not in result
        assert "line1\nline2" in result

    def test_cr_to_lf(self):
        result = _normalize("line1\rline2")
        assert "\r" not in result

    def test_consecutive_spaces_collapsed(self):
        result = _normalize("word1   word2\t\tword3")
        assert "word1 word2 word3" in result

    def test_excess_blank_lines_collapsed(self):
        result = _normalize("line1\n\n\n\n\nline2")
        assert "\n\n\n" not in result

    def test_lowercased(self):
        result = _normalize("Hello World")
        assert result == "hello world"

    def test_stripped(self):
        result = _normalize("  hello  \n")
        assert result == "hello"


# =====================================================================
# _html_to_plain
# =====================================================================


class TestHtmlToPlain:
    def test_tags_removed(self):
        result = _html_to_plain("<p>Hello <strong>World</strong></p>")
        assert "<" not in result
        assert "Hello" in result
        assert "World" in result

    def test_style_block_removed(self):
        result = _html_to_plain("<style>body { color: red; }</style><p>text</p>")
        assert "color" not in result
        assert "text" in result

    def test_script_block_removed(self):
        result = _html_to_plain("<script>alert('x')</script><p>text</p>")
        assert "alert" not in result
        assert "text" in result

    def test_nbsp_converted(self):
        result = _html_to_plain("hello&nbsp;world")
        assert "hello world" in result

    def test_amp_entity_converted(self):
        result = _html_to_plain("A &amp; B")
        assert "A & B" in result

    def test_lt_gt_entities_converted(self):
        result = _html_to_plain("&lt;tag&gt;")
        assert "<tag>" in result


# =====================================================================
# _make_diff
# =====================================================================


class TestMakeDiff:
    def test_no_diff_returns_empty(self):
        text = "line1\nline2\n"
        assert _make_diff(text, text, "A", "B") == ""

    def test_diff_contains_labels(self):
        result = _make_diff("hello\n", "world\n", "original", "received")
        assert "original" in result
        assert "received" in result

    def test_diff_shows_changes(self):
        result = _make_diff("hello\n", "world\n", "A", "B")
        assert "-hello" in result
        assert "+world" in result

    def test_max_lines_truncated(self):
        """max_lines を超えた差分は省略メッセージが付く"""
        original = "\n".join(f"line{i}" for i in range(50)) + "\n"
        received = "\n".join(f"LINE{i}" for i in range(50)) + "\n"
        result = _make_diff(original, received, "A", "B", max_lines=10)
        assert "以下省略" in result
        # 省略なし版と比べて行数が少ないことを確認
        full = _make_diff(original, received, "A", "B", max_lines=10000)
        assert len(result.splitlines()) < len(full.splitlines())

    def test_within_max_lines_no_truncation(self):
        result = _make_diff("line1\n", "line2\n", "A", "B", max_lines=40)
        assert "以下省略" not in result


# =====================================================================
# analyze_unsubscribe
# =====================================================================


def _make_msg(list_unsubscribe: str = "", list_unsubscribe_post: str = "") -> Message:
    lines = ["From: sender@example.com"]
    if list_unsubscribe:
        lines.append(f"List-Unsubscribe: {list_unsubscribe}")
    if list_unsubscribe_post:
        lines.append(f"List-Unsubscribe-Post: {list_unsubscribe_post}")
    lines += ["", "Body"]
    return email.message_from_string("\r\n".join(lines), policy=email.policy.compat32)


class TestAnalyzeUnsubscribe:
    def test_both_headers_present(self):
        msg = _make_msg(
            list_unsubscribe="<mailto:unsub@example.com>, <https://example.com/unsub>",
            list_unsubscribe_post="List-Unsubscribe=One-Click",
        )
        result = analyze_unsubscribe(msg)
        assert result.checked is True
        assert result.has_list_unsubscribe is True
        assert result.has_list_unsubscribe_post is True
        assert result.ok is True

    def test_only_list_unsubscribe(self):
        msg = _make_msg(list_unsubscribe="<mailto:unsub@example.com>")
        result = analyze_unsubscribe(msg)
        assert result.has_list_unsubscribe is True
        assert result.has_list_unsubscribe_post is False
        assert result.ok is False

    def test_only_list_unsubscribe_post(self):
        msg = _make_msg(list_unsubscribe_post="List-Unsubscribe=One-Click")
        result = analyze_unsubscribe(msg)
        assert result.has_list_unsubscribe is False
        assert result.has_list_unsubscribe_post is True
        assert result.ok is False

    def test_no_headers(self):
        msg = _make_msg()
        result = analyze_unsubscribe(msg)
        assert result.has_list_unsubscribe is False
        assert result.has_list_unsubscribe_post is False
        assert result.ok is False

    def test_values_stored(self):
        msg = _make_msg(
            list_unsubscribe="<mailto:unsub@example.com>",
            list_unsubscribe_post="List-Unsubscribe=One-Click",
        )
        result = analyze_unsubscribe(msg)
        assert "mailto:unsub@example.com" in result.list_unsubscribe_value
        assert "One-Click" in result.list_unsubscribe_post_value
