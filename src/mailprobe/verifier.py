"""
mailprobe / verifier.py
========================
raw MIME バイト列からメール配信結果を検証する。
"""

from __future__ import annotations

import difflib
import email
import email.header
import email.policy
import logging
import re
import unicodedata
from email.message import Message
from pathlib import Path

from charset_normalizer import from_bytes

from mailprobe.config import Config
from mailprobe.fetcher import ImapProviderHints, MailFetcher, SearchCriteria
from mailprobe.models import (
    AuthResult,
    DriveCompareResult,
    EncodingResult,
    HtmlResult,
    MtaResult,
    UnsubscribeResult,
    VerifyResult,
)
from mailprobe.reporter import print_report, print_summary, save_json

logger = logging.getLogger(__name__)

# =====================================================================
# MTA識別 + TLS検証 (Received: ヘッダー解析)
# =====================================================================

# Gmail が受信した際に追加する by ホストのパターン
# smtp.gmail.com / mx.google.com / googlemail.com など複数ドメインに対応
_GOOGLE_MX_RE = re.compile(r"\bby\s+\S+\.(?:google(?:mail)?|gmail)\.com\b", re.IGNORECASE)

# from ホスト名と IPアドレス (IPv4) の抽出
# ブラケットあり: (hostname [IP]) - Gmail形式
# ブラケットなし: (IP) - Outlook形式
_FROM_HOST_RE = re.compile(
    r"\bfrom\s+(\S+)\s+\((?:\S+\s+)?\[?(\d{1,3}(?:\.\d{1,3}){3})\]?\)",
    re.IGNORECASE,
)
# from ホスト名と IPアドレス (IPv6): [IPv6:xxxx] 形式と [xxxx] 形式の両方に対応
_FROM_HOST_V6_RE = re.compile(
    r"\bfrom\s+(\S+)\s+\((?:\S+\s+)?\[(?:IPv6:)?([0-9a-fA-F:]+)\]\)",
    re.IGNORECASE,
)


