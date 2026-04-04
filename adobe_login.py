#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Adobe Stock コントリビューターポータル ログイン & セッション保存
初回のみ実行してください。
"""

from playwright.sync_api import sync_playwright
from pathlib import Path

SESSION_FILE = Path(__file__).parent / "adobe_session.json"
USER_DATA_DIR = Path(__file__).parent / "chrome_profile"

def save_session():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        page = context.new_page()

        print("Adobe Stockコントリビューターポータルを開きます...")
        print("ブラウザでAdobeアカウントにログインしてください。")
        page.goto("https://contributor.stock.adobe.com/")

        input("\nログインが完了したら、ここでEnterキーを押してください...")

        # セッション保存
        context.storage_state(path=str(SESSION_FILE))
        print(f"セッションを保存しました: {SESSION_FILE}")

        # ブラウザを閉じる
        context.close()

if __name__ == "__main__":
    save_session()
