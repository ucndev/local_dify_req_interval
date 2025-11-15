# Dify Slack History Loop

Dify APIを使用してSlackチャンネルの履歴を一定間隔で取得するPythonスクリプトです。

## 概要

このスクリプトは以下の機能を提供します：

- Dify APIを使用してSlackチャンネルの履歴を取得
- カーソルベースのページネーションで全履歴を順次取得
- 中断・再開可能（状態ファイルで進捗を保存）
- リクエスト間隔を指定可能
- エラー時の自動リトライ

## 必要な環境

- Python 3.x
- 必要なパッケージ：
  - `requests`
  - `python-dotenv`

## セットアップ

### 1. 依存パッケージのインストール

```bash
pip install requests python-dotenv
```

### 2. 環境変数の設定

プロジェクトルートに `.env` ファイルを作成し、以下の環境変数を設定します：

#### 必須の環境変数

```env
DIFY_ENDPOINT=https://your-dify-api-endpoint/v1/workflows/run
DIFY_API_KEY=your-api-key-here
CHANNEL_ID=your-slack-channel-id
```

#### オプションの環境変数

```env
# DifyユーザーID（デフォルト: slack-history-import）
DIFY_USER_ID=slack-history-import

# Slack履歴の取得範囲（UNIXタイムスタンプ、文字列として指定）
OLDEST_TS=1704034800
LATEST_TS=1762354800

# 1回のリクエストで取得するメッセージ数（デフォルト: 5）
LIMIT=5

# リクエスト間隔（分単位、デフォルト: 1）
REQUEST_INTERVAL_MIN=1

# リトライ設定
MAX_RETRIES=3              # Dify内部エラー時の最大リトライ回数（デフォルト: 3）
RETRY_INTERVAL_SEC=5       # リトライ間隔（秒単位、デフォルト: 5）

# 状態ファイルのパス（デフォルト: ./cursor.state.json）
STATE_FILE=./cursor.state.json
```

### 環境変数の説明

| 環境変数 | 必須 | デフォルト | 説明 |
|---------|------|-----------|------|
| `DIFY_ENDPOINT` | ✓ | - | Dify API のエンドポイントURL |
| `DIFY_API_KEY` | ✓ | - | Dify API の認証キー |
| `CHANNEL_ID` | ✓ | - | 取得対象のSlackチャンネルID |
| `DIFY_USER_ID` | - | `slack-history-import` | Dify APIリクエストのユーザーID |
| `OLDEST_TS` | - | なし | 遡る下限のタイムスタンプ（文字列）。ここまで遡ったら停止 |
| `LATEST_TS` | - | なし | 取得開始位置のタイムスタンプ（文字列）。未指定なら最新から開始 |
| `LIMIT` | - | `5` | 1回のリクエストで取得するメッセージ数 |
| `REQUEST_INTERVAL_MIN` | - | `1` | リクエスト間隔（分） |
| `MAX_RETRIES` | - | `3` | Dify内部エラー時の最大リトライ回数 |
| `RETRY_INTERVAL_SEC` | - | `5` | リトライ間隔（秒） |
| `STATE_FILE` | - | `./cursor.state.json` | 進捗状態を保存するファイルのパス |

**重要**: Slack履歴は**最新から過去に向かって遡る**形で取得されます：
- `LATEST_TS` 未指定の場合、最新のメッセージから取得開始
- `OLDEST_TS` を指定すると、その時刻まで遡ったら停止
- 例: `OLDEST_TS=1704034800`（2024-01-01）なら、最新から2024年1月1日まで遡る

## 使い方

### 基本的な実行

```bash
python3 dify_slack_history_loop.py
```

### テスト実行（1回だけ実行）

設定やAPI接続を確認するために、ループせずに1回だけ実行する：

```bash
python3 dify_slack_history_loop.py --once
```

このモードでは：
- 1バッチのみ取得して終了（取得件数は `LIMIT` 環境変数で指定、デフォルト5件）
- エラーが発生した場合は即座に終了（リトライなし）
- 状態ファイルは正しく更新されるので、次回の実行で続きから再開可能

### ヘルプの表示

利用可能なオプションを確認する：

```bash
python3 dify_slack_history_loop.py --help
```

### 実行の流れ