def analyze_received(msg: Message, provider_hints: ImapProviderHints | None = None) -> MtaResult:
    """
    Received: ヘッダーから送信元MTAのホスト名・IPアドレスおよびTLS情報を抽出する。

    Gmail は受信時に自身の Received: ヘッダーを先頭に追加するため、
    "by *.google.com" を含む Received: が送信MTAとGmailの境界になる。
    その行の "from" 部分が送信元MTA情報となる。

    provider_hints を渡すと、Gmail以外のプロバイダー固有の境界パターンと
    TLS検出ルールを適用できる（Outlook組み込み済み、外部実装も注入可能）。
    """
    result = MtaResult()
    received_headers: list[str] = msg.get_all("Received") or []

    # Gmail境界のReceivedを探す (リストは新着順: 先頭が最新)
    # from 句を持つもの (外部MTA → Google MX の境界) を優先する。
    # from 句のないものは Gmail 内部ルーティングヘッダーのため除外する。
    google_headers = [h for h in received_headers if _GOOGLE_MX_RE.search(h)]
    from_google = [
        h for h in google_headers if _FROM_HOST_RE.search(h) or _FROM_HOST_V6_RE.search(h)
    ]
    target: str = from_google[-1] if from_google else ""

    # Gmail境界が見つからない場合: プロバイダーヒントの境界パターンを試す
    # (Outlook等。外部実装のキャリアメールもここで処理される)
    if not target and provider_hints and provider_hints.mx_boundary_pattern:
        hint_re = re.compile(provider_hints.mx_boundary_pattern, re.IGNORECASE)
        hint_headers = [h for h in received_headers if hint_re.search(h)]
        hint_from = [
            h for h in hint_headers if _FROM_HOST_RE.search(h) or _FROM_HOST_V6_RE.search(h)
        ]
        target = hint_from[-1] if hint_from else ""

    # それでも見つからない場合: ESMTPS を含む Received を外部境界として使う
    # (iCloud など)
    if not target:
        esmtps_headers = [h for h in received_headers if re.search(r"\bESMTPS\b", h, re.IGNORECASE)]
        target = (
            esmtps_headers[-1]
            if esmtps_headers
            else (received_headers[-1] if received_headers else "")
        )

    if not target:
        return result

    result.raw_received = target

    # ホスト名・IPアドレス抽出 (IPv4優先、なければIPv6)
    m = _FROM_HOST_RE.search(target) or _FROM_HOST_V6_RE.search(target)
    if m:
        result.hostname = m.group(1)
        result.ip = m.group(2)

    # TLSプロトコル (ESMTPS / ESMTPSA / SMTPS / ESMTP など)
    proto_m = re.search(r"\bwith\s+(ESMTPS?A?|SMTPS|SMTP)\b", target, re.IGNORECASE)
    if proto_m:
        result.tls_protocol = proto_m.group(1).upper()
        result.tls_ok = result.tls_protocol in ("ESMTPS", "ESMTPSA", "SMTPS")
    elif (
        provider_hints
        and provider_hints.tls_server_pattern
        and re.search(provider_hints.tls_server_pattern, target, re.IGNORECASE)
    ):
        # プロバイダー固有のTLSサーバー識別 (Outlook: "Microsoft SMTP Server" 等)
        result.tls_protocol = provider_hints.tls_protocol_name

    # TLSバージョン (version=TLSv1.3 / version=TLS1_3 など表記揺れに対応)
    ver_m = re.search(r"\bversion=(TLS[\w.]+)\b", target, re.IGNORECASE)
    if ver_m:
        raw_ver = ver_m.group(1)
        result.tls_version = re.sub(r"^TLS(\d+)_(\d+)$", r"TLSv\1.\2", raw_ver, flags=re.IGNORECASE)
        # tls_by_version が True のプロバイダーは version= の存在でTLS有りと判定
        if not result.tls_ok and provider_hints and provider_hints.tls_by_version:
            result.tls_ok = True

    # 暗号スイート
    cipher_m = re.search(r"\bcipher=(\S+)", target, re.IGNORECASE)
    if cipher_m:
        result.tls_cipher = cipher_m.group(1).rstrip(")")

    return result


# =====================================================================
# 認証ヘッダー解析
# =====================================================================

_AUTH_ADVICE = {
    "spf": {
        "fail": "SPF認証失敗: 送信元IPがSPFレコードに含まれていません。DNS SPFレコード(TXT)を確認してください。",
        "softfail": "SPF softfail: ~all 設定のため受信されましたが正規の送信元でない可能性があります。-allへの変更を検討してください。",
        "neutral": "SPF neutral: ?all 設定です。より厳格な設定を推奨します。",
        "none": "SPFレコードが存在しません。送信ドメインのDNSにTXTレコードを追加してください。",
        "temperror": "SPF一時エラー: DNSの問い合わせに失敗しました。",
        "permerror": "SPF永続エラー: SPFレコードの構文が不正です。修正してください。",
        "unknown": "SPF結果をAuthentication-Resultsから読み取れませんでした。ヘッダーを手動確認してください。",
    },
    "dkim": {
        "fail": "DKIM署名検証失敗: 署名が無効です。メール転送・改ざん、または秘密鍵の不一致を確認してください。",
        "none": "DKIM署名がありません。送信MTAでDKIM署名を設定してください。",
        "unknown": "DKIM結果をAuthentication-Resultsから読み取れませんでした。",
    },
    "dmarc": {
        "fail": "DMARC認証失敗: SPFまたはDKIMのアライメントが通っていません。DMARCポリシーとfromドメインの一致を確認してください。",
        "none": "DMARCレコードが存在しません。送信ドメインのDNSに _dmarc TXTレコードを追加してください。",
        "unknown": "DMARC結果をAuthentication-Resultsから読み取れませんでした。",
    },
}


