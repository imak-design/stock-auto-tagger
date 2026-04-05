# Stock Auto Tagger

Gemini AI を使ってストック素材（画像・動画・ベクターEPS）のタイトル・タグを自動生成し、Adobe Stock / Shutterstock / Pixta へブラウザ経由で一括アップロード・審査提出するツールです。

## できること

1. **AI タグ自動生成**: Gemini 2.5 Flash で画像・動画を解析し、3サイト分のタイトル・キーワード・カテゴリを一括生成
2. **バッチ処理**: 画像を最大10枚ずつまとめて1リクエストで解析（API消費を大幅削減）
3. **CSV 出力**: Adobe Stock / Shutterstock 用の CSV を自動作成
4. **メタデータ埋め込み**: Pixta 用に IPTC/XMP メタデータを画像に直接埋め込み
5. **全自動パイプライン**: タグ生成 → 3サイトアップロード → ファイル移動まで一気通貫
6. **ブラウザ自動アップロード**: Playwright で各サイトにファイルをアップロード → CSV 適用 → 審査提出まで全自動
7. **バリエーションリネーム**: 色違い素材を AI が色名で自動リネーム
8. **ベクター(EPS)対応**: EPS + PNG → XMP 埋め込み → ZIP 化 → アップロード

## 対応サイト

| サイト | 画像 | 動画 | ベクター |
|--------|:----:|:----:|:--------:|
| Adobe Stock | JPG/PNG | MP4/MOV | EPS |
| Shutterstock | JPG | MP4/MOV | EPS |
| Pixta | JPG/PNG | MP4/MOV | ZIP(EPS+PNG) |

---

## セットアップ

### 1. Python のインストール

#### Mac
```bash
# Homebrew をインストール（まだの場合）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Python をインストール
brew install python
```

