"""
tests/test_batch.py
===================
バッチ実行・横断サマリー・CSV出力・Config.with_overrides のユニットテスト

対象:
  - Config.with_overrides
  - print_batch_summary
  - save_csv
  - run_batch (エラーケース + GmailFetcher をモックした正常系)
"""

from __future__ import annotations

import csv
from unittest.mock import patch

import pytest

from mailprobe.__main__ import print_batch_summary, run_batch, save_csv
from mailprobe.config import Config
from mailprobe.verifier import (
    AuthResult,
    DriveCompareResult,
    EncodingResult,
    HtmlResult,
    MtaResult,
    VerifyResult,
)

# =====================================================================
# テスト用ヘルパー
# =====================================================================


def _make_result(
    *,
    overall_ok: bool = True,
    hostname: str = "mail.example.com",
    ip: str = "203.0.113.1",
    tls_ok: bool = True,
    tls_version: str = "TLSv1.3",
    tls_cipher: str = "TLS_AES_256_GCM_SHA384",
    spf_pass: bool = True,
    dkim_pass: bool = True,
    dmarc_pass: bool = True,
    declared_charset: str = "utf-8",
    expected_charset: str = "",
    plain_garbled: bool = False,
    html_garbled: bool = False,
    compare_skipped: bool = True,
    plain_match: bool = True,
    html_match: bool = True,
) -> VerifyResult:
    """テスト用の VerifyResult を生成する"""
    return VerifyResult(
        message_id="abc123",
        subject="テストメール",
        from_addr="sender@example.com",
        date="Thu, 20 Mar 2025 10:00:00 +0900",
        overall_ok=overall_ok,
        mta=MtaResult(
            hostname=hostname,
            ip=ip,
            tls_ok=tls_ok,
            tls_version=tls_version,
            tls_cipher=tls_cipher,
        ),
        auth=AuthResult(
            spf_status="pass" if spf_pass else "fail",
            spf_pass=spf_pass,
            dkim_status="pass" if dkim_pass else "fail",
            dkim_pass=dkim_pass,
            dmarc_status="pass" if dmarc_pass else "fail",
            dmarc_pass=dmarc_pass,
            all_passed=spf_pass and dkim_pass and dmarc_pass,
        ),
        encoding=EncodingResult(
            ok=not plain_garbled and not html_garbled and True,
            declared_charset=declared_charset,
            expected_charset=expected_charset,
            plain_garbled=plain_garbled,
            html_garbled=html_garbled,
        ),
        html=HtmlResult(ok=True),
        compare=DriveCompareResult(
            skipped=compare_skipped,
            plain_match=plain_match,
            html_match=html_match,
        ),
    )


# =====================================================================
# Config.with_overrides
# =====================================================================