def _extract_auth_status(auth_header: str, protocol: str) -> tuple[str, str]:
    """Authentication-Results から protocol の status と詳細を抽出"""
    pattern = re.compile(
        rf"{protocol}=(pass|fail|neutral|softfail|none|temperror|permerror|hardfail)",
        re.IGNORECASE,
    )
    m = pattern.search(auth_header)
    if not m:
        return "unknown", ""

    status = m.group(1).lower()
    # 詳細情報 (smtp.mailfrom= など) をセミコロンまで取得
    detail_m = re.search(rf"{protocol}=[^;\n]{{0,200}}", auth_header, re.IGNORECASE)
    detail = detail_m.group(0).strip() if detail_m else ""
    return status, detail


def analyze_auth(msg: Message) -> AuthResult:
    # iCloud など複数の Authentication-Results ヘッダーを持つプロバイダーに対応するため全結合
    auth_headers = msg.get_all("Authentication-Results") or []
    auth_header = "\n".join(auth_headers)
    spf_header = msg.get("Received-SPF", "") or ""
    dkim_sig = msg.get("DKIM-Signature", "") or ""

    result = AuthResult(raw_auth_header=auth_header)

    # SPF
    spf_status, spf_detail = _extract_auth_status(auth_header, "spf")
    if spf_status == "unknown" and spf_header:
        m = re.match(r"(pass|fail|neutral|softfail|none)", spf_header.strip(), re.IGNORECASE)
        if m:
            spf_status = m.group(1).lower()
            spf_detail = spf_header[:120]
    result.spf_status = spf_status
    result.spf_pass = spf_status == "pass"
    result.spf_detail = spf_detail
    result.spf_advice = "" if result.spf_pass else _AUTH_ADVICE["spf"].get(spf_status, "")

    # DKIM
    dkim_status, dkim_detail = _extract_auth_status(auth_header, "dkim")
    if dkim_status == "unknown" and dkim_sig:
        dkim_status = "signature_present_not_verified"
        dkim_detail = "DKIM-Signatureヘッダーは存在するがAuthentication-Resultsで確認できません"
    result.dkim_status = dkim_status
    result.dkim_pass = dkim_status == "pass"
    result.dkim_detail = dkim_detail
    result.dkim_advice = "" if result.dkim_pass else _AUTH_ADVICE["dkim"].get(dkim_status, "")

    # DMARC
    dmarc_status, dmarc_detail = _extract_auth_status(auth_header, "dmarc")
    result.dmarc_status = dmarc_status
    result.dmarc_pass = dmarc_status == "pass"
    result.dmarc_detail = dmarc_detail
    result.dmarc_advice = "" if result.dmarc_pass else _AUTH_ADVICE["dmarc"].get(dmarc_status, "")

    result.all_passed = result.spf_pass and result.dkim_pass and result.dmarc_pass
    return result


# =====================================================================
# エンコーディング検証 (raw バイト列から直接判定)
# =====================================================================

_KNOWN_CHARSETS = {
    "utf-8": (True, "UTF-8: 推奨エンコーディング"),
    "utf8": (True, "UTF-8 (表記揺れ)"),
    "iso-2022-jp": (True, "ISO-2022-JP: 日本語メールで標準的"),
    "shift_jis": (True, "Shift_JIS: 古いシステムで使用"),
    "shift-jis": (True, "Shift-JIS (表記揺れ)"),
    "sjis": (True, "Shift-JIS (表記揺れ)"),
    "us-ascii": (False, "US-ASCII: 日本語を含む場合は不適切"),
    "windows-1252": (False, "Windows-1252: 日本語メールには不適切"),
}


