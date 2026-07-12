# mailprobe

MTAサーバーが配信したメールを Gmail / Outlook / iCloud 経由で受信し、以下を多角的に検証するツールです。

- **MTA識別 / TLS**: 送信元MTAのホスト名・IPアドレスおよびTLS接続の有無・バージョンを確認
- **認証ヘッダー**: SPF / DKIM / DMARC の Pass/Fail を解析
- **文字コード**: charset宣言・推定・期待値の3点照合、文字化け検出 (UTF-8 / ISO-2022-JP / Shift-JIS)
- **HTML構造**: タグ開閉・DOCTYPE・文字セット meta 等の検証 (参考情報。総合判定に影響しない)
- **購読解除ヘッダー**: List-Unsubscribe / List-Unsubscribe-Post の有無を確認 (オプション)
- **原稿比較**: 送信前の原稿ファイル (.eml / .txt / .html) とメール本文の一致率を確認
- **カスタムヘッダー記録**: `custom_headers` で指定した任意のヘッダーの値を結果に記録
- **MTA別サマリー**: 複数台のMTAからの配信結果を一覧表示し、未着・NG台数を集計

---

## セットアップ手順

### 1. リポジトリを clone

```bash
git clone <repo-url>
cd mailprobe
```

### 2. 依存パッケージのインストール

uv を使う場合 (推奨):
```bash
uv sync
```

pip を使う場合:
```bash
pip install -e .
```

### 3. Google Cloud Console での設定 (初回のみ)

1. https://console.cloud.google.com にアクセス
2. プロジェクトを作成 (または既存プロジェクトを選択)
3. 「APIとサービス」→「ライブラリ」→ **Gmail API** を有効化
4. 「APIとサービス」→「認証情報」→「認証情報を作成」→「OAuth クライアント ID」
   - アプリケーションの種類: **デスクトップアプリ**
   - 名前: `mailprobe` など任意
5. 作成後に「JSONをダウンロード」→ ファイル名を `credentials.json` に変更
6. プロジェクトルートに配置

> `credentials.json` に秘密情報は含まれません。秘密情報は初回認証後に自動生成される `token.json` です。

### 4. Azure Portal での設定 (初回のみ・Outlook を使う場合)

Outlook は IMAP 認証に OAuth2 が必要です。以下の手順で Azure にアプリを登録してください。

1. https://portal.azure.com にアクセスしてサインイン
2. 「Microsoft Entra ID」→「アプリの登録」→「新規登録」
3. 設定:
   - 名前: `mailprobe`（任意）
   - サポートされているアカウントの種類: **「個人用 Microsoft アカウントのみ」**
   - リダイレクト URI: 「パブリック クライアント/ネイティブ」→ `http://localhost`
4. 「登録」後、概要ページの **「アプリケーション (クライアント) ID」** をコピー
5. 左メニュー「Authentication (Preview)」→「詳細設定」→「パブリック クライアント フローを許可する」を **「はい」** に設定して保存
6. Outlook アカウントの設定で IMAP を有効化:
   - https://outlook.live.com → 設定（歯車）→「メール」→「メールを同期する」→ IMAP を有効化

> コピーしたクライアント ID は次の手順で `config.yaml` に記載します。

### 5. 設定ファイルの作成

```bash
cp config.yaml.example config.yaml
```

`config.yaml` を編集:

```yaml
search:
  subject: "検証したいメールの件名"
  after_date: "2025/01/01"
  before_date: ""            # 省略可 (空欄で絞り込まない)
  expected_charset: "utf-8"  # 省略可 (utf-8 / iso-2022-jp / shift_jis)
  expected_mta_count: 3      # 省略可 (0で台数チェックしない)
check_unsubscribe: false       # true にすると購読解除ヘッダーの有無をチェック
custom_headers: []             # 記録したいカスタムヘッダー名のリスト (例: [X-Campaign-ID])

providers:
  outlook:
    enabled: true
    email: "your@outlook.jp"
    client_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"  # Azure Portal で取得したクライアント ID

  icloud:
    enabled: true
    email: "your@icloud.com"
    app_password: ""           # Apple ID → アプリ用パスワードで生成
```

### 6. 原稿ファイルの配置

`originals/` 配下に**検索条件ごとのサブディレクトリ**を作り、原稿ファイルを置きます。
サブディレクトリ名とメールの件名が部分一致すると自動的に紐付けられます。

**推奨: EML形式 (`.eml`)**

送信システムが出力するEMLファイルをそのまま配置できます。text/plain・text/html の両パートを1ファイルで管理でき、charsetも宣言通りに扱われます。