class TestWithOverrides:
    def test_subject_overridden(self):
        config = Config(subject="元の件名")
        result = config.with_overrides({"subject": "新しい件名"})
        assert result.subject == "新しい件名"

    def test_empty_value_not_overridden(self):
        """空文字列は上書きしない (config.yaml の値を引き継ぐ)"""
        config = Config(subject="元の件名")
        result = config.with_overrides({"subject": ""})
        assert result.subject == "元の件名"

    def test_whitespace_only_not_overridden(self):
        """空白のみの値も上書きしない"""
        config = Config(subject="元の件名")
        result = config.with_overrides({"subject": "  "})
        assert result.subject == "元の件名"

    def test_from_key_alias(self):
        """CSVヘッダー 'from' が from_addr に反映される"""
        config = Config(from_addr="old@example.com")
        result = config.with_overrides({"from": "new@example.com"})
        assert result.from_addr == "new@example.com"

    def test_from_addr_key(self):
        """'from_addr' キーでも上書きできる"""
        config = Config(from_addr="old@example.com")
        result = config.with_overrides({"from_addr": "new@example.com"})
        assert result.from_addr == "new@example.com"

    def test_date_fields_overridden(self):
        config = Config(after_date="2025/01/01", before_date="2025/12/31")
        result = config.with_overrides({"after_date": "2025/03/01", "before_date": "2025/03/31"})
        assert result.after_date == "2025/03/01"
        assert result.before_date == "2025/03/31"

    def test_expected_charset_overridden(self):
        config = Config(expected_charset="utf-8")
        result = config.with_overrides({"expected_charset": "iso-2022-jp"})
        assert result.expected_charset == "iso-2022-jp"

    def test_expected_mta_count_int_conversion(self):
        """文字列 '3' が int 3 に変換される"""
        config = Config(expected_mta_count=0)
        result = config.with_overrides({"expected_mta_count": "3"})
        assert result.expected_mta_count == 3

    def test_check_unsubscribe_true(self):
        config = Config(check_unsubscribe=False)
        result = config.with_overrides({"check_unsubscribe": "true"})
        assert result.check_unsubscribe is True

    def test_check_unsubscribe_false(self):
        config = Config(check_unsubscribe=True)
        result = config.with_overrides({"check_unsubscribe": "false"})
        assert result.check_unsubscribe is False

    def test_check_unsubscribe_case_insensitive(self):
        config = Config(check_unsubscribe=False)
        result = config.with_overrides({"check_unsubscribe": "True"})
        assert result.check_unsubscribe is True

    def test_check_unsubscribe_empty_not_changed(self):
        """空欄は変更しない"""
        config = Config(check_unsubscribe=True)
        result = config.with_overrides({"check_unsubscribe": ""})
        assert result.check_unsubscribe is True

    def test_path_fields_not_overridden(self):
        """パス系フィールドは CSV で上書きできない"""
        config = Config(credentials_path="my_creds.json", results_dir="my_results")
        result = config.with_overrides(
            {
                "credentials_path": "other.json",
                "results_dir": "other_results",
            }
        )
        assert result.credentials_path == "my_creds.json"
        assert result.results_dir == "my_results"

    def test_original_config_not_mutated(self):
        """元の Config インスタンスは変更されない"""
        config = Config(subject="元の件名")
        config.with_overrides({"subject": "新しい件名"})
        assert config.subject == "元の件名"

    def test_missing_key_not_overridden(self):
        """CSVに列がない場合も config.yaml の値を引き継ぐ"""
        config = Config(subject="元の件名")
        result = config.with_overrides({})
        assert result.subject == "元の件名"


# =====================================================================
# print_batch_summary
# =====================================================================


class TestPrintBatchSummary:
    def test_all_ok_shows_checkmark(self, capsys):
        batch = [("条件A", [_make_result()], Config())]
        print_batch_summary(batch)
        out = capsys.readouterr().out
        assert "✅" in out
        assert "条件A" in out

    def test_ng_shows_cross(self, capsys):
        batch = [("条件A", [_make_result(overall_ok=False, tls_ok=False)], Config())]
        print_batch_summary(batch)
        out = capsys.readouterr().out
        assert "❌" in out

    def test_ng_count_shown(self, capsys):
        results = [_make_result(), _make_result(overall_ok=False, tls_ok=False)]
        batch = [("条件A", results, Config())]
        print_batch_summary(batch)
        out = capsys.readouterr().out
        assert "NG:1件" in out

    def test_mta_count_check_ok(self, capsys):
        """取得件数 >= expected_mta_count のとき ✅"""
        results = [_make_result(), _make_result()]
        config = Config(expected_mta_count=2)
        batch = [("条件A", results, config)]
        print_batch_summary(batch)
        out = capsys.readouterr().out
        assert "2/2台" in out
        assert "✅" in out

    def test_mta_count_check_ng(self, capsys):
        """取得件数 < expected_mta_count のとき ❌"""
        results = [_make_result()]
        config = Config(expected_mta_count=3)
        batch = [("条件A", results, config)]
        print_batch_summary(batch)
        out = capsys.readouterr().out
        assert "1/3台" in out
        assert "❌" in out

    def test_mta_count_not_set_shows_count_only(self, capsys):
        """expected_mta_count=0 のとき台数チェックなし表示"""
        results = [_make_result(), _make_result()]
        config = Config(expected_mta_count=0)
        batch = [("条件A", results, config)]
        print_batch_summary(batch)
        out = capsys.readouterr().out
        assert "2件" in out

    def test_ng_detail_shows_mta_name_and_items(self, capsys):
        """NG結果の詳細行にMTA名とNG項目が表示される"""
        result = _make_result(overall_ok=False, tls_ok=False, hostname="bad.example.com")
        batch = [("条件A", [result], Config())]
        print_batch_summary(batch)
        out = capsys.readouterr().out
        assert "bad.example.com" in out
        assert "TLS" in out

    def test_ng_detail_auth_items(self, capsys):
        """認証NG時に SPF/DKIM/DMARC が詳細に表示される"""
        result = _make_result(overall_ok=False, spf_pass=False, dkim_pass=False, dmarc_pass=True)
        batch = [("条件A", [result], Config())]
        print_batch_summary(batch)
        out = capsys.readouterr().out
        assert "SPF" in out
        assert "DKIM" in out

    def test_total_line_shown(self, capsys):
        """全体集計行が表示される"""
        batch = [
            ("条件A", [_make_result()], Config()),
            ("条件B", [_make_result(), _make_result()], Config()),
        ]
        print_batch_summary(batch)
        out = capsys.readouterr().out
        assert "2条件" in out
        assert "3件検証" in out

    def test_no_ng_detail_for_ok_result(self, capsys):
        """OK結果には詳細行が表示されない"""
        batch = [("条件A", [_make_result()], Config())]
        print_batch_summary(batch)
        out = capsys.readouterr().out
        assert "↳" not in out