def analyze_unsubscribe(msg: Message) -> UnsubscribeResult:
    """List-Unsubscribe / List-Unsubscribe-Post ヘッダーの有無を確認する"""
    result = UnsubscribeResult(checked=True)

    val = msg.get("List-Unsubscribe", "")
    if val:
        result.has_list_unsubscribe = True
        result.list_unsubscribe_value = val.strip()

    val = msg.get("List-Unsubscribe-Post", "")
    if val:
        result.has_list_unsubscribe_post = True
        result.list_unsubscribe_post_value = val.strip()

    result.ok = result.has_list_unsubscribe and result.has_list_unsubscribe_post
    return result


def _detect_garbled(text: str, label: str, threshold: int = 3) -> tuple[bool, int, list[str]]:
    """デコード済み文字列から文字化けの痕跡を検出"""
    details: list[str] = []
    score = 0

    # 1. Unicode置換文字 U+FFFD (デコード失敗の直接証拠)
    n = text.count("\ufffd")
    if n > 0:
        score += n * 10
        details.append(f"Unicode置換文字(U+FFFD)が {n} 個: デコード失敗の強い証拠です")

    # 2. ISO-2022-JPエスケープシーケンス残存
    n = len(re.findall(r"\x1b\$[B@]", text))
    if n > 0:
        score += n * 5
        details.append(f"ISO-2022-JPエスケープシーケンス残存が {n} 個: charset変換が不完全です")

    # 3. 制御文字 (タブ・改行以外)
    n = len(re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text))
    if n > 3:
        score += n * 2
        details.append(f"制御文字が {n} 個: バイナリ混入またはデコードエラーの可能性があります")

    # 4. '?' の異常な連続 (QP/Base64デコード失敗の典型)
    n = len(re.findall(r"\?{3,}", text))
    if n > 0:
        score += n * 3
        details.append(f"'?' の連続が {n} 箇所: デコード失敗の疑いがあります")

    # 5. 日本語が期待されるのにASCIIのみ (警告のみ)
    has_jp = bool(re.search(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef]", text))
    if not has_jp and len(text) > 50:
        details.append("日本語文字が含まれていません (英文メールでなければ要確認)")

    return score >= threshold, score, details


def analyze_encoding(msg: Message, raw_bytes: bytes, config: Config) -> EncodingResult:
    result = EncodingResult()

    # --- charset宣言の確認 ---
    # マルチパートの場合は各パートから取得
    _param = msg.get_param("charset")
    charset_declared = (_param if isinstance(_param, str) else "").lower()

    # マルチパートの場合は最初のtext/*パートのcharsetを代表値とする
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "text":
                _param = part.get_param("charset")
                charset_declared = (_param if isinstance(_param, str) else "").lower()
                break

    if not charset_declared:
        charset_declared = "unknown"

    result.declared_charset = charset_declared
    valid, note = _KNOWN_CHARSETS.get(
        charset_declared, (False, f'未知のcharset: "{charset_declared}"')
    )
    result.charset_valid = valid
    result.charset_note = note

    # --- charset_normalizer によるバイト列からの推定 ---
    # raw_bytes 全体ではなくメール本文部分を抽出してから推定
    body_bytes = _extract_body_bytes(msg)
    if body_bytes:
        matches = from_bytes(body_bytes)
        best = matches.best()
        if best:
            result.detected_charset = best.encoding
            result.detection_confidence = best.chaos  # 低いほど確実
            # 宣言と推定が一致しているか (表記揺れを考慮)
            result.charset_match = _charset_equiv(charset_declared, best.encoding)

    # --- テキスト・HTML本文の文字化け検出 ---
    plain_text = _get_part_text(msg, "plain")
    html_text = _get_part_text(msg, "html")

    if plain_text is not None:
        result.plain_garbled, result.plain_score, result.plain_details = _detect_garbled(
            plain_text, "text/plain", config.garbled_threshold
        )

    if html_text is not None:
        result.html_garbled, result.html_score, result.html_details = _detect_garbled(
            html_text, "text/html", config.garbled_threshold
        )

    # --- 期待charsetとの照合 ---
    if config.expected_charset:
        result.expected_charset = config.expected_charset.lower()
        result.expected_charset_ok = _charset_equiv(charset_declared, config.expected_charset)

    # charset_match (宣言と推定の一致) はNGに含めない。
    # キャリアゲートウェイによる自動変換で不一致が生じる正常ケースがあるため、
    # 警告として出力するにとどめ、総合判定には影響させない。
    result.ok = (
        result.charset_valid
        and not result.plain_garbled
        and not result.html_garbled
        and result.expected_charset_ok
    )
    return result


def _extract_body_bytes(msg: Message) -> bytes:
    """メール本文のバイト列を返す (charset_normalizer 用)"""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes) and payload:
                    return payload
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes) and payload:
            return payload
    return b""