```
originals/
  テストメール_UTF8/
    body.eml
  テストメール_ISO2022/
    body.eml
```

**`.txt` / `.html` 形式も使用可能**

EMLファイルが見つからない場合は `.txt` / `.html` にフォールバックします。

```
originals/
  テストメール/
    body.txt
    body.html
```

> ファイル名は任意です。サブディレクトリ内の `.eml` / `.txt` / `.html` を自動検出します。
> フラット構成 (`originals/テストメール.eml`) も使用できます。
>
> **HTML比較について**: `.html` 原稿（およびEMLのHTMLパート）との比較はタグを除去したテキスト内容の一致率を見ます。タグ構造の差異は検出しません。

### 7. 実行

**単一条件（config.yaml の設定で実行）:**

```bash
uv run mailprobe                          # デフォルト (Gmail)
uv run mailprobe --provider outlook       # Outlook で受信したメールを検証
uv run mailprobe --provider icloud        # iCloud で受信したメールを検証
uv run mailprobe --provider all           # config.yaml で enabled: true のプロバイダーをすべて実行
```

**バッチ実行（CSV/TSV の条件一覧を順番に実行）:**

```bash
cp conditions.csv.example conditions.csv
# conditions.csv を編集して検索条件を列挙する
uv run mailprobe --conditions conditions.csv
uv run mailprobe --conditions conditions.csv --provider outlook
```

**Gmail** の初回実行時はブラウザが開きます。Google アカウントでログインすると `token.json` が自動保存され、次回以降は不要です。

**Outlook** の初回実行時はターミナルに以下のように表示されます:
```
[認証] Outlook: To sign in, use a web browser to open the page https://www.microsoft.com/link and enter the code XXXXXXXX to authenticate.
```
ブラウザで上記 URL を開いてコードを入力してください。認証完了後に `outlook_token.json` が自動保存され、次回以降は不要です。

---

## 実行結果

```
[開始] メール検証 subject='テストメール_UTF8'
       期待charset: utf-8
       期待MTA台数: 3台
[検索] Gmail query: subject:"テストメール_UTF8" after:2025/01/01
3 件を検証します

============================================================
総合判定: ✅ OK
件名    : テストメール_UTF8
送信元  : sender@example.com
受信日時: Mon, 11 Mar 2025 10:00:00 +0900

--- MTA / TLS ---
  MTA : mta1.example.com [192.168.1.1]
  TLS : ✅ ESMTPS TLSv1.3 / TLS_AES_256_GCM_SHA384

--- 認証 ---
  SPF  : ✅ PASS
  DKIM : ✅ PASS
  DMARC: ✅ PASS

--- エンコーディング ---
  宣言charset : utf-8 ✅ UTF-8: 推奨エンコーディング
  期待charset : utf-8 ✅
  推定charset : utf_8 (信頼度スコア:0.000) ✅
  テキスト本文: ✅ 文字化けスコア=0
  HTML本文    : ✅ 文字化けスコア=0

--- HTML構造 ---
  ✅ OK 問題数=0

--- 購読解除ヘッダー ---
  List-Unsubscribe      : ✅  <https://example.com/unsub>, <mailto:unsub@example.com>
  List-Unsubscribe-Post : ✅  List-Unsubscribe=One-Click

--- カスタムヘッダー ---
  X-Campaign-ID: ABC-12345

--- 原稿比較 ---
  原稿ファイル: body.eml | Text一致率: 97.3% | HTML一致率: 95.1%
  テキスト: ✅ OK / HTML: ✅ OK

============================================================
===== MTA別サマリー =====
  MTA台数: ✅ 取得 3件 / 期待 3台

  ✅ OK  mta1.example.com  [TLS:TLSv1.3]  charset:utf-8
  ✅ OK  mta2.example.com  [TLS:TLSv1.3]  charset:utf-8
  ✅ OK  mta3.example.com  [TLS:TLSv1.3]  charset:utf-8

  合計: 3件中 0件NG
============================================================
```

**バッチ実行時の横断サマリー例:**

```
====================================================================
===== 全条件 横断サマリー =====

  ✅  UTF8メール_0311              MTA:3/3台 ✅  NG:0件
  ✅  ISO2022メール_0311           MTA:3/3台 ✅  NG:0件
  ❌  SJISメール_0311              MTA:2/3台 ❌  NG:1件
       ↳ mta3.example.com: エンコーディング

  全体: 3条件 / 8件検証 / 1件NG
====================================================================
```

JSON結果は `results/result_YYYYMMDD_HHMMSS_<msgid>.json` に、CSV一覧は `results/result_YYYYMMDD_HHMMSS.csv` に保存されます。

---

## Slack通知と定期実行への組み込み

