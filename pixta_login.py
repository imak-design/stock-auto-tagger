#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pixta contributor login & session save.
Run once to create pixta_session.json.
"""

import time
from playwright.sync_api import sync_playwright
from pathlib import Path

SESSION_FILE = Path(__file__).parent / "pixta_session.json"
USER_DATA_DIR = Path(__file__).parent / "pixta_profile"

LOGIN_URL = "https://pixta.jp/sign_in"


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

        print("Opening Pixta login page...")
        print("Please log in to your Pixta account in the browser.")
        print("Waiting for login (up to 3 minutes)...")
        page.goto(LOGIN_URL)

        for _ in range(360):
            url = page.url
            if "pixta.jp" in url and "sign_in" not in url and "login" not in url:
                break
            time.sleep(0.5)
        else:
            print("Timeout: Login did not complete.")
            context.close()
            return

        time.sleep(2)

        context.storage_state(path=str(SESSION_FILE))
        print(f"Session saved: {SESSION_FILE}")

        context.close()


if __name__ == "__main__":
    save_session()