#### Windows
[python.org](https://www.python.org/downloads/) からインストーラーをダウンロード。
インストール時に **「Add Python to PATH」にチェック** を入れてください。

### 2. Git のインストール（Windows のみ）

Windows には Git が入っていないため、別途インストールが必要です（Mac は初回に自動インストールされます）。

[git-scm.com](https://git-scm.com/downloads/win) からインストーラーをダウンロードし、デフォルト設定のままインストールしてください。

> Git をインストールしたくない場合は、このページ上部にある緑色の **「Code」ボタン → 「Download ZIP」** からダウンロードし、展開してください。その場合、次の手順3（リポジトリのクローン）はスキップしてください。

### 3. このリポジトリをダウンロード

```bash
# リポジトリをクローン
git clone https://github.com/imak-design/stock-auto-tagger.git
cd stock-auto-tagger
```

#### Mac の場合（仮想環境が必須）

```bash
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt
brew install python-tk@3.14   # Tkinter（GUIに必要）
playwright install chromium
```

#### Windows の場合

```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. 設定ファイルを作成

```bash
cp config.example.json config.json
```

`config.json` を開いて設定を入力してください：

```json
{
  "input_folder": "/Users/あなた/Desktop/stock_input",
  "variation_folder": "/Users/あなた/Desktop/stock_variation",
  "backup_folder": "",
  "enabled_sites": ["adobe", "shutterstock", "pixta"]
}
```

| 項目 | 説明 |
|------|------|
| `input_folder` | 素材を置くフォルダ（JPG/PNG/MP4 を入れる） |
| `variation_folder` | バリエーション素材フォルダ（工程0で使用、不要なら空欄） |
| `backup_folder` | バックアップ先（不要なら空欄） |
| `enabled_sites` | アップロードするサイト（不要なサイトを配列から削除） |
| `test_mode` | `true` にするとアップロードまで実行し審査申請をスキップ（GUI からも切替可） |

### 5. Gemini API キーの設定

API キーはプロジェクトフォルダとは別の場所に保存します。プロジェクトフォルダを共有しても API キーが漏れる心配がありません。

**保存先:**

| OS | パス |
|---|---|
| Mac | `~/.stock-auto-tagger/.env` |
| Windows | `C:\Users\ユーザー名\.stock-auto-tagger\.env` |

以下のコマンドでフォルダ作成と `.env` ファイルの編集ができます。

**Mac:**
```bash
mkdir -p ~/.stock-auto-tagger
echo "GEMINI_API_KEY=ここにAPIキーを貼り付け" > ~/.stock-auto-tagger/.env
```
作成後に編集する場合：
```bash
open -e ~/.stock-auto-tagger/.env
```

**Windows:**
```bash
mkdir %USERPROFILE%\.stock-auto-tagger
echo GEMINI_API_KEY=ここにAPIキーを貼り付け > %USERPROFILE%\.stock-auto-tagger\.env
```
作成後に編集する場合：
```bash
notepad %USERPROFILE%\.stock-auto-tagger\.env
```

API キーは [Google AI Studio](https://aistudio.google.com/apikey) で無料取得できます。

> **無料枠の目安（Gemini 2.5 Flash）**: 無料 API キーは **1日20リクエスト**（1分5リクエスト）の上限があります。バッチ処理により画像最大10枚 = 1リクエストで処理するため：
> - **工程1のみ**（タグ生成）: 1日 **約100〜200枚**（画像のみの場合。動画は1本 = 1リクエスト）
> - **工程0から実行**（リネーム + タグ生成）: リネームにも1リクエスト使うため、やや少なくなります
>
> それ以上処理する場合は有料プランをご検討ください。上限は太平洋時間の深夜0時（日本時間 17:00）にリセットされます。
> ※ API の仕様・制限は変更される場合があります。最新情報は [Google AI Studio](https://aistudio.google.com/) の公式ドキュメントを参照してください。

> **API キーの取り扱いについて**
> - API キーは必ず上記の `.env` ファイルに書いてください。プロジェクトフォルダの外に保存されるため、フォルダを共有しても漏れません
> - `config.json` など他のファイルには API キーを書かないでください
> - Claude などの AI チャットに API キーを貼り付けると会話ログに残る可能性があります。キーの設定は必ずご自身でテキストエディタから直接行ってください

### 6. 各サイトにログインしてセッション保存

使いたいサイトだけでOKです：

```bash
python adobe_login.py        # Adobe Stock
python shutterstock_login.py  # Shutterstock
python pixta_login.py         # Pixta
```

ブラウザが開くので手動でログイン → セッションが自動保存されます。

### 7. アプリ起動

**Windows**: `起動する.bat` をダブルクリック、または：
```bash
python app.py
```

**Mac**: 初回のみ、ターミナルで実行権限を付与してください：
```bash
chmod +x ~/Desktop/stock-auto-tagger/起動.command
```
その後 `起動.command` をダブルクリック、または：
```bash
source venv/bin/activate
python app.py
```

---

## 使い方

### GUI の工程ボタン

| ボタン | 処理内容 |
|--------|----------|
| **工程0** リネーム → 全自動 | バリエーションリネーム → 工程1〜5を全自動実行 |
| **工程1** タグ生成 → 全自動 | Gemini解析（バッチ処理） → CSV生成 → メタデータ埋込 → 工程2〜5を自動実行 |
| **工程2** Adobe | ブラウザアップロード → CSV適用 → 審査提出 |
| **工程3** Shutterstock | ブラウザアップロード → CSV適用 → 審査提出 |
| **工程4** Pixta | 画像 + 動画 + Vector → アップロード → 審査申請 |
| **工程5** ファイル移動 | 処理済みファイルをアーカイブフォルダへ移動 |

### 基本的な流れ

1. `input_folder` に素材ファイル（JPG/PNG/MP4）を入れる
2. アプリを起動して **工程1** を押す → タグ生成から全サイトアップロード、ファイル移動まで全自動で実行

バリエーションリネームも行う場合は **工程0** を押してください。

> 起動時に素材フォルダの内容から **API概算リクエスト数** が自動表示されます。アップロード前には **ファイルサイズ制限**（Adobe 45MB / Pixta PNG 30MB / Shutterstock 50MB / Pixta 50枚）のバリデーションも自動実行されます。

---

## フォルダ構成

```
input_folder/                  ← 素材を置くフォルダ
├── image1.jpg
├── image2.png
├── video1.mp4
├── AI/                        ← AI生成素材（任意）
│   └── ai_image.png
├── Photo/                     ← 写真素材（任意）
│   └── DSC01234.jpg
├── Vector/                    ← ベクター素材（任意）
│   └── 260402_icon/
│       ├── 260402_icon.eps
│       └── 260402_icon.png
└── csv_output/                ← CSV 自動出力先（自動作成）

variation_folder/              ← バリエーション素材（工程0用、任意）
├── 01/ 〜 10/                ← 画像バリエーション
└── movie/01/ 〜 10/          ← 動画バリエーション
```

---

## ファイル振り分けルール

| ファイル種別 | Adobe Stock | Shutterstock | Pixta |
|:----------:|:-----------:|:------------:|:-----:|
| PNG | 対象 | 対象外 | 対象 |
| JPG | 対象 | 対象 | 対象 |
| 動画 | 対象 | 対象 | 対象 |
| EPS | 対象 | 対象 | ZIP(EPS+PNG) |

> Shutterstock は PNG 非対応のため、JPG と動画のみアップロードされます。

### AI / 写真フォルダの振り分け

素材フォルダ内に `AI/` や `Photo/` サブフォルダを作ると、各サイトで適切なカテゴリ設定が自動適用されます。

| フォルダ | Adobe Stock | Shutterstock | Pixta |
|:--------:|:-----------:|:------------:|:-----:|
| 通常（直下） | イラスト | 通常 | イラストページ |
| `Photo/` | 写真カテゴリ | 通常 | 写真ページ |
| `AI/` | イラスト + AI生成チェック | 対象外 | イラストページ + AI生成チェック |

- **写真素材**: `Photo/` フォルダに入れると、Adobe では「写真」カテゴリ、Pixta では写真専用ページにアップロードされます
- **AI生成素材**: `AI/` フォルダに入れると、Adobe では AI生成チェックボックスが自動ON、Pixta でも AI生成フラグが設定されます。Shutterstock は AI 作品登録不可のため除外されます
- フォルダがなければ従来通り全ファイルが通常素材として処理されます

---

## テストモード

GUI の「テストモード」チェックボックスをONにすると、各サイトで **審査申請の手前で処理が停止** します。

- **Adobe Stock**: ファイルアップロード → CSV適用 → コンテンツタイプ設定 → 全選択まで完了し、「審査に登録」ボタンの手前で停止
- **Shutterstock**: ファイルアップロード → CSV適用まで完了し、提出ボタンの手前で停止
- **Pixta 画像**: ファイルアップロード → AIチェック → 全選択まで完了し、「選択した作品を登録」ボタンの手前で停止
- **Pixta 動画**: ファイルアップロード → サムネイル生成待機 → タイトル/タグ入力まで完了し、審査申請の手前で停止

停止後はブラウザが開いたままになるので、**タイトルやタグを手動で確認・修正してから審査申請ボタンを押す** ことができます。ブラウザ内でページ遷移（審査申請ページへ移動など）しても問題ありません。

確認が終わったら **ブラウザを閉じると自動的に次のサイトの処理に進みます**。ブラウザ待機中はポップアップで通知されます。

テストモード時はファイル移動もスキップされるため、同じ素材で繰り返しテストできます。各工程の完了時には処理時間が表示されます。

---

## カスタマイズ

### プロンプトの編集

`prompts/` フォルダ内のテキストファイルを編集すると、AI のタグ生成ルールを変更できます：

- `prompts/image_prompt.txt` — 画像用プロンプト
- `prompts/video_prompt.txt` — 動画用プロンプト

Python コードを触らずに、テキストファイルを編集するだけでカスタマイズ可能です。

### Claude でカスタマイズ

このツールは Claude（AI アシスタント）を使って自分の環境に合わせてカスタマイズできます。

例えば：
- 「Adobe Stock だけ使いたいので、Shutterstock と Pixta の処理を削除して」
- 「動画は扱わないので、画像だけに対応するシンプル版にして」
- 「タグの生成ルールを変えたい」

Claude にこのプロジェクトのコードを見せて、やりたいことを伝えるだけでOKです。

---

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `Session expired` | 該当の `*_login.py` を再実行 |
| Gemini 429 エラー | 自動リトライで回復待ち（無料枠: 1分15リクエスト） |
| Pixta タグ 0個 | ログで Gemini レスポンスを確認 |
| アップロードが途中で止まる | セッション期限切れの可能性 → 再ログイン |

---

## 技術スタック

- **Python 3.10+** / Tkinter（GUI）
- **Google Gemini 2.5 Flash**（タグ生成）/ **Flash-Lite**（リネーム・コスト削減）
- **Playwright Chromium**（ブラウザ自動化）
- **Pillow / NumPy**（画像処理・背景検出）

---

## ライセンス

MIT License
