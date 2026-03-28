"""
mailprobe エントリーポイント
============================
単独実行:  uv run mailprobe
バッチ実行: uv run mailprobe --conditions conditions.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from mailprobe.config import Config
from mailprobe.fetcher import GmailFetcher, ImapFetcher, MailFetcher
from mailprobe.models import VerifyResult
from mailprobe.reporter import print_batch_summary, save_csv
from mailprobe.verifier import run_condition

logger = logging.getLogger(__name__)

# =====================================================================
# Fetcher ファクトリ
# =====================================================================

# --provider で指定できるプロバイダー名
# IMAP対応プロバイダーは ImapFetcher.HOSTS から自動導出する（二重管理を避けるため）
_IMAP_PROVIDERS = tuple(ImapFetcher.HOSTS.keys())
PROVIDER_CHOICES = ("gmail", *_IMAP_PROVIDERS, "all")


def _build_fetcher(config: Config, provider: str) -> MailFetcher:
    """プロバイダー名から適切な Fetcher を返す"""
    if provider == "gmail":
        return GmailFetcher(config)

    pconf = config.providers.get(provider)
    if not pconf or not pconf.enabled:
        logger.error(
            "[エラー] プロバイダー '%s' が config.yaml の providers: セクションで enabled: true になっていません",
            provider,
        )
        sys.exit(1)
    return ImapFetcher(
        provider,
        pconf.email,
        app_password=pconf.app_password,
        client_id=pconf.client_id,
        token_cache_path=config.outlook_token_path,
    )


def _enabled_providers(config: Config) -> list[str]:
    """enabled=true のプロバイダー名を返す (gmail を先頭に)"""
    result = []
    if config.providers.get("gmail") and config.providers["gmail"].enabled:
        result.append("gmail")
    for name, pconf in config.providers.items():
        if name != "gmail" and pconf.enabled:
            result.append(name)
    return result


# =====================================================================
# 実行モード
# =====================================================================


def run_single(config: Config, provider: str = "gmail") -> None:
    """config.yaml の単一条件で実行する"""
    if provider == "all":
        providers = _enabled_providers(config)
        if not providers:
            logger.error("[エラー] 有効なプロバイダーが config.yaml に設定されていません")
            sys.exit(1)
        all_results: list[VerifyResult] = []
        for pname in providers:
            fetcher = _build_fetcher(config, pname)
            all_results.extend(run_condition(fetcher, config, provider=pname))
        if all_results:
            label = config.subject or "single"
            out = save_csv([(label, all_results)], config.results_dir)
            logger.info("\n[CSV] %s", out)
    else:
        fetcher = _build_fetcher(config, provider)
        results = run_condition(fetcher, config, provider=provider)
        if results:
            label = config.subject or "single"
            out = save_csv([(label, results)], config.results_dir)
            logger.info("\n[CSV] %s", out)


def run_batch(conditions_path: str, provider: str = "gmail") -> None:
    """CSV/TSV の条件一覧を順番に実行する"""
    path = Path(conditions_path)
    if not path.exists():
        logger.error("[エラー] 条件ファイルが見つかりません: %s", path)
        sys.exit(1)

    # 拡張子で区切り文字を自動判定
    delimiter = "\t" if path.suffix.lower() in (".tsv", ".tab") else ","

    base_config = Config.load()

    if provider == "all":
        providers = _enabled_providers(base_config)
        if not providers:
            logger.error("[エラー] 有効なプロバイダーが config.yaml に設定されていません")
            sys.exit(1)
        fetchers = {pname: _build_fetcher(base_config, pname) for pname in providers}
    else:
        # 認証は1度だけ行う
        fetchers = {provider: _build_fetcher(base_config, provider)}

    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=delimiter))

    if not rows:
        logger.error("[エラー] 条件ファイルに有効な行がありません")
        sys.exit(1)

    logger.info("[バッチ実行] %d件の検索条件を順番に実行します", len(rows))

    batch: list[tuple[str, list[VerifyResult], Config]] = []
    for i, row in enumerate(rows, 1):
        label = row.get("label", f"条件{i}").strip() or f"条件{i}"
        config = base_config.with_overrides(row)

        logger.info("\n%s", "=" * 60)
        logger.info("[%d/%d] %s", i, len(rows), label)

        results: list[VerifyResult] = []
        for pname, fetcher in fetchers.items():
            results.extend(run_condition(fetcher, config, provider=pname))
        batch.append((label, results, config))

    print_batch_summary(batch)

    out = save_csv([(label, results) for label, results, _ in batch], base_config.results_dir)
    logger.info("\n[CSV] %s", out)


# =====================================================================
# エントリーポイント
# =====================================================================


def entry() -> None:
    parser = argparse.ArgumentParser(
        prog="mailprobe",
        description="MTAサーバーが配信したメールを多角的に検証するツール",
    )
    parser.add_argument(
        "--conditions",
        metavar="FILE",
        help="検索条件一覧ファイル (CSV / TSV)。指定しない場合は config.yaml の単一条件で実行。",
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDER_CHOICES,
        default="gmail",
        help=(
            "使用するメールプロバイダー (デフォルト: gmail)。"
            " all を指定すると config.yaml で enabled: true のプロバイダーをすべて使用する。"
            " outlook / icloud は config.yaml の providers: セクションで設定が必要。"
        ),
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.conditions:
        run_batch(args.conditions, provider=args.provider)
    else:
        run_single(Config.load(), provider=args.provider)


if __name__ == "__main__":
    entry()