def _get_part_text(msg: Message, subtype: str) -> str | None:
    """指定サブタイプのパートをデコードして文字列で返す"""
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_subtype() == subtype:
                parts.append(part)
    elif msg.get_content_subtype() == subtype:
        parts.append(msg)

    if not parts:
        return None

    texts = []
    for part in parts:
        raw = part.get_payload(decode=True)
        if raw is None:
            continue
        charset = part.get_param("charset") or "utf-8"
        try:
            texts.append(raw.decode(charset, errors="replace"))
        except (LookupError, UnicodeDecodeError):
            texts.append(raw.decode("utf-8", errors="replace"))

    return "\n".join(texts)


def _charset_equiv(a: str, b: str) -> bool:
    """charset名の表記揺れを吸収して同一か判定"""

    def normalize(s: str) -> str:
        return s.lower().replace("-", "").replace("_", "")

    return normalize(a) == normalize(b)


# =====================================================================
# HTML構造検証
# =====================================================================

_VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_CHECK_TAGS = [
    "html",
    "head",
    "body",
    "div",
    "span",
    "table",
    "tr",
    "td",
    "th",
    "tbody",
    "thead",
    "tfoot",
    "ul",
    "ol",
    "li",
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "a",
    "strong",
    "em",
    "style",
]


def validate_html(html: str) -> HtmlResult:
    result = HtmlResult()
    issues: list[str] = []

    # 基本構造
    if not re.search(r"<!DOCTYPE\s+html", html, re.IGNORECASE):
        issues.append("DOCTYPEが宣言されていません")
    if not re.search(r"<html[\s>]", html, re.IGNORECASE):
        issues.append("<html>タグが見つかりません")
    if not re.search(r"</html>", html, re.IGNORECASE):
        issues.append("</html>閉じタグが見つかりません")
    if not re.search(r"<head[\s>]", html, re.IGNORECASE):
        issues.append("<head>タグが見つかりません")
    if not re.search(r"<body[\s>]", html, re.IGNORECASE):
        issues.append("<body>タグが見つかりません")

    # charset meta
    m = re.search(r'charset=["\']?([^"\';\s>]+)', html, re.IGNORECASE)
    if not m:
        issues.append("HTML内にcharset宣言(meta)がありません。<meta charset='UTF-8'>を推奨します")
    else:
        result.charset_in_meta = m.group(1)

    # タグの開閉バランス (コメント除去後)
    stripped = re.sub(r"<!--[\s\S]*?-->", "", html)
    for tag in _CHECK_TAGS:
        opens = len(re.findall(rf"<{tag}[\s>]", stripped, re.IGNORECASE))
        closes = len(re.findall(rf"</{tag}>", stripped, re.IGNORECASE))
        if opens != closes:
            issues.append(f"<{tag}>タグの開閉が不一致 (開き={opens}, 閉じ={closes})")

    # エスケープされていない &
    raw_amp = re.findall(r"&(?![a-zA-Z#][^;]{0,9};)", html)
    if raw_amp:
        issues.append(f"エスケープされていない '&' が {len(raw_amp)} 個あります")

    # alt 属性なし img
    no_alt = re.findall(r"<img(?![^>]*\balt=)[^>]*>", html, re.IGNORECASE)
    if no_alt:
        issues.append(f"alt属性のない<img>が {len(no_alt)} 個あります")

    # 外部CSS (メールクライアントで無視される)
    ext_css = re.findall(r'<link[^>]+rel=["\']stylesheet["\'][^>]*>', html, re.IGNORECASE)
    if ext_css:
        issues.append(
            f"外部CSSリンクが {len(ext_css)} 個あります。メールではインラインスタイルを使用してください"
        )

    result.ok = len(issues) == 0
    result.issue_count = len(issues)
    result.issues = issues
    return result


