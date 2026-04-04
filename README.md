# Stock Auto Tagger

Gemini AI を使ってストック素材（画像・動画・ベクターEPS）のタイトル・タグを自動生成し、Adobe Stock / Shutterstock / Pixta へブラウザ経由で一括アップロード・審査提出するツールです。

## できること

1. **AI タグ自動生成**: Gemini 2.5 Flash で画像・動画を解析し、3サイト分のタイトル・キーワード・カテゴリを一括生成
2. **CSV 出力**: Adobe Stock / Shutterstock 用の CSV を自動作成
3. **メタデータ埋め込み**: Pixta 用に IPTC/XMP メタデータを画像に直接埋め込み
4. **ブラウザ自動アップロード**: Playwright で各サイトにファイルをアップロード → CSV 適用 → 審査提出まで全自動
5. **バリエーションリネーム**: 色違い素材を AI が色名で自動リネーム
6. **ベクター(EPS)対応**: EPS + PNG → XMP 埋め込み → ZIP 化 → アップロード

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

### 2. このリポジトリをダウンロード

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

### 3. 設定ファイルを作成

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

### 4. Gemini API キーの設定

プロジェクトフォルダに `.env` ファイルを作成し、API キーを記入してください：

```
GEMINI_API_KEY=あなたのGemini APIキー
```

API キーは [Google AI Studio](https://aistudio.google.com/apikey) で無料取得できます。

> **注意**: `.env` ファイルは `.gitignore` に含まれているため、Git にはアップロードされません。`config.json` には API キーを書かないでください。また、Claude などの AI チャットに API キーを貼り付けると会話ログに残る可能性があるため、キーの設定は必ずご自身でテキストエディタから直接行ってください。

### 5. 各サイトにログインしてセッション保存

使いたいサイトだけでOKです：

```bash
python adobe_login.py        # Adobe Stock
python shutterstock_login.py  # Shutterstock
python pixta_login.py         # Pixta
```

ブラウザが開くので手動でログイン → セッションが自動保存されます。

### 6. アプリ起動

**Windows**: `起動する.bat` をダブルクリック、または：
```bash
python app.py
```

**Mac**: `起動.command` をダブルクリック、または：
```bash
source venv/bin/activate
python app.py
```

---

## 使い方

### GUI の工程ボタン

| ボタン | 処理内容 |
|--------|----------|
| **工程0** リネーム→タグ生成 | バリエーションリネーム → 工程1〜5を自動実行 |
| **工程1** タグ生成 & CSV出力 | Gemini解析 → CSV生成 → メタデータ埋込 |
| **工程2** Adobe | ブラウザアップロード → CSV適用 → 審査提出 |
| **工程3** Shutterstock | ブラウザアップロード → CSV適用 → 審査提出 |
| **工程4** Pixta | 画像 + 動画 + Vector → アップロード → 審査申請 |
| **工程5** ファイル移動 | 処理済みファイルをアーカイブフォルダへ移動 |

### 基本的な流れ

1. `input_folder` に素材ファイル（JPG/PNG/MP4）を入れる
2. アプリを起動して **工程1** を実行（AI がタグを自動生成）
3. **工程2〜4** で各サイトにアップロード
4. **工程5** で処理済みファイルを移動

### 一気通貫モード

**工程0** を押すと、バリエーションリネームから全サイトアップロード、ファイル移動まで全自動で実行します。

---

## フォルダ構成

```
input_folder/                  ← 素材を置くフォルダ
├── image1.jpg
├── image2.png
├── video1.mp4
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
- **Google Gemini 2.5 Flash**（AI 解析）
- **Playwright Chromium**（ブラウザ自動化）
- **Pillow / NumPy**（画像処理・背景検出）

---

## ライセンス

MIT License
