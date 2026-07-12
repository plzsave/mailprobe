# mailprobe アーキテクチャ設計

設計上の判断とその背景をまとめたドキュメントです。

---

## なぜ Python + Gmail API raw か

GASの `getBody()` はGoogleがデコード済みのUnicode文字列しか返さない。
文字化け検証には生MIMEバイト列が必須であり、Gmail APIの `format=raw` で取得できる。
`charset_normalizer` でバイト列を直接分析し、charset宣言との照合が可能になる。

---

## なぜ IMAP か (Outlook / iCloud)

- iCloud: 公式APIなし → IMAP + アプリパスワード一択
- Outlook: Microsoft が Basic Auth を廃止 → IMAP + XOAUTH2 (OAuth2) で対応
  Azure Portal でアプリ登録が必要だが、Microsoft Graph API と同コストで生MIMEが取得できる
- IMAP `FETCH RFC822` で生MIMEバイト列が取得可能 (Gmail API rawと同等)

---

## Fetcher の抽象化

Gmail API と IMAP の差異を `MailFetcher` Protocol で吸収している。
`verifier.py` はバイト列 (`bytes`) のみを受け取るため、メール取得方法に依存しない。

```
GmailFetcher  ──┐
ImapFetcher   ──┤──→ list[bytes] ──→ verify_message() ──→ VerifyResult
(将来の拡張)  ──┘
```

新しいメールプロバイダーへの対応は以下の2箇所を変更するだけで済む:

1. `ImapFetcher.HOSTS` にホスト名を追加（`_IMAP_PROVIDERS` は自動導出される）
2. `__main__.py` の `_build_fetcher()` に認証方式を追加（アプリパスワード / OAuth2 等）

---

## 認証情報の管理方針

### Gmail (OAuth2)

`auth_gmail.py` がブラウザ認証フローを提供し、`token.json` をローカルに保存する。
サーバーレス環境ではサービスアカウント認証への差し替えを想定している。
その際 `auth_gmail.py` の `get_gmail_service_sa(key_dict, subject_email)` を使用し、
`key_dict` の取得元（ファイル・環境変数・Secrets Manager等）は呼び出し側の責務とする。

### Outlook (OAuth2 XOAUTH2)

`auth_outlook.py` が MSAL device code flow を提供し、`outlook_token.json` にトークンをキャッシュする。
初回はターミナルに表示される URL とコードをブラウザで入力して認証する。
Azure Portal でのアプリ登録（アプリケーション ID の取得）が事前に必要。

### iCloud (アプリパスワード)

`app_password` は以下の優先順位で解決される:

1. 環境変数 `MAILPROBE_ICLOUD_APP_PASSWORD`（CI・Lambda等での運用推奨）
2. `config.yaml` の `app_password:` フィールド（ローカル開発向け）

---

## カスタムヘッダーの拡張

検証したいカスタムヘッダーは `config.yaml` の `custom_headers:` リストで指定する。
コアはヘッダー名を知らず、結果を `VerifyResult.custom_headers: dict[str, str]` に記録するだけ。

```yaml
custom_headers:
  - X-Campaign-ID
  - X-Mailer-ID
```

---

## キャリアメール対応の方針

キャリアメールはIMAP対応しているため `ImapFetcher` をそのまま利用できる。
ただし以下の制約があるため、現時点では検証環境が整い次第対応する。

- キャリアゲートウェイが送信時の charset を自動変換する場合があり、
  charset不一致が「文字化け」なのか「ゲートウェイ正常変換」なのかを完全には切り分けられない
- 変換仕様は各キャリア非公開であり、変更される可能性がある
- 独自絵文字・機種依存文字の変換正しさの検証はスコープ外

---

## なぜ Web ホスティングではなく「通知の組み込み」か

このツールは日常的に使う道具ではなく「配信のたびに走ってほしい安全網」であり、
人が能動的に起動する設計 (プル型) では問題がない限り誰も実行しない。
需要問題の本質はインターフェース (CLI か Web か) ではなく実行タイミングにある。

そのため Web UI 化より先に、cron / 配信パイプラインからの無人実行と結果のプッシュ通知を整備した:

- `--notify`: 横断サマリーを Slack Incoming Webhook に送信 (`notifier.py`)
- `--fail-on-ng`: NG・未着があれば終了コード2 (パイプラインでの検知用)
- 認証情報は共有実行環境に1セット置けばよく、利用者ごとの OAuth セットアップが不要になる

通知は `reporter.py` の `BatchSummary` (表示から分離したサマリーデータ) を入力に取るため、
通知先の追加 (メール・Teams 等) は `notifier.py` に関数を足すだけでよい。
Slack 送信は標準ライブラリ (urllib) のみで実装し、追加依存を持たない。

フル Web アプリ (履歴閲覧・ダッシュボード) は、この形で利用が定着してから検討する。

---

## なぜ Rust ではないか

- チームメンバーがRustを読めない → 属人化リスクが上がる
- 手動・少量実行のためパフォーマンス要件がない
- 将来安定稼働に入ったタイミングでRust移植を検討する余地はある