# =====================================================================
# save_csv
# =====================================================================


class TestSaveCsv:
    def test_file_created(self, tmp_path):
        batch = [("label1", [_make_result()])]
        out = save_csv(batch, str(tmp_path))
        assert out.exists()
        assert out.suffix == ".csv"

    def test_csv_headers(self, tmp_path):
        batch = [("label1", [_make_result()])]
        out = save_csv(batch, str(tmp_path))
        with out.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames is not None
            assert "label" in reader.fieldnames
            assert "overall" in reader.fieldnames
            assert "ng_items" in reader.fieldnames

    def test_ok_result_row(self, tmp_path):
        batch = [("my_label", [_make_result()])]
        out = save_csv(batch, str(tmp_path))
        with out.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["label"] == "my_label"
        assert rows[0]["overall"] == "OK"
        assert rows[0]["tls"] == "OK"
        assert rows[0]["spf"] == "PASS"
        assert rows[0]["charset"] == "utf-8"
        assert rows[0]["ng_items"] == ""

    def test_ng_result_ng_items(self, tmp_path):
        """TLS・認証・エンコーディングが NG のとき ng_items に列挙される"""
        result = _make_result(overall_ok=False, tls_ok=False, spf_pass=False, plain_garbled=True)
        batch = [("ng_label", [result])]
        out = save_csv(batch, str(tmp_path))
        with out.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        ng_items = rows[0]["ng_items"]
        assert "TLS" in ng_items
        assert "認証" in ng_items
        assert "エンコーディング" in ng_items

    def test_expected_charset_ok_shown(self, tmp_path):
        """expected_charset が設定されているとき OK/NG を表示する"""
        result = _make_result(declared_charset="utf-8", expected_charset="utf-8")
        result.encoding.expected_charset_ok = True
        batch = [("label1", [result])]
        out = save_csv(batch, str(tmp_path))
        with out.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["expected_charset_ok"] == "OK"

    def test_expected_charset_not_set_shows_dash(self, tmp_path):
        """expected_charset 未設定のとき '-' を表示する"""
        result = _make_result(expected_charset="")
        batch = [("label1", [result])]
        out = save_csv(batch, str(tmp_path))
        with out.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["expected_charset_ok"] == "-"

    def test_compare_skipped_shows_dash(self, tmp_path):
        """原稿比較スキップ時は compare_plain / compare_html が '-'"""
        result = _make_result(compare_skipped=True)
        batch = [("label1", [result])]
        out = save_csv(batch, str(tmp_path))
        with out.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["compare_plain"] == "-"
        assert rows[0]["compare_html"] == "-"

    def test_multiple_conditions_multiple_rows(self, tmp_path):
        """複数条件・複数結果が正しく行数になる"""
        batch = [
            ("条件A", [_make_result(), _make_result()]),
            ("条件B", [_make_result()]),
        ]
        out = save_csv(batch, str(tmp_path))
        with out.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 3
        assert rows[0]["label"] == "条件A"
        assert rows[2]["label"] == "条件B"

    def test_output_dir_created_if_not_exists(self, tmp_path):
        """results_dir が存在しなくても自動作成される"""
        results_dir = tmp_path / "subdir" / "results"
        batch = [("label1", [_make_result()])]
        out = save_csv(batch, str(results_dir))
        assert out.exists()


