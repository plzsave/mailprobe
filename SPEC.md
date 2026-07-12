# SPEC — mailprobe

MTA サーバーが配信したメールを Gmail / Outlook / iCloud 経由で受信し、多角的に検証する CLI ツール。
セットアップ手順・設定項目の詳細は README.md が正。

## 目的

複数台の MTA からの配信結果を、受信側の実メールボックスで検証する:
MTA 識別 / TLS、SPF / DKIM / DMARC、文字コード(UTF-8 / ISO-2022-JP / Shift-JIS)、
HTML 構造、購読解除ヘッダー、原稿比較、カスタムヘッダー記録、MTA 別サマリー。

## 確定事項(再議論禁止)

- **言語**: Python >= 3.11。パッケージ管理は uv(`uv sync` / `uv run`)。build は hatchling
- **受信プロバイダ**: Gmail は Gmail API(OAuth、google-api-python-client)、
  Outlook は IMAP + OAuth2(msal、パブリッククライアントフロー)、iCloud は IMAP
- **設定は `config.yaml`**(`config.yaml.example` からコピー)。CLI 引数で設定を増やさない
- **秘密情報**: `credentials.json` は秘密ではない。秘密は自動生成される `token.json`。
  いずれも gitignore 対象で、リポジトリにコミットしない
- **HTML 構造検証は参考情報**。総合判定(Pass/Fail)に影響させない
- 文字コード判定は「charset 宣言・推定(charset-normalizer)・期待値」の3点照合
- **リンタ/フォーマッタ**: ruff。型チェック: pyright。テスト: pytest。pre-commit 導入済み

## スコープ外

- メールの送信機能(検証は受信側のみ)
- Web UI / 常駐デーモン化(単発実行の CLI)

## アーキテクチャ

`src/mailprobe/` 単一パッケージ:
`auth_gmail.py` / `auth_outlook.py`(認証)→ `fetcher.py`(取得)→ `verifier.py`(検証)→
`reporter.py`(整形)/ `notifier.py`(通知)。設定は `config.py`、型は `models.py`。
エントリポイントは `mailprobe` スクリプト(`__main__:entry`)。

## DO / DO NOT

- DO: 変更後は `uv run pytest && uv run ruff check . && uv run pyright`
- DO: 新しい検証項目は verifier に純粋関数で足し、I/O(fetcher)と分離する
- DO NOT: `token.json` / `config.yaml`(実値入り)を読み書きの例に使わない
- DO NOT: 検証ロジックにプロバイダ固有の分岐を持ち込まない(取得層で正規化する)

## 検証手順(E2E)

1. `uv run pytest`(資格情報不要のユニットテスト)
2. `uv run ruff check . && uv run pyright`
3. 実メール検証(資格情報が必要・人間の操作): `config.yaml` を用意し `uv run mailprobe`。
   MTA 別サマリーが出力され、未着・NG 台数が集計されること