# =====================================================================
# Google Drive 原稿との比較
# =====================================================================


def _bigram_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    def bigrams(s: str) -> set[str]:
        return {s[i : i + 2] for i in range(len(s) - 1)}

    ba, bb = bigrams(a), bigrams(b)
    intersection = len(ba & bb)
    return 2 * intersection / (len(ba) + len(bb)) if (ba or bb) else 0.0


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip().lower()


def _html_to_plain(html: str) -> str:
    html = re.sub(r"<style[\s\S]*?</style>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&lt;", "<", html)
    html = re.sub(r"&gt;", ">", html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()


def _make_diff(
    original: str, received: str, label_a: str, label_b: str, max_lines: int = 40
) -> str:
    """unified diff を文字列で返す。差分がなければ空文字列。"""
    a_lines = original.splitlines(keepends=True)
    b_lines = received.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(a_lines, b_lines, fromfile=label_a, tofile=label_b, lineterm="")
    )
    if not diff:
        return ""
    if len(diff) > max_lines:
        diff = [*diff[:max_lines], f"... (以下省略、差分 {len(diff) - max_lines} 行)"]
    return "\n".join(diff)


def _read_original(path: Path) -> str:
    """原稿ファイルをcharset自動検出で読み込み、正規化テキストを返す"""
    raw = path.read_bytes()
    detected = from_bytes(raw).best()
    encoding = detected.encoding if detected else "utf-8"
    text = raw.decode(encoding, errors="replace")
    if path.suffix.lower() in (".html", ".htm"):
        text = _html_to_plain(text)
    return _normalize(text)


def _find_original_files(
    originals_dir: Path, subject: str
) -> tuple[Path | None, Path | None, Path | None]:
    """
    originals/ から件名に一致するファイルを探す。

    Returns: (txt_file, html_file, eml_file)
      - eml_file が見つかった場合は txt_file / html_file は None になる (.eml 優先)
      - eml_file が None の場合は従来通り txt_file / html_file を返す

    探索順:
      1. originals/{件名一致サブディレクトリ}/ 内のファイル
      2. originals/ 直下のファイル (フラット構成・後退互換)
    """

    def _scan(directory: Path) -> tuple[Path | None, Path | None, Path | None]:
        t = h = e = None
        for f in directory.iterdir():
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext == ".eml" and e is None:
                e = f
            elif ext in (".html", ".htm") and h is None:
                h = f
            elif ext == ".txt" and t is None:
                t = f
        # .eml が見つかれば .txt / .html は返さない
        return (None, None, e) if e else (t, h, None)

    # 1. サブディレクトリ優先: ディレクトリ名が件名と部分一致するものを探す
    for entry in originals_dir.iterdir():
        if entry.is_dir() and (entry.name in subject or subject in entry.name):
            txt_file, html_file, eml_file = _scan(entry)
            if txt_file or html_file or eml_file:
                return txt_file, html_file, eml_file

    # 2. フラット構成へのフォールバック
    txt_file: Path | None = None
    html_file: Path | None = None
    eml_file: Path | None = None
    for f in originals_dir.iterdir():
        if not f.is_file():
            continue
        if not (f.stem in subject or subject in f.stem):
            continue
        ext = f.suffix.lower()
        if ext == ".eml" and eml_file is None:
            eml_file = f
        elif ext in (".html", ".htm") and html_file is None:
            html_file = f
        elif ext == ".txt" and txt_file is None:
            txt_file = f

    return (None, None, eml_file) if eml_file else (txt_file, html_file, None)


