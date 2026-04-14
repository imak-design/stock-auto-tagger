#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
機密情報・設定ファイルの保存先を一元管理する。
プロジェクトフォルダを共有しても漏れないよう、すべてユーザーのホームディレクトリ配下に配置する。

- config.json       : ユーザー設定
- sessions/*.json   : Playwright の storage_state（ログイン状態）
- profiles/*/       : Playwright の永続ブラウザプロファイル（Cookie・LocalStorage）
"""

import shutil
from pathlib import Path

_PROJECT_DIR = Path(__file__).parent

ENV_DIR = Path.home() / ".stock-auto-tagger"
SESSIONS_DIR = ENV_DIR / "sessions"
PROFILES_DIR = ENV_DIR / "profiles"

ENV_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

ENV_FILE = ENV_DIR / ".env"
CONFIG_FILE = ENV_DIR / "config.json"

ADOBE_SESSION = SESSIONS_DIR / "adobe_session.json"
SHUTTERSTOCK_SESSION = SESSIONS_DIR / "shutterstock_session.json"
PIXTA_SESSION = SESSIONS_DIR / "pixta_session.json"

ADOBE_PROFILE = PROFILES_DIR / "adobe_profile"
SHUTTERSTOCK_PROFILE = PROFILES_DIR / "shutterstock_profile"
PIXTA_PROFILE = PROFILES_DIR / "pixta_profile"

# 旧パス（プロジェクト内）から新パス（ホームディレクトリ）へ自動マイグレーション。
# 新パスが未作成で旧パスが存在する場合のみ移動する（ログインし直しを回避）。
_MIGRATIONS = [
    (_PROJECT_DIR / "config.json", CONFIG_FILE),
    (_PROJECT_DIR / "adobe_session.json", ADOBE_SESSION),
    (_PROJECT_DIR / "shutterstock_session.json", SHUTTERSTOCK_SESSION),
    (_PROJECT_DIR / "pixta_session.json", PIXTA_SESSION),
    (_PROJECT_DIR / "chrome_profile", ADOBE_PROFILE),
    (_PROJECT_DIR / "shutterstock_profile", SHUTTERSTOCK_PROFILE),
    (_PROJECT_DIR / "pixta_profile", PIXTA_PROFILE),
]

for _old, _new in _MIGRATIONS:
    if _old.exists() and not _new.exists():
        try:
            shutil.move(str(_old), str(_new))
        except Exception:
            pass
