# NDロードスター（FCRカップ戦仕様）個人売買監視システム＆ダッシュボード

マツダ NDロードスター（初期型 `DBA-ND5RC` / 6MT車）の個人売買情報を **X（旧Twitter）・ヤフオク（個人出品）・メルカリ・ジモティー** から自動収集し、富士チャンピオンレース（FCR）ロードスターカップ参戦に適した車両（`NR-A` / `機械式LSD` / `6点式ロールバー` / `TE37 SONIC 16インチ 7J` 等）を100点満点でスコアリングして **LINE Messaging API** で即時通知する完全無料（GitHub Actions + GitHub Pages）の監視システムです。

---

## 1. ディレクトリ構成

```text
├── .github/
│   └── workflows/
│       └── monitor.yml      # 定期実行＆データ更新コミット用ワークフロー
├── data/
│   ├── config.json          # 監視条件・スコアリング配点・監視間隔設定
│   └── vehicles.json        # 検出済み車両データ履歴（初期モックデータ3件入り）
├── src/
│   ├── monitor.py           # メイン巡回スクリプト
│   ├── scrapers.py          # X, ヤフオク, メルカリ, ジモティー取得モジュール
│   ├── scorer.py            # マッチ度計算・除外判定モジュール
│   └── notifier.py          # LINE Messaging API 送信モジュール
├── docs/                    # GitHub Pages 公開用ディレクトリ
│   ├── index.html           # ダッシュボード画面（Tailwind CSS利用）
│   └── app.js               # データ読込・ソート・定型文コピー・GitHub API更新処理
├── requirements.txt         # Python依存パッケージ一覧
└── README.md                # GitHub Secrets・LINE Bot・GitHub Pagesの初期設定手順書
```

---

## 2. 初期セットアップ手順（完全無料運用）

### 手順1：GitHubリポジトリの作成とプッシュ
1. GitHubで新規 **Publicリポジトリ**（例：`nd5rc-fcr-monitor`）を作成します（Publicリポジトリにすることで、GitHub Actionsの無料実行枠とGitHub Pagesが完全無料で利用できます）。
2. 本プロジェクト一式をリポジトリの `main` ブランチへプッシュします。

### 手順2：LINE Messaging API（公式アカウント）の取得
1. [LINE Developers コンソール](https://developers.line.biz/ja/) にログインし、プロバイダーと **Messaging API チャネル** を新規作成します（無料プラン：月200通まで無料）。
2. チャネル基本設定タブの最下部にある **「あなたのユーザーID」**（`U`から始まる33文字の文字列）を控えます。
3. Messaging API設定タブで **「チャネルアクセストークン（長期）」** を発行して控えます。
4. 作成したLINE公式アカウントのQRコードを読み取り、友だち追加しておきます。

### 手順3：X（旧Twitter）監視用サブアカウントのCookie取得（`X_COOKIES`）
1. 凍結リスク回避のため、普段使いとは別の **監視専用Xサブアカウント** を用意し、PCブラウザでログインします。
2. Chrome拡張機能「Cookie-Editor」等を用いて、`x.com` のCookie一覧を **JSON形式** でエクスポートします（または `{"auth_token": "...", "ct0": "..."}` のJSON形式で用意します）。

### 手順4：GitHub Secrets の登録
1. GitHubリポジトリの **Settings ＞ Secrets and variables ＞ Actions ＞ New repository secret** を開きます。
2. 以下の3つのSecretを登録します：
   - `LINE_CHANNEL_ACCESS_TOKEN` : 手順2で取得したLINEチャネルアクセストークン
   - `LINE_USER_ID` : 手順2で取得したLINEユーザーID
   - `X_COOKIES` : 手順3でエクスポートしたXのCookie JSON文字列

### 手順5：GitHub Actions の書き込み権限と GitHub Pages の有効化
1. **Settings ＞ Actions ＞ General ＞ Workflow permissions** にて、**「Read and write permissions」** を選択して Save します（巡回後に `data/vehicles.json` を自動コミットするために必須です）。
2. **Settings ＞ Pages** を開き、**Build and deployment** の Source を `Deploy from a branch`、Branch を `main` / フォルダを `/ (root)` または `/docs` に設定して Save します。
   - ※ ルート公開（`/ (root)`）にした場合は `https://<ユーザー名>.github.io/<リポジトリ名>/docs/` でアクセスでき、`../data/vehicles.json` を直接参照できます。

### 手順6：スマホ画面からの監視コントロール用 PAT（Personal Access Token）発行
1. ダッシュボードの「監視・配点設定」タブから監視スパン（5分〜24時間・停止）や配点を直接コミット変更する場合、GitHubの **Settings ＞ Developer settings ＞ Personal access tokens ＞ Fine-grained tokens** からトークンを発行します。
2. 対象リポジトリのみを選択し、Repository permissions の **Contents: Read and write** および **Workflows: Read and write** を付与します。
3. 発行したトークンをスマホのダッシュボード画面に入力すると、ブラウザの `localStorage` にのみ安全に保存されます。