1. `.env` ファイルから設定を読み込み
2. 状態ファイル（`cursor.state.json`）が存在する場合は読み込み
3. 前回の続きから、または最初（最新のメッセージ）からDify APIを呼び出し
4. 1回のリクエストで指定件数のメッセージを取得（`LIMIT` 環境変数、デフォルト5件）
5. 結果を確認：`oldest_dt` で各バッチの最も古いメッセージの日時を表示
6. `OLDEST_TS` に到達したか、`next_cursor` が空になったら終了
7. それ以外は指定した間隔（デフォルト1分）待機して次のバッチへ
8. **過去に向かって遡りながら**繰り返し

### 中断と再開

- **中断**: `Ctrl+C` を押すと、現在の状態を保存して終了します
- **再開**: 再度スクリプトを実行すると、前回の続きから自動的に再開します

### 状態ファイル

`cursor.state.json` には以下の情報が保存されます：

```json
{
  "cursor": "bmV4dF90czoxNzU4NjgyMjYyMjQ2NzU5",
  "batch_no": 42,
  "finished": false
}
```

- `cursor`: 次に取得するページのカーソル
- `batch_no`: 実行済みバッチ数
- `finished`: 全データの取得が完了したかどうか

### 最初からやり直す

状態ファイルを削除して再実行します：

```bash
rm cursor.state.json
python3 dify_slack_history_loop.py
```

## Dify APIのレスポンス形式

スクリプトは以下の形式のレスポンスを期待します：

```json
{
  "data": {
    "outputs": {
      "message_size": 5,
      "oldest_dt": "2025-09-24 02:54:14",
      "next_cursor": "bmV4dF90czoxNzU4NjgyMjYyMjQ2NzU5"
    }
  }
}
```

または：

```json
{
  "message_size": 5,
  "oldest_dt": "2025-09-24 02:54:14",
  "next_cursor": "bmV4dF90czoxNzU4NjgyMjYyMjQ2NzU5"
}
```

## エラーハンドリング

### 自動リトライ機能

スクリプトは以下の場合に自動的にリトライします：

1. **Dify内部エラー** (`message_size`, `oldest_dt`, `next_cursor` がすべて `None`)
   - 同じcursorで `MAX_RETRIES` 回まで自動リトライ
   - リトライ間隔: `RETRY_INTERVAL_SEC` 秒
   - リトライ回数を超えた場合:
     - `--once` モード: エラーで終了
     - 通常モード: 次の間隔で同じバッチを再試行

2. **APIリクエストの例外エラー** (ネットワークエラー、HTTP 400/500エラーなど)
   - 同じcursorで `MAX_RETRIES` 回まで自動リトライ
   - リトライ回数を超えた場合:
     - `--once` モード: エラーで終了
     - 通常モード: 次の間隔で同じバッチを再試行

### その他のエラーハンドリング

- `Ctrl+C` で中断した場合、状態を保存してから終了します
- 必須の環境変数が設定されていない場合、エラーメッセージを表示して終了します

## トラブルシューティング

### `python-dotenv could not parse statement` エラー

`.env` ファイルの構文エラーです。以下を確認してください：

- コメントは必ず `#` で始める（`;` は使用不可）
- 各行は `KEY=VALUE` の形式にする
- 値に空白を含む場合は引用符で囲む

### 環境変数が読み込まれない

`.env` ファイルがスクリプトと同じディレクトリにあるか確認してください。

### `oldest_ts in input form must be a string` エラー

`OLDEST_TS` や `LATEST_TS` は文字列として扱われます。`.env` ファイルで以下のように指定してください：

```env
OLDEST_TS=1704034800
LATEST_TS=1762354800
```

引用符は不要です。

### `message_size=None oldest_dt=None next_cursor=None` が頻繁に返される

Dify内部エラーの可能性があります。以下を試してください：

- `MAX_RETRIES` を増やす（例: `MAX_RETRIES=5`）
- `RETRY_INTERVAL_SEC` を長くする（例: `RETRY_INTERVAL_SEC=10`）
- Difyのサーバー負荷を確認
- Difyのログを確認

### API呼び出しが失敗する

- `DIFY_ENDPOINT` と `DIFY_API_KEY` が正しいか確認
- ネットワーク接続を確認
- Dify APIのステータスを確認
- リトライ設定を調整（`MAX_RETRIES`, `RETRY_INTERVAL_SEC`）

### 完了済みと表示されて実行されない

```bash
# 状態ファイルを削除して最初からやり直す
rm cursor.state.json
```

または、状態ファイルの `finished` を `false` に手動で変更してください。

## ライセンス

このスクリプトはそのまま提供されます。
