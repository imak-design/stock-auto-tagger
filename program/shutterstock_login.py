#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shutterstock コントリビューターポータル ログイン & セッション保存
初回のみ実行してください。
"""

from playwright.sync_api import sync_playwright
from paths import SHUTTERSTOCK_SESSION as SESSION_FILE, SHUTTERSTOCK_PROFILE as USER_DATA_DIR

PORTAL_URL = "https://submit.shutterstock.com/"

def save_session():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = context.new_page()

        print("Shutterstockコントリビューターポータルを開きます...")
        print("ブラウザでShutterstockアカウントにログインしてください。")
        page.goto(PORTAL_URL, wait_until="commit")

        cb = globals().get("_confirm_callback")
        if cb:
            cb()
        else:
            input("\nログインが完了したら、ここでEnterキーを押してください...")

        try:
            context.storage_state(path=str(SESSION_FILE))
            print(f"セッションを保存しました: {SESSION_FILE}")
        except Exception as e:
            raise RuntimeError(
                "ブラウザが閉じられたため、セッションを保存できませんでした。"
                "ブラウザを閉じずに、ログイン完了後にダイアログのOKを押してください。"
            ) from e
        finally:
            try:
                context.close()
            except Exception:
                pass

if __name__ == "__main__":
    save_session()
