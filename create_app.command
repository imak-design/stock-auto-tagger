#!/bin/zsh
# StockAutoTagger.app をデスクトップに作成するセットアップスクリプト
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
APP_PATH="$HOME/Desktop/StockAutoTagger.app"
ICON_PNG="$PROJECT_DIR/icon.png"

echo "=== StockAutoTagger アプリを作成します ==="
echo "プロジェクト: $PROJECT_DIR"
echo ""

# fileicon が必要
if ! command -v fileicon &> /dev/null; then
    echo "fileicon をインストールします（アイコン設定に必要）..."
    brew install fileicon
fi

# 既存があれば削除
if [ -d "$APP_PATH" ]; then
    echo "既存の StockAutoTagger.app を置き換えます..."
    rm -rf "$APP_PATH"
fi

# AppleScript で .app を生成
osacompile -e "
do shell script \"cd '$PROJECT_DIR' && ./venv/bin/python app.py &> /dev/null &\"
" -o "$APP_PATH"

# アイコンを設定
if [ -d "$APP_PATH" ]; then
    if [ -f "$ICON_PNG" ] && command -v fileicon &> /dev/null; then
        fileicon set "$APP_PATH" "$ICON_PNG"
    fi
    echo ""
    echo "✓ デスクトップに StockAutoTagger.app を作成しました！"
    echo "  ダブルクリックまたは Dock にドラッグして使えます。"
    echo ""
    echo "このスクリプトは今後実行する必要はありません。"
else
    echo "✗ 作成に失敗しました。"
fi

echo ""
read "?Enter で閉じます..."