# =====================================================================
# run_batch
# =====================================================================


class TestRunBatch:
    def test_file_not_found_exits(self, tmp_path):
        """存在しないファイルを指定すると sys.exit(1) する"""
        with pytest.raises(SystemExit) as exc_info:
            run_batch(str(tmp_path / "nonexistent.csv"))
        assert exc_info.value.code == 1

    def test_empty_conditions_exits(self, tmp_path):
        """ヘッダー行のみで条件がない CSV は sys.exit(1) する"""
        csv_file = tmp_path / "conditions.csv"
        csv_file.write_text("label,subject\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            run_batch(str(csv_file))
        assert exc_info.value.code == 1

    def test_normal_csv_run(self, tmp_path):
        """正常な CSV を渡すと各行の run_condition が呼ばれる"""
        csv_file = tmp_path / "conditions.csv"
        csv_file.write_text(
            "label,subject\n条件A,テストメールA\n条件B,テストメールB\n",
            encoding="utf-8",
        )
        mock_result = _make_result()
        with (
            patch("mailprobe.__main__.Config.load", return_value=Config(results_dir=str(tmp_path))),
            patch("mailprobe.__main__.GmailFetcher"),
            patch("mailprobe.__main__.run_condition", return_value=[mock_result]) as mock_run,
        ):
            run_batch(str(csv_file))

        assert mock_run.call_count == 2

    def test_tsv_delimiter_detected(self, tmp_path):
        """拡張子 .tsv はタブ区切りとして読み込まれる"""
        tsv_file = tmp_path / "conditions.tsv"
        tsv_file.write_text(
            "label\tsubject\n条件A\tテストメールA\n",
            encoding="utf-8",
        )
        mock_result = _make_result()
        with (
            patch("mailprobe.__main__.Config.load", return_value=Config(results_dir=str(tmp_path))),
            patch("mailprobe.__main__.GmailFetcher"),
            patch("mailprobe.__main__.run_condition", return_value=[mock_result]) as mock_run,
        ):
            run_batch(str(tsv_file))

        assert mock_run.call_count == 1

    def test_label_fallback_when_empty(self, tmp_path):
        """label 列が空のとき '条件N' にフォールバックする"""
        csv_file = tmp_path / "conditions.csv"
        csv_file.write_text(
            "label,subject\n,テストメール\n",
            encoding="utf-8",
        )
        with (
            patch("mailprobe.__main__.Config.load", return_value=Config(results_dir=str(tmp_path))),
            patch("mailprobe.__main__.GmailFetcher"),
            patch("mailprobe.__main__.run_condition", return_value=[]) as mock_run,
        ):
            run_batch(str(csv_file))

        # run_condition が呼ばれた際の config.subject が上書きされていること
        call_args = mock_run.call_args_list[0]
        config_arg = call_args[0][1]  # 第2引数 (fetcher, config)
        assert config_arg.subject == "テストメール"

    def test_gmail_fetcher_instantiated_once(self, tmp_path):
        """複数条件でも GmailFetcher は1回だけインスタンス化される"""
        csv_file = tmp_path / "conditions.csv"
        csv_file.write_text(
            "label,subject\n条件A,メールA\n条件B,メールB\n条件C,メールC\n",
            encoding="utf-8",
        )
        with (
            patch("mailprobe.__main__.Config.load", return_value=Config(results_dir=str(tmp_path))),
            patch("mailprobe.__main__.GmailFetcher") as mock_fetcher_cls,
            patch("mailprobe.__main__.run_condition", return_value=[]),
        ):
            run_batch(str(csv_file))

        assert mock_fetcher_cls.call_count == 1