def _parse_original_eml(eml_path: Path) -> tuple[str | None, str | None]:
    """EMLファイルをパースして (正規化済みtext/plain, 正規化済みtext/html平文) を返す。

    パートが存在しない場合は None を返す。
    """
    raw = eml_path.read_bytes()
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    plain_text = _get_part_text(msg, "plain")
    html_text = _get_part_text(msg, "html")
    plain_normalized = _normalize(plain_text) if plain_text is not None else None
    html_normalized = _normalize(_html_to_plain(html_text)) if html_text is not None else None
    return plain_normalized, html_normalized


def compare_with_original(
    msg: Message,
    originals_dir: Path,
    subject: str,
) -> DriveCompareResult:
    """originals/ ディレクトリの原稿ファイルとメール本文を比較する。

    ファイル探索の優先順位:
      1. .eml ファイル (EML形式: text/plain・text/html を1ファイルから抽出)
      2. .txt / .html ファイル (従来形式: 後退互換)

    サブディレクトリ構成 (originals/{件名}/body.eml など) を優先して探し、
    なければフラット構成 (originals/件名.eml) にフォールバックする。
    """
    result = DriveCompareResult()

    txt_file, html_file, eml_file = _find_original_files(originals_dir, subject)

    if not txt_file and not html_file and not eml_file:
        result.note = (
            f"件名「{subject}」に対応する原稿ファイルが {originals_dir} に見つかりません (スキップ)"
        )
        return result

    result.skipped = False
    try:
        if eml_file:
            plain_original, html_original = _parse_original_eml(eml_file)
        else:
            plain_original = _read_original(txt_file) if txt_file else None
            html_original = _read_original(html_file) if html_file else None
    except Exception as e:
        result.error = f"原稿ファイルの読み込み失敗: {e}"
        return result

    has_plain_part = _get_part_text(msg, "plain") is not None
    has_html_part = _get_part_text(msg, "html") is not None
    plain_text = _get_part_text(msg, "plain") or ""
    html_text = _get_part_text(msg, "html") or ""
    norm_plain = _normalize(plain_text)
    norm_html = _normalize(_html_to_plain(html_text))

    # 原稿ファイルが片方しかない場合は共用する
    effective_plain = plain_original if plain_original is not None else html_original
    effective_html = html_original if html_original is not None else plain_original

    # メールにパートが存在する場合のみ比較する
    # パートが存在しない場合はスキップ扱い (match=True) とし、NG判定に影響させない
    if has_plain_part:
        sim_plain = _bigram_similarity(effective_plain or "", norm_plain)
        result.plain_match = sim_plain >= 0.85
        if not result.plain_match:
            result.diff_plain = _make_diff(effective_plain or "", norm_plain, "原稿", "受信メール")
    else:
        sim_plain = None
        result.plain_match = True  # パートなし = スキップ

    if has_html_part:
        sim_html = _bigram_similarity(effective_html or "", norm_html)
        result.html_match = sim_html >= 0.85
        if not result.html_match:
            result.diff_html = _make_diff(
                effective_html or "", norm_html, "原稿(HTML)", "受信メール(HTML)"
            )
    else:
        sim_html = None
        result.html_match = True  # パートなし = スキップ

    sims = [s for s in (sim_plain, sim_html) if s is not None]
    result.similarity = round(max(sims) * 100, 1) if sims else 0.0

    plain_str = f"{round(sim_plain * 100, 1)}%" if sim_plain is not None else "スキップ(パートなし)"
    html_str = f"{round(sim_html * 100, 1)}%" if sim_html is not None else "スキップ(パートなし)"
    if eml_file:
        used_files = eml_file.name
    else:
        used_files = " / ".join(f.name for f in [txt_file, html_file] if f is not None)
    result.note = f"原稿ファイル: {used_files} | Text一致率: {plain_str} | HTML一致率: {html_str}"
    return result


