#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Adobe Stock コントリビューターポータル ログイン & セッション保存
初回のみ実行してください。
"""

import time
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
        # webdriver フラグを無効化
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        page = context.new_page()

        print("Adobe Stockコントリビューターポータルを開きます...")
        print("ブラウザでAdobeアカウントにログインしてください。")
        print("ログイン完了を自動検出します（最大3分待機）。")
        page.goto("https://contributor.stock.adobe.com/")

        # ログイン完了まで待機（ログインページから離脱するまでポーリング）
        for _ in range(360):
            url = page.url
            if "contributor.stock.adobe.com" in url and "adobelogin" not in url:
                break
            time.sleep(0.5)
        else:
            print("タイムアウト: ログインが完了しませんでした。")
            context.close()
            return

        time.sleep(2)  # ページ安定待ち

        context.storage_state(path=str(SESSION_FILE))
        print(f"セッションを保存しました: {SESSION_FILE}")

        context.close()

if __name__ == "__main__":
    save_session()
