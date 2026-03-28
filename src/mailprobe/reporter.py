"""
reporter.py
===========
検証結果の出力・保存を担う。

  print_report()       : 1件の検証結果をターミナルに表示
  print_summary()      : 1条件分のMTA別サマリーを表示
  print_batch_summary(): バッチ実行の横断サマリーを表示
  save_json()          : 1件の検証結果をJSONに保存
  save_csv()           : バッチ結果をCSVに保存
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from mailprobe.config import Config
from mailprobe.models import VerifyResult

# =====================================================================
# 共通ヘルパー
# =====================================================================


def _ok(b: bool) -> str:
    return "✅ OK" if b else "❌ NG"


def _icon(b: bool) -> str:
    return "✅" if b else "❌"


def _ok_csv(b: bool) -> str:
    """CSV出力用: True/False を "OK" / "NG" に変換"""
    return "OK" if b else "NG"


def _tri(b: bool, skipped: bool) -> str:
    return "-" if skipped else _ok_csv(b)


# =====================================================================
# ターミナル表示
# =====================================================================


def print_report(result: VerifyResult) -> None:

    print("\n" + "=" * 60)
    print(f"総合判定: {_ok(result.overall_ok)}")
    if result.provider:
        print(f"プロバイダー: {result.provider}")
    print(f"件名    : {result.subject}")
    print(f"送信元  : {result.from_addr}")
    print(f"受信日時: {result.date}")

    print("\n--- MTA / TLS ---")
    t = result.mta
    if t.hostname or t.ip:
        print(f"  MTA : {t.hostname} [{t.ip}]")
    else:
        print("  MTA : 不明 (Received: ヘッダーから取得できませんでした)")
    if t.tls_ok:
        ver_str = f" {t.tls_version}" if t.tls_version else ""
        cipher_str = f" / {t.tls_cipher}" if t.tls_cipher else ""
        print(f"  TLS : ✅ {t.tls_protocol}{ver_str}{cipher_str}")
    else:
        proto_str = t.tls_protocol if t.tls_protocol else "不明"
        print(f"  TLS : ❌ 暗号化なし ({proto_str})")

    print("\n--- 認証 ---")
    a = result.auth
    print(f"  SPF  : {_icon(a.spf_pass)} {a.spf_status.upper()}")
    if a.spf_advice:
        print(f"         ↳ {a.spf_advice}")
    print(f"  DKIM : {_icon(a.dkim_pass)} {a.dkim_status.upper()}")
    if a.dkim_advice:
        print(f"         ↳ {a.dkim_advice}")
    print(f"  DMARC: {_icon(a.dmarc_pass)} {a.dmarc_status.upper()}")
    if a.dmarc_advice:
        print(f"         ↳ {a.dmarc_advice}")

    print("\n--- エンコーディング ---")
    e = result.encoding
    print(f"  宣言charset : {e.declared_charset} {_icon(e.charset_valid)} {e.charset_note}")
    if e.expected_charset:
        print(f"  期待charset : {e.expected_charset} {_icon(e.expected_charset_ok)}", end="")
        print("" if e.expected_charset_ok else f"  ↳ 宣言({e.declared_charset})と不一致")
    if e.detected_charset:
        match_icon = _icon(e.charset_match)
        print(
            f"  推定charset : {e.detected_charset} (信頼度スコア:{e.detection_confidence:.3f}) {match_icon}"
        )
        if not e.charset_match:
            print(f"         ↳ 宣言({e.declared_charset})と推定({e.detected_charset})が不一致です")
    print(f"  テキスト本文: {_icon(not e.plain_garbled)} 文字化けスコア={e.plain_score}")
    for d in e.plain_details:
        print(f"         ↳ {d}")
    print(f"  HTML本文    : {_icon(not e.html_garbled)} 文字化けスコア={e.html_score}")
    for d in e.html_details:
        print(f"         ↳ {d}")

    print("\n--- HTML構造 ---")
    h = result.html
    print(f"  {_ok(h.ok)} 問題数={h.issue_count}")
    for issue in h.issues:
        print(f"   ❌ {issue}")

    if result.unsubscribe.checked:
        print("\n--- 購読解除ヘッダー ---")
        u = result.unsubscribe
        print(f"  List-Unsubscribe      : {_icon(u.has_list_unsubscribe)}", end="")
        print(f"  {u.list_unsubscribe_value}" if u.list_unsubscribe_value else "  (なし)")
        print(f"  List-Unsubscribe-Post : {_icon(u.has_list_unsubscribe_post)}", end="")
        print(f"  {u.list_unsubscribe_post_value}" if u.list_unsubscribe_post_value else "  (なし)")

    if result.custom_headers:
        print("\n--- カスタムヘッダー ---")
        for name, value in result.custom_headers.items():
            print(f"  {name}: {value}")

    print("\n--- 原稿比較 ---")
    c = result.compare
    if c.skipped:
        print(f"  スキップ: {c.note}")
    elif c.error:
        print(f"  エラー: {c.error}")
    else:
        print(f"  {c.note}")
        print(f"  テキスト: {_ok(c.plain_match)} / HTML: {_ok(c.html_match)}")
        if c.diff_plain:
            print("\n  [テキスト差分]")
            for line in c.diff_plain.splitlines():
                print(f"  {line}")
        if c.diff_html:
            print("\n  [HTML差分]")
            for line in c.diff_html.splitlines():
                print(f"  {line}")


def print_summary(results: list[VerifyResult], config: Config) -> None:
    """MTA別サマリーを表示する"""

    print("\n" + "=" * 60)
    print("===== MTA別サマリー =====")

    if config.expected_mta_count > 0:
        actual = len(results)
        count_ok = actual >= config.expected_mta_count
        print(f"  MTA台数: {_icon(count_ok)} 取得 {actual}件 / 期待 {config.expected_mta_count}台")
        if not count_ok:
            print("         ↳ 期待台数に満たないメールが未着の可能性があります")
    else:
        print(f"  MTA台数: {len(results)}件取得 (台数チェックなし)")

    print()
    for r in results:
        mta_label = r.mta.hostname or r.mta.ip or "MTA不明"
        tls_str = (
            f"TLS:{r.mta.tls_version or r.mta.tls_protocol or '?'}" if r.mta.tls_ok else "TLS:❌"
        )
        charset_str = r.encoding.declared_charset
        ng_items = []
        if not r.mta.tls_ok:
            ng_items.append("TLS")
        if not r.auth.all_passed:
            failed = [
                p.upper() for p in ("spf", "dkim", "dmarc") if not getattr(r.auth, f"{p}_pass")
            ]
            ng_items.append(f"認証({','.join(failed)})")
        if not r.encoding.ok:
            ng_items.append("エンコーディング")
        if not r.compare.skipped and not (r.compare.plain_match and r.compare.html_match):
            ng_items.append("原稿比較")
        if r.unsubscribe.checked and not r.unsubscribe.ok:
            ng_items.append("購読解除ヘッダー")

        status = _ok(r.overall_ok)
        detail = f"  NG項目: {', '.join(ng_items)}" if ng_items else ""
        print(f"  {status}  {mta_label}  [{tls_str}]  charset:{charset_str}{detail}")

    ng_count = sum(1 for r in results if not r.overall_ok)
    print(f"\n  合計: {len(results)}件中 {ng_count}件NG")
    print("=" * 60)


def print_batch_summary(
    batch: list[tuple[str, list[VerifyResult], Config]],
) -> None:
    """全検索条件の横断サマリーを表示する"""

    print("\n" + "=" * 70)
    print("===== 全条件 横断サマリー =====\n")

    total_results = 0
    total_ng = 0

    for label, results, config in batch:
        count = len(results)
        ng_count = sum(1 for r in results if not r.overall_ok)
        total_results += count
        total_ng += ng_count

        if config.expected_mta_count > 0:
            mta_ok = count >= config.expected_mta_count
            mta_str = f"{count}/{config.expected_mta_count}台 {_icon(mta_ok)}"
        else:
            mta_ok = True
            mta_str = f"{count}件"

        condition_ok = mta_ok and ng_count == 0
        print(f"  {_icon(condition_ok)}  {label:<32}  MTA:{mta_str}  NG:{ng_count}件")

        for r in results:
            if not r.overall_ok:
                mta_name = r.mta.hostname or r.mta.ip or "MTA不明"
                items: list[str] = []
                if not r.mta.tls_ok:
                    items.append("TLS")
                if not r.auth.all_passed:
                    failed = [
                        p.upper()
                        for p in ("spf", "dkim", "dmarc")
                        if not getattr(r.auth, f"{p}_pass")
                    ]
                    items.append(f"認証({','.join(failed)})")
                if not r.encoding.ok:
                    items.append("エンコーディング")
                if not r.html.ok:
                    items.append("HTML構造")
                if not r.compare.skipped and not (r.compare.plain_match and r.compare.html_match):
                    items.append("原稿比較")
                print(f"       ↳ {mta_name}: {', '.join(items)}")

    print(f"\n  全体: {len(batch)}条件 / {total_results}件検証 / {total_ng}件NG")
    print("=" * 70)


# =====================================================================
# ファイル保存
# =====================================================================


def save_json(result: VerifyResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    msg_id_part = result.message_id[:8] if result.message_id else "noid"
    out = output_dir / f"result_{ts}_{msg_id_part}.json"
    out.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return out


_CSV_HEADERS = [
    "label",
    "overall",
    "provider",
    "subject",
    "from",
    "date",
    "mta",
    "ip",
    "tls",
    "tls_version",
    "tls_cipher",
    "spf",
    "dkim",
    "dmarc",
    "charset",
    "expected_charset_ok",
    "plain_garbled",
    "html_garbled",
    "compare_plain",
    "compare_html",
    "ng_items",
]


def _result_to_row(label: str, r: VerifyResult) -> dict:
    ng_items: list[str] = []
    if not r.mta.tls_ok:
        ng_items.append("TLS")
    if not r.auth.all_passed:
        failed = [p.upper() for p in ("spf", "dkim", "dmarc") if not getattr(r.auth, f"{p}_pass")]
        ng_items.append(f"認証({','.join(failed)})")
    if not r.encoding.ok:
        ng_items.append("エンコーディング")
    if not r.compare.skipped and not (r.compare.plain_match and r.compare.html_match):
        ng_items.append("原稿比較")

    return {
        "label": label,
        "overall": _ok_csv(r.overall_ok),
        "provider": r.provider,
        "subject": r.subject,
        "from": r.from_addr,
        "date": r.date,
        "mta": r.mta.hostname or r.mta.ip,
        "ip": r.mta.ip,
        "tls": _ok_csv(r.mta.tls_ok),
        "tls_version": r.mta.tls_version,
        "tls_cipher": r.mta.tls_cipher,
        "spf": r.auth.spf_status.upper(),
        "dkim": r.auth.dkim_status.upper(),
        "dmarc": r.auth.dmarc_status.upper(),
        "charset": r.encoding.declared_charset,
        "expected_charset_ok": _ok_csv(r.encoding.expected_charset_ok)
        if r.encoding.expected_charset
        else "-",
        "plain_garbled": _ok_csv(not r.encoding.plain_garbled),
        "html_garbled": _ok_csv(not r.encoding.html_garbled),
        "compare_plain": _tri(r.compare.plain_match, r.compare.skipped),
        "compare_html": _tri(r.compare.html_match, r.compare.skipped),
        "ng_items": " / ".join(ng_items),
    }


def save_csv(
    batch: list[tuple[str, list[VerifyResult]]],
    results_dir: str,
) -> Path:
    """検証結果を CSV に保存して保存先パスを返す"""
    output_dir = Path(results_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = output_dir / f"result_{ts}.csv"

    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_HEADERS)
        writer.writeheader()
        for label, results in batch:
            for r in results:
                writer.writerow(_result_to_row(label, r))

    return out