# =====================================================================
# 総合検証
# =====================================================================


def verify_message(
    raw: bytes, config: Config, provider_hints: ImapProviderHints | None = None
) -> VerifyResult:
    # Python email モジュールで raw MIME をパース
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)

    # Message-ID ヘッダーを識別子として使用 (山括弧を除去)
    msg_id = (msg.get("Message-ID", "") or "").strip().strip("<>")

    subject = str(
        email.header.make_header(email.header.decode_header(msg.get("Subject", "(件名なし)")))
    )
    from_addr = str(email.header.make_header(email.header.decode_header(msg.get("From", ""))))
    date_str = msg.get("Date", "")

    logger.info("\n  検証中: [%s] from=%s date=%s", subject, from_addr, date_str)

    result = VerifyResult(
        message_id=msg_id,
        subject=subject,
        from_addr=from_addr,
        date=date_str,
    )

    # 0. MTA識別 + TLS
    result.mta = analyze_received(msg, provider_hints)
    if not result.mta.tls_ok:
        result.overall_ok = False

    # 1. 認証ヘッダー
    result.auth = analyze_auth(msg)
    if not result.auth.all_passed:
        result.overall_ok = False

    # 2. エンコーディング
    result.encoding = analyze_encoding(msg, raw, config)
    if not result.encoding.ok:
        result.overall_ok = False

    # 3. HTML構造 (参考情報。overall_ok には影響しない)
    html_text = _get_part_text(msg, "html")
    if html_text:
        result.html = validate_html(html_text)
    else:
        result.html = HtmlResult(
            ok=True, issue_count=0, issues=[], charset_in_meta="HTMLパートなし"
        )

    # 4. 原稿比較
    originals_dir = Path(config.originals_dir)
    if originals_dir.exists():
        result.compare = compare_with_original(msg, originals_dir, subject)

    # 5. 購読解除ヘッダー (check_unsubscribe が True のときのみ)
    if config.check_unsubscribe:
        result.unsubscribe = analyze_unsubscribe(msg)
        if not result.unsubscribe.ok:
            result.overall_ok = False

    # 6. カスタムヘッダー記録
    for header_name in config.custom_headers:
        val = (msg.get(header_name, "") or "").strip()
        if val:
            result.custom_headers[header_name] = val

    return result


# =====================================================================
# エントリーポイント
# =====================================================================


def run_condition(fetcher: MailFetcher, config: Config, provider: str = "") -> list[VerifyResult]:
    """1検索条件の検索・検証を実行して結果リストを返す。

    バッチ実行からも単独実行からも呼ばれる共通ロジック。
    """
    provider_str = f" ({provider})" if provider else ""
    logger.info("\n[開始] メール検証%s subject='%s'", provider_str, config.subject)
    if config.expected_charset:
        logger.info("       期待charset: %s", config.expected_charset)
    if config.expected_mta_count:
        logger.info("       期待MTA台数: %s台", config.expected_mta_count)

    criteria = SearchCriteria.from_config(config)
    raw_messages = fetcher.fetch_messages(criteria)

    if not raw_messages:
        logger.warning(
            "対象メールが見つかりませんでした。config.yaml の検索条件を確認してください。"
        )
        return []

    # MailFetcher Protocol にない属性なので getattr で安全に取得
    hints: ImapProviderHints | None = getattr(fetcher, "provider_hints", None)

    logger.info("%d 件を検証します", len(raw_messages))
    results = []
    for raw in raw_messages:
        r = verify_message(raw, config, provider_hints=hints)
        r.provider = provider
        print_report(r)
        out = save_json(r, Path(config.results_dir))
        logger.info("\n  → 結果保存: %s", out)
        results.append(r)

    print_summary(results, config)
    return results