配信のたびに人が手動で実行しなくても済むよう、実行後の横断サマリーを Slack にプッシュ通知できます。
cron や配信パイプラインの後段に組み込む使い方を想定しています。

### Slack通知 (`--notify`)

1. Slack で Incoming Webhook を作成し、Webhook URL を取得する
2. URL を設定する (どちらか一方):
   - `config.yaml` の `notify.slack_webhook_url` に記載
   - 環境変数 `MAILPROBE_SLACK_WEBHOOK_URL` に設定 (cron / CI での運用推奨)
3. `--notify` を付けて実行する

```bash
uv run mailprobe --conditions conditions.csv --provider all --notify
```

通知には「✅/❌ の見出し + 横断サマリー本文」が含まれ、NGがあれば通知プレビューの1行目で分かります。

### 終了コード (`--fail-on-ng`)

パイプラインや CI から NG を検知できるよう、`--fail-on-ng` を付けると結果に応じた終了コードを返します。

| 終了コード | 意味 |
|---|---|
| 0 | 全条件OK (NGなし・未着なし) |
| 1 | 実行エラー (設定不備・認証失敗・Slack通知失敗など) |
| 2 | `--fail-on-ng` 指定時にNGまたは未着 (期待MTA台数割れ) があった |

### cron での定期実行例

```bash
# 毎朝9時に前日配信分を検証して Slack に通知する例
0 9 * * * cd /path/to/mailprobe && MAILPROBE_SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..." uv run mailprobe --conditions conditions.csv --provider all --notify --fail-on-ng
```

> 無人実行では OAuth のブラウザ認証が行えないため、事前に手動で1回実行して `token.json` / `outlook_token.json` を生成しておいてください。トークンが失効した場合も再度手動での認証が必要です。

---

## ファイル構成

```
mailprobe/
├── src/
│   └── mailprobe/
│       ├── __init__.py
│       ├── __main__.py      # CLIエントリーポイント (uv run mailprobe)
│       ├── verifier.py      # 検証ロジック本体
│       ├── fetcher.py       # GmailFetcher / ImapFetcher (メール取得の抽象化)
│       ├── auth_gmail.py    # Gmail OAuth2認証
│       ├── auth_outlook.py  # Outlook OAuth2認証 (MSAL device code flow)
│       ├── models.py        # 検証結果のデータクラス定義
│       ├── reporter.py      # 結果の表示・JSON/CSV保存・横断サマリー組み立て
│       ├── notifier.py      # Slack Incoming Webhook 通知
│       └── config.py        # 設定読み込み
├── originals/               # 原稿ファイル置き場
│   └── テストメール_UTF8/   # 検索条件ごとにサブディレクトリを作成
│       └── body.eml         # EML形式推奨 (.txt/.html も可)
├── results/                 # 検証結果JSON (自動生成)
├── credentials.json         # GCPからDL (gitignore)
├── token.json               # OAuth2認証トークン (gitignore)
├── config.yaml              # 検索設定 (gitignore)
├── config.yaml.example      # 設定テンプレート
├── conditions.csv           # バッチ条件一覧 (gitignore)
├── conditions.csv.example   # バッチ条件テンプレート
├── pyproject.toml
└── .gitignore
```

---

## トラブルシューティング

### `credentials.json が見つかりません`
Google Cloud Console からダウンロードしてプロジェクトルートに配置してください。

### `token.json` を削除したい (別アカウントに切り替えたい)
```bash
rm token.json
uv run mailprobe  # 再認証が走ります
```

### `アクセスがブロックされました` (ブラウザ認証時)
Google Cloud Console の「OAuth同意画面」でテストユーザーに自分のメールアドレスを追加してください。

### Outlook 認証トークンを削除したい (別アカウントに切り替えたい)
```bash
rm outlook_token.json
uv run mailprobe --provider outlook  # 再認証が走ります
```

### iCloud で `プロバイダー 'icloud' が enabled: true になっていません` と表示される
`config.yaml` の `providers:` セクションで `enabled: true` にして `email` と `app_password` を設定してください。

`app_password` は config.yaml への直書きのほか、環境変数でも指定できます（CI 等での運用に便利です）。

| プロバイダー | 環境変数名 |
|---|---|
| iCloud | `MAILPROBE_ICLOUD_APP_PASSWORD` |

**iCloud アプリパスワードの発行手順:**
1. https://appleid.apple.com にアクセスしてサインイン（2ファクタ認証あり）
2. 「サインインとセキュリティ」→「アプリ用パスワード」→「アプリ用パスワードを生成する」
3. 用途名を入力（例: `mailprobe`）し、表示された16文字をメモ（再表示不可）
