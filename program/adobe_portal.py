#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Adobe Stock コントリビューターポータル 自動化
- adobe_session.json のセッションを使用（ログイン不要）
- ポータルの「CSV アップロード」機能でメタデータを一括適用
- レビュー提出（審査に登録）
"""

import csv
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from paths import ADOBE_SESSION as SESSION_FILE, ADOBE_PROFILE
UPLOADS_URL = "https://contributor.stock.adobe.com/jp/uploads?upload=1"

def _convert_csv_categories(src_path: Path) -> Path:
    """
    ポータルの CSV アップロードは 1-21 のシンプルなカテゴリコードを受け付ける。
    adobe_stock_*.csv はすでに 1-21 形式なので変換不要。
    """
    return src_path


def run_portal_automation(csv_path: Path, progress_callback=None, headless: bool = False,
                          expected_count: int = 0, files: list = None,
                          confirm_submit_callback=None, is_ai: bool = False,
                          content_type: str = "illustration",
                          file_settings: dict = None, no_wait: bool = False,
                          playwright_instance=None):
    """
    ポータルにファイルをアップロードし、CSV でメタデータを一括適用して審査に登録する。

    Args:
        csv_path: adobe_stock_*.csv のパス
        progress_callback: ログ出力用コールバック (str) -> None
        headless: ヘッドレス実行するか（デバッグ時は False）
        expected_count: SFTPでアップロードしたファイル数（ポータル反映まで待機する、filesと同時使用不可）
        files: アップロードするファイルのリスト（Pathオブジェクト）。指定時はブラウザで直接アップロード。
        is_ai: AI生成コンテンツの場合True（生成AIチェックボックスをONにする）※後方互換
        file_settings: ファイル名→{"content_type": "photo"|"illustration", "is_ai": bool} のdict。
                       指定時はファイルごとに個別設定。未指定時はis_ai/content_typeで一律設定。
    Returns:
        {"submitted": int, "errors": list}
    """

    def log(msg: str):
        if progress_callback:
            progress_callback(msg)
        else:
            print(msg)

    if not SESSION_FILE.exists():
        raise FileNotFoundError(
            f"セッションファイルが見つかりません: {SESSION_FILE}\n"
            "先に adobe_login.py を実行してください。"
        )

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV が見つかりません: {csv_path}")

    errors = []
    submitted = 0

    # カテゴリコードをポータル形式に変換
    upload_csv = _convert_csv_categories(csv_path)
    if upload_csv != csv_path:
        log(f"カテゴリコードを変換しました → {upload_csv}")

    _own_playwright = playwright_instance is None
    p = sync_playwright().start() if _own_playwright else playwright_instance
    try:
        browser = p.chromium.launch(
            headless=headless,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            storage_state=str(SESSION_FILE),
            viewport={"width": 1440, "height": 900},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()
        _keep_open = False

        try:
            # --- ポータルを開く ------------------------------------
            log("ポータルを開いています...")
            page.goto(UPLOADS_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)

            # セッション切れチェック → 自動再ログイン
            if "adobelogin" in page.url or "account.adobe.com" in page.url:
                log("セッションが切れています。ブラウザでログインしてください...")
                context.close()
                browser.close()

                relogin_ctx = p.chromium.launch_persistent_context(
                    user_data_dir=str(ADOBE_PROFILE),
                    headless=False,
                    channel="chrome",
                    args=["--disable-blink-features=AutomationControlled"],
                    ignore_default_args=["--enable-automation"],
                )
                relogin_ctx.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                relogin_page = relogin_ctx.pages[0] if relogin_ctx.pages else relogin_ctx.new_page()
                relogin_page.goto("https://contributor.stock.adobe.com/")

                log("ログイン完了を待っています（最大3分）...")
                for _ in range(360):
                    if "contributor.stock.adobe.com" in relogin_page.url and "adobelogin" not in relogin_page.url:
                        break
                    time.sleep(0.5)
                else:
                    relogin_ctx.close()
                    raise TimeoutError("Adobeログインがタイムアウトしました。")

                time.sleep(2)
                relogin_ctx.storage_state(path=str(SESSION_FILE))
                log("セッションを保存しました。処理を続行します...")
                relogin_ctx.close()

                # 新しいセッションで再接続
                browser = p.chromium.launch(
                    headless=headless,
                    channel="chrome",
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context(
                    storage_state=str(SESSION_FILE),
                    viewport={"width": 1440, "height": 900},
                )
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                page = context.new_page()
                page.goto(UPLOADS_URL, wait_until="domcontentloaded", timeout=30000)
                time.sleep(4)

            log(f"ポータル接続OK: {page.url}")

            # --- ブラウザ直接アップロード（filesが指定された場合）----------
            if files:
                # アップロード前の既存ファイル数を記録（新規ファイルとの区別に使用）
                try:
                    pre_data = page.evaluate("() => window.__react_context__")
                    initial_count = len(pre_data.get("reduxState", {}).get("content", []))
                except Exception:
                    initial_count = 0
                log(f"アップロード前の既存ファイル数: {initial_count}件")

                uploaded_count = _upload_files(page, files, log)
                expected_count = len(files)  # 必ずアップロード対象数を使用

            # ポータルへのファイル反映を待機（新規分が増えるまで最大30分）
            # ★リロード禁止: アップロード中のXHRが切断されるため
            if expected_count > 0:
                target_count = initial_count + expected_count
                log(f"ポータルへのファイル反映を待っています（目標: {target_count}件、最大30分）...")
                for i in range(180):
                    time.sleep(10)
                    try:
                        data = page.evaluate("() => window.__react_context__")
                        current = len(data.get("reduxState", {}).get("content", []))
                        if current >= target_count:
                            log(f"ファイル反映確認: {current}件（新規 {current - initial_count}件）")
                            break
                        if i % 6 == 0:
                            log(f"  ...{(i+1)*10}秒経過, 現在{current}件/{target_count}件目標。ページ更新中...")
                            page.reload(wait_until="domcontentloaded", timeout=30000)
                            time.sleep(4)
                    except Exception:
                        pass
                else:
                    log("[!] タイムアウト: 処理を続行します。")

            # アップロード件数を確認・比較
            data = page.evaluate("() => window.__react_context__")
            content = data.get("reduxState", {}).get("content", [])
            actual_new = len(content) - initial_count if files else len(content)
            log(f"アップロード済みファイル: {len(content)}件（新規 {actual_new}件）")
            for item in content:
                log(f"  {item.get('originalName', '?')} (status={item.get('status', '?')})")

            if files and actual_new < len(files):
                missing = len(files) - actual_new
                log(f"[!] 警告: {missing}件が未反映。再待機します...")
                for i in range(30):
                    time.sleep(10)
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                        time.sleep(4)
                        data = page.evaluate("() => window.__react_context__")
                        content = data.get("reduxState", {}).get("content", [])
                        actual_new = len(content) - initial_count
                        if actual_new >= len(files):
                            log(f"反映確認: {actual_new}件（全件反映）")
                            break
                        log(f"  ...再待機 {(i+1)*10}秒, 現在{actual_new}/{len(files)}件")
                    except Exception:
                        pass
                else:
                    log(f"[!] 再待機タイムアウト: {actual_new}/{len(files)}件で続行します")

            # --- Step1: CSV アップロードでメタデータを一括適用 --------
            log("\n>> CSV アップロード開始...")
            submitted = _upload_metadata_csv(page, upload_csv, log)

            # --- Step2: コンテンツタイプ + AI設定 ------
            if file_settings:
                # ファイルごとに個別設定（統合アップロード時）
                log("\n>> ファイルごとにコンテンツタイプ + AI設定を適用...")
                _set_content_type_per_file(page, log, file_settings)
            elif is_ai:
                log("\n>> 画像ファイルのコンテンツタイプ(イラスト) + AI生成チェックを設定...")
                _set_content_type(page, log, value="2", label="イラスト", set_ai=True)
            elif content_type == "photo":
                log("\n>> 画像ファイルのコンテンツタイプを写真に設定...")
                _set_content_type(page, log, value="1", label="写真")
            else:
                log("\n>> 画像ファイルのコンテンツタイプをイラストに設定...")
                _set_content_type(page, log, value="2", label="イラスト")

            # --- Step3: 審査に登録 ------------------------------------
            if submitted >= 0:
                should_submit = True
                if confirm_submit_callback:
                    should_submit = confirm_submit_callback()
                if should_submit:
                    _submit_for_review(page, log)
                else:
                    # テストモード: 全選択だけして審査登録ボタン手前で停止
                    _keep_open = True
                    log("\n>> [テストモード] 全ファイルを選択して停止します...")
                    select_all = page.locator('input[type=checkbox]').first
                    try:
                        if select_all.is_visible(timeout=3000) and not select_all.is_checked():
                            select_all.click()
                            time.sleep(1)
                            log("全ファイルを選択しました。審査登録ボタンを手動で押してください。")
                    except PWTimeout:
                        log("[!] 全選択チェックボックスが見つかりません")

        except Exception as e:
            log(f"NG エラー: {e}")
            errors.append(str(e))
            raise
        finally:
            # 一時ファイルを削除
            if upload_csv != csv_path and upload_csv.exists():
                upload_csv.unlink()
            if not _keep_open:
                context.close()
                browser.close()
                if _own_playwright:
                    p.stop()
            elif no_wait:
                log("ブラウザを開いたままにします。（次の工程に進みます）")
            else:
                log("ブラウザを開いたままにします。ブラウザを閉じると次の処理に進みます。")
                try:
                    # Playwrightのイベントループで待機（ブラウザ切断時は例外で抜ける）
                    page.wait_for_event("close", timeout=7200000)
                except Exception:
                    pass
                log("ブラウザが閉じられました。")
                try:
                    if browser.is_connected():
                        context.close()
                        browser.close()
                except Exception:
                    pass
                if _own_playwright:
                    p.stop()

    except Exception:
        if _own_playwright:
            p.stop()
        raise

    return {"submitted": submitted, "errors": errors}


def _upload_files(page, files: list, log) -> int:
    """
    ?upload=1 URL で自動オープンするアップロードダイアログにファイルをセットする。
    クリック → ファイルチューザー横取り方式でReactのイベントを正しく発火させる。
    戻り値: アップロードしたファイル数
    """
    log(f"ブラウザアップロード開始: {len(files)}件")
    for f in files:
        log(f"  {f.name}")

    # ?upload=1 でダイアログが開くまで少し待つ
    time.sleep(3)

    triggered = False

    # クリック → ファイルチューザー横取り（Reactイベントが正しく発火する）
    # DOM確認済み: .dropzone > .uploader__drop > [data-t="drop-screen-browse"] > button[type=submit] "参照"
    upload_area_selectors = [
        '.uploader__drop button[type="submit"]',   # 「参照」ボタン（確認済み）
        '.upload-bubble button[type="submit"]',
        '[data-t="drop-screen-browse"] button',
        'button:has-text("参照")',
        '.dropzone button[type="submit"]',
        '[data-t="upload-dropzone"]',
        '[class*="dropzone"]',
        'button:has-text("ファイルを選択")',
        'button:has-text("Browse")',
        '[data-t="upload-button"]',
    ]

    for sel in upload_area_selectors:
        try:
            el = page.locator(sel).first
            if not el.is_visible(timeout=3000):
                continue
            log(f"アップロード領域を発見: {sel}")
            with page.expect_file_chooser(timeout=8000) as fc_info:
                el.click()
            fc_info.value.set_files([str(f) for f in files])
            triggered = True
            log("ファイルチューザー経由でセット完了")
            break
        except Exception:
            continue

    if not triggered:
        # フォールバック: ページ全体にファイルチューザーイベントを待ちながらクリック可能な要素を探す
        try:
            log("フォールバック: ページクリックでファイルチューザーを試みます...")
            with page.expect_file_chooser(timeout=5000) as fc_info:
                page.keyboard.press("Space")
            fc_info.value.set_files([str(f) for f in files])
            triggered = True
            log("Spaceキートリガーでセット完了")
        except Exception:
            pass

    if not triggered:
        raise RuntimeError(
            "アップロード領域が見つかりません。"
            "ポータルのUIが変更されている可能性があります。"
        )

    # アップロード完了までの最低待機時間をファイルサイズから推定
    VIDEO_EXTS = {".mp4", ".mov", ".avi"}
    has_video = any(f.suffix.lower() in VIDEO_EXTS for f in files)
    total_mb = sum(f.stat().st_size for f in files) / (1024 * 1024)
    base_wait = 180 if has_video else 60  # 動画あり: 最低3分、画像のみ: 最低1分
    min_wait = max(base_wait, int(total_mb * 0.3))  # 約3MB/s想定
    log(f"ファイルをセット完了。アップロード開始 (総{total_mb:.0f}MB、最低{min_wait}秒待機後にリロード)")

    # アップロード完了まで待機（リロード禁止）
    for elapsed in range(0, min_wait, 30):
        time.sleep(30)
        remaining = min_wait - elapsed - 30
        if remaining > 0:
            log(f"  アップロード待機中... 残り約{remaining}秒")

    # オーバーレイを閉じてリロード（この時点でXHRは完了しているはず）
    log("最低待機完了。オーバーレイを閉じてページをリロードします...")
    try:
        close_btn = page.locator('.uploader__close').first
        if close_btn.is_visible(timeout=3000):
            close_btn.click()
            time.sleep(2)
    except Exception:
        pass

    page.reload(wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)

    expected = len(files)
    return expected


def _select_all_and_set(page, log, content_value="2", content_label="イラスト", set_ai=False):
    """
    全ファイルを選択してコンテンツタイプとAI生成チェックを一括設定する。
    AI素材用: 全選択 → イラスト設定 → AIチェック → 保存
    """
    # 全選択チェックボックスをクリック
    try:
        page.locator('input[type=checkbox]').first.wait_for(state="visible", timeout=10000)
        checkboxes = page.locator('input[type=checkbox]').all()
    except PWTimeout:
        log("[!] チェックボックスが見つかりません。スキップします。")
        return

    select_all = checkboxes[0]
    if not select_all.is_checked():
        select_all.click()
        time.sleep(1.5)
    log("全ファイルを選択しました")

    # コンテンツタイプ設定
    ct_sel = page.locator('select[name="contentType"]').first
    try:
        current = ct_sel.input_value()
        if current == content_value:
            log(f"コンテンツタイプは既に{content_label}です")
        else:
            ct_sel.select_option(value=content_value)
            time.sleep(1)
            log(f"コンテンツタイプ → {content_label} に変更")
    except PWTimeout:
        log("[!] contentType セレクトが見つかりません")

    # AI生成チェックボックス
    if set_ai:
        try:
            ai_checkbox = page.locator('#content-tagger-generative-ai-checkbox')
            ai_checkbox.wait_for(state="visible", timeout=10000)
            if not ai_checkbox.is_checked():
                ai_checkbox.click()
                time.sleep(1)
            log("[OK] AI生成チェックボックスをONにしました")
        except Exception as e:
            log(f"[!] AI生成チェックボックスの処理に失敗: {e}")
        # 架空の人物およびプロパティ
        try:
            prop_cb = page.locator('[name="content-tagger-generative-ai-property-release-checkbox"]')
            if prop_cb.is_visible(timeout=3000) and not prop_cb.is_checked():
                prop_cb.click()
                time.sleep(1)
            log("[OK] 架空プロパティチェックをONにしました")
        except Exception as e:
            log(f"[!] 架空プロパティチェックの処理に失敗: {e}")

    # 保存
    save_btn = page.locator('[data-t="save-work"]').first
    try:
        if save_btn.is_visible(timeout=3000):
            disabled = save_btn.get_attribute("disabled")
            if disabled is None:
                save_btn.click()
                time.sleep(3)
                log("[OK] 保存完了")
            else:
                log("保存ボタンは無効（変更なし）")
        else:
            log("[!] 保存ボタンが見つかりません")
    except PWTimeout:
        log("[!] 保存ボタンが見つかりません")


def _set_content_type(page, log, value="2", label="イラスト", set_ai=False):
    """
    画像ファイルのコンテンツタイプを設定する。
    サムネイルをクリック→サイドパネルのファイル形式ドロップダウンで変更→保存。
    set_ai=True の場合、AI生成チェックボックスもONにする。
    - EPS（ベクター）: 自動設定されるためスキップ
    - 動画: スキップ
    """
    VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"}
    VECTOR_EXTS = {".eps", ".ai"}

    # ドロップダウンのラベルマップ (value → 表示テキスト)
    LABEL_MAP = {"1": "写真", "2": "イラスト", "3": "ベクター", "4": "ビデオ"}
    target_label = LABEL_MAP.get(value, label)

    # __react_context__ からファイル一覧を取得
    try:
        data = page.evaluate("() => window.__react_context__")
        content = data.get("reduxState", {}).get("content", [])
    except Exception:
        content = []

    if not content:
        log("[!] ファイル一覧を取得できません。スキップします。")
        return

    image_indices = []
    vector_indices = []
    for i, item in enumerate(content):
        ext = Path(item.get("originalName", "")).suffix.lower()
        if ext in VIDEO_EXTS:
            continue
        elif ext in VECTOR_EXTS:
            vector_indices.append(i)
        else:
            image_indices.append(i)
    video_count = len(content) - len(image_indices) - len(vector_indices)
    log(f"ファイル内訳: 画像 {len(image_indices)}件 / ベクター {len(vector_indices)}件 / 動画 {video_count}件")
    if vector_indices:
        log(f"ベクターファイルはコンテンツタイプ自動設定のためスキップします")
    if not image_indices:
        log(f"[!] {label}設定対象の画像ファイルがありません。スキップします。")
        if vector_indices:
            _set_icon_checkbox(page, content, vector_indices, log)
        return

    # サムネイル一覧を取得
    thumbs = page.locator('img.upload-tile__thumbnail').all()
    if not thumbs:
        log("[!] サムネイルが見つかりません。スキップします。")
        return

    changed = 0
    for idx in image_indices:
        name = content[idx].get("originalName", f"#{idx+1}")
        if idx >= len(thumbs):
            log(f"  [!] {name}: サムネイルが見つかりません（index={idx}）")
            continue
        try:
            # サムネイルをクリックして選択→サイドパネル表示
            thumbs[idx].click()
            time.sleep(1)

            # サイドパネル内のファイル形式ドロップダウンボタンを取得
            file_type_wrapper = page.locator('[data-t="content-tagger-file-type-wrapper"]')
            dropdown_btn = file_type_wrapper.locator('button[aria-haspopup="listbox"]').first
            dropdown_btn.wait_for(state="visible", timeout=5000)

            # 現在の値を確認
            current_label = dropdown_btn.locator('span').first.inner_text()
            need_type_change = current_label != target_label

            # ドロップダウンが無効なら（ベクター/動画で自動設定）スキップ
            if need_type_change and dropdown_btn.get_attribute("disabled") is not None:
                log(f"  [!] {name}: ファイル形式ドロップダウンが無効です")
                continue

            # ファイル形式変更
            if need_type_change:
                dropdown_btn.click()
                time.sleep(0.5)
                menu = page.locator('[role="listbox"][class*="spectrum-Menu"]')
                menu.wait_for(state="visible", timeout=3000)
                option = menu.locator(f'[role="option"]:has-text("{target_label}")')
                option.click()
                time.sleep(1)

            # AI生成チェックボックス + 架空プロパティ
            need_ai = False
            if set_ai:
                ai_cb = page.locator('#content-tagger-generative-ai-checkbox')
                try:
                    if ai_cb.is_visible(timeout=3000) and not ai_cb.is_checked():
                        ai_cb.click()
                        time.sleep(0.5)
                        need_ai = True
                except Exception as e:
                    log(f"  [!] {name}: AIチェック失敗: {e}")
                prop_cb = page.locator('[name="content-tagger-generative-ai-property-release-checkbox"]')
                try:
                    if prop_cb.is_visible(timeout=3000) and not prop_cb.is_checked():
                        prop_cb.click()
                        time.sleep(0.5)
                        need_ai = True
                except Exception as e:
                    log(f"  [!] {name}: 架空プロパティチェック失敗: {e}")

            if not need_type_change and not need_ai:
                continue  # 変更なし

            # 保存
            save_btn = page.locator('[data-t="save-work"]').first
            if save_btn.is_visible(timeout=3000):
                disabled = save_btn.get_attribute("disabled")
                if disabled is None:
                    save_btn.click()
                    time.sleep(1.5)
                    changed += 1
                    parts = []
                    if need_type_change:
                        parts.append(target_label)
                    if need_ai:
                        parts.append("AI生成")
                    log(f"  {name}: → {' + '.join(parts)}")
                else:
                    log(f"  [!] {name}: 保存ボタンが無効です")
        except Exception as e:
            log(f"  [!] {name}: コンテンツタイプ設定失敗: {e}")

    if changed > 0:
        log(f"[OK] コンテンツタイプ → {label}: {changed}/{len(image_indices)}件 変更")
    else:
        log(f"コンテンツタイプは全て{label}済み（{len(image_indices)}件）")

    # ベクターファイルのアイコンチェック
    if content and vector_indices:
        _set_icon_checkbox(page, content, vector_indices, log)


def _set_content_type_per_file(page, log, file_settings: dict):
    """
    ファイルごとに個別のコンテンツタイプとAI生成チェックを設定する。
    file_settings: {ファイル名: {"content_type": "photo"|"illustration", "is_ai": bool}}
    """
    VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"}
    VECTOR_EXTS = {".eps", ".ai"}
    CONTENT_TYPE_MAP = {"photo": ("1", "写真"), "illustration": ("2", "イラスト")}

    try:
        data = page.evaluate("() => window.__react_context__")
        content = data.get("reduxState", {}).get("content", [])
    except Exception:
        content = []

    if not content:
        log("[!] ファイル一覧を取得できません。スキップします。")
        return

    thumbs = page.locator('img.upload-tile__thumbnail').all()
    if not thumbs:
        log("[!] サムネイルが見つかりません。スキップします。")
        return

    image_indices = []
    video_indices = []
    vector_indices = []
    for i, item in enumerate(content):
        ext = Path(item.get("originalName", "")).suffix.lower()
        if ext in VIDEO_EXTS:
            video_indices.append(i)
        elif ext in VECTOR_EXTS:
            vector_indices.append(i)
        else:
            image_indices.append(i)

    log(f"ファイル内訳: 画像 {len(image_indices)}件 / ベクター {len(vector_indices)}件 / 動画 {len(video_indices)}件")
    if vector_indices:
        log(f"ベクターファイルはコンテンツタイプ自動設定のためスキップします")

    # 画像 + 動画（動画はコンテンツタイプ変更不要だがAIチェックは必要）
    target_indices = image_indices + video_indices
    if not target_indices:
        log("[!] 設定対象のファイルがありません。スキップします。")
        if vector_indices:
            _set_icon_checkbox(page, content, vector_indices, log)
        return

    changed = 0
    for idx in target_indices:
        name = content[idx].get("originalName", f"#{idx+1}")
        if idx >= len(thumbs):
            log(f"  [!] {name}: サムネイルが見つかりません（index={idx}）")
            continue

        # file_settingsからこのファイルの設定を取得
        settings = file_settings.get(name, {})
        ct = settings.get("content_type", "illustration")
        is_ai = settings.get("is_ai", False)
        is_video = Path(name).suffix.lower() in VIDEO_EXTS

        # AIチェック不要かつ動画なら何もしない
        if is_video and not is_ai:
            continue

        value, target_label = CONTENT_TYPE_MAP.get(ct, ("2", "イラスト"))

        try:
            thumbs[idx].click()
            time.sleep(1)

            # 動画以外: コンテンツタイプ変更
            need_type_change = False
            if not is_video:
                file_type_wrapper = page.locator('[data-t="content-tagger-file-type-wrapper"]')
                dropdown_btn = file_type_wrapper.locator('button[aria-haspopup="listbox"]').first
                dropdown_btn.wait_for(state="visible", timeout=5000)

                current_label = dropdown_btn.locator('span').first.inner_text()
                need_type_change = current_label != target_label

                if need_type_change and dropdown_btn.get_attribute("disabled") is not None:
                    log(f"  [!] {name}: ファイル形式ドロップダウンが無効です")
                    continue

                if need_type_change:
                    dropdown_btn.click()
                    time.sleep(0.5)
                    menu = page.locator('[role="listbox"][class*="spectrum-Menu"]')
                    menu.wait_for(state="visible", timeout=3000)
                    option = menu.locator(f'[role="option"]:has-text("{target_label}")')
                    option.click()
                    time.sleep(1)

            # AIチェック + 架空の人物およびプロパティ
            need_ai = False
            if is_ai:
                ai_cb = page.locator('#content-tagger-generative-ai-checkbox')
                try:
                    if ai_cb.is_visible(timeout=3000) and not ai_cb.is_checked():
                        ai_cb.click()
                        time.sleep(0.5)
                        need_ai = True
                except Exception as e:
                    log(f"  [!] {name}: AIチェック失敗: {e}")
                # 架空の人物およびプロパティ
                prop_cb = page.locator('[name="content-tagger-generative-ai-property-release-checkbox"]')
                try:
                    if prop_cb.is_visible(timeout=3000) and not prop_cb.is_checked():
                        prop_cb.click()
                        time.sleep(0.5)
                        need_ai = True
                except Exception as e:
                    log(f"  [!] {name}: 架空プロパティチェック失敗: {e}")

            if not need_type_change and not need_ai:
                continue

            save_btn = page.locator('[data-t="save-work"]').first
            if save_btn.is_visible(timeout=3000):
                disabled = save_btn.get_attribute("disabled")
                if disabled is None:
                    save_btn.click()
                    time.sleep(1.5)
                    changed += 1
                    parts = []
                    if need_type_change:
                        parts.append(target_label)
                    if need_ai:
                        parts.append("AI生成")
                    log(f"  {name}: → {' + '.join(parts)}")
                else:
                    log(f"  [!] {name}: 保存ボタンが無効です")
        except Exception as e:
            log(f"  [!] {name}: 設定失敗: {e}")

    log(f"[OK] コンテンツタイプ個別設定完了: {changed}/{len(target_indices)}件 変更")

    if content and vector_indices:
        _set_icon_checkbox(page, content, vector_indices, log)


def _set_icon_checkbox(page, content, vector_indices, log):
    """
    ベクターファイルのうち、ファイル名に 'icon' が含まれるものに
    「これはアイコンです」チェックボックスを設定する。
    各ファイルを個別に選択→チェック→保存する。
    """
    icon_indices = [
        i for i in vector_indices
        if "icon" in Path(content[i].get("originalName", "")).stem.lower()
    ]
    if not icon_indices:
        return

    log(f"アイコンファイル検出: {len(icon_indices)}件")

    try:
        checkboxes = page.locator('input[type=checkbox]').all()
    except Exception:
        return

    for idx in icon_indices:
        cb_pos = idx + 1
        if cb_pos >= len(checkboxes):
            continue

        name = content[idx].get("originalName", "?")
        try:
            # ファイルを選択
            if not checkboxes[cb_pos].is_checked():
                checkboxes[cb_pos].click()
                time.sleep(0.5)

            # 「これはアイコンです」チェックボックスを探してチェック
            icon_cb = page.locator('[data-t="content-tagger-is-icon-checkbox"]').first
            if icon_cb.is_visible(timeout=3000) and not icon_cb.is_checked():
                icon_cb.click()
                time.sleep(0.5)
                log(f"  アイコンチェック: {name}")

                # 保存
                save_btn = page.locator('[data-t="save-work"]').first
                if save_btn.is_visible(timeout=3000):
                    disabled = save_btn.get_attribute("disabled")
                    if disabled is None:
                        save_btn.click()
                        time.sleep(2)

            # 選択解除
            if checkboxes[cb_pos].is_checked():
                checkboxes[cb_pos].click()
                time.sleep(0.3)
        except Exception as e:
            log(f"  [!] アイコン設定失敗 ({name}): {e}")


def _upload_metadata_csv(page, csv_path: Path, log) -> int:
    """
    「CSV アップロード」ボタンを押してファイルをセットし、アップロードを実行する。
    戻り値: アップロードされたファイル件数（失敗時は -1）
    """
    # ① CSV アップロードボタンをクリック
    csv_btn = page.locator('[data-t="edit-menu-upload-csv"]').first
    if not csv_btn.is_visible(timeout=5000):
        raise RuntimeError("CSV アップロードボタンが見つかりません")
    csv_btn.click()
    time.sleep(1.5)
    log("CSV アップロードダイアログを開きました")

    # ② ファイル input に CSV をセット
    file_input = page.locator('input[accept="text/csv"]').first
    if not file_input.count():
        raise RuntimeError("CSV ファイル入力欄が見つかりません")
    file_input.set_input_files(str(csv_path))
    time.sleep(2)
    log(f"CSV をセット: {csv_path.name}")

    # ③ ダイアログが残っている場合は「アップロード」ボタンをクリック
    #    （set_input_files で自動処理される場合もあるため、任意）
    try:
        upload_btn = page.locator('[data-t="csv-modal-upload"]').first
        if upload_btn.is_visible(timeout=2000):
            upload_btn.click()
            time.sleep(2)
            log("CSV アップロード実行ボタンをクリック")
    except PWTimeout:
        log("ダイアログが自動的に処理されました")

    # ④ ダイアログが閉じるまで待つ
    for _ in range(15):
        time.sleep(1)
        close_btn = page.locator('[data-t="csv-modal-close"]')
        if not close_btn.is_visible():
            break  # ダイアログが閉じた
    else:
        # エラーテキストを確認してから閉じる
        dialog_text = ""
        dialogs = page.locator('[role=dialog]')
        if dialogs.count() > 0:
            dialog_text = dialogs.first.inner_text()[:200]
        log(f"[!] ダイアログがタイムアウト: {dialog_text}")
        page.locator('[data-t="csv-modal-close"]').click()
        return -1

    log("[OK] CSV アップロード完了")

    # 「更新して変更を表示」ボタンをクリック（通知バナーにある）
    time.sleep(1)
    try:
        refresh_btn = page.locator(
            '[data-t="csv-upload-success-notice-container"] a, '
            '[data-t="csv-upload-success-notice-container"] button, '
            '.alert--success a, '
            '.alert--success button'
        ).first
        if refresh_btn.is_visible(timeout=3000):
            refresh_btn.click()
            log("ページを更新して変更を反映...")
            time.sleep(4)
        else:
            # ボタンが見つからない場合はページリロード
            page.reload(wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)
    except PWTimeout:
        page.reload(wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

    # 更新されたファイル件数を取得 & メタデータ反映確認
    data = page.evaluate("() => window.__react_context__")
    content = data.get("reduxState", {}).get("content", [])

    # メタデータ（タイトル・キーワード）が反映されるまで待機
    for attempt in range(6):
        all_ok = True
        for item in content:
            title = (item.get("title") or "").strip()
            keywords = item.get("keywords") or []
            if not title or not keywords:
                all_ok = False
                break
        if all_ok:
            log("[OK] メタデータ反映確認: 全ファイルにタイトル・キーワード設定済み")
            break
        if attempt < 5:
            log(f"  メタデータ反映待機中... ({(attempt+1)*5}秒)")
            time.sleep(5)
            page.reload(wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)
            data = page.evaluate("() => window.__react_context__")
            content = data.get("reduxState", {}).get("content", [])
    else:
        # 反映されなかったファイルを警告
        for item in content:
            title = (item.get("title") or "").strip()
            keywords = item.get("keywords") or []
            name = item.get("originalName", "?")
            if not title:
                log(f"  [!] タイトル未設定: {name}")
            if not keywords:
                log(f"  [!] キーワード未設定: {name}")

    return len(content)


def _submit_for_review(page, log):
    """
    全ファイルを選択して審査に登録する。
    ダイアログのフロー:
      1. 全選択 -> submit-moderation-button クリック
      2. ガイドライン同意チェックボックス x2 -> continue-moderation-button
      3. サムネイル確認ダイアログ -> send-moderation-button
      4. (任意) 5ワード入力チャレンジ -> 入力 -> 送信
    """
    log("\n>> 審査に登録...")

    # --- Step A: 全選択 ---
    select_all = page.locator('input[type=checkbox]').first
    try:
        if select_all.is_visible(timeout=3000) and not select_all.is_checked():
            select_all.click()
            time.sleep(1)
            log("全ファイルを選択")
    except PWTimeout:
        log("[!] 全選択チェックボックスが見つかりません")

    # --- Step B: 審査登録ボタン ---
    submit_btn = page.locator('[data-t="submit-moderation-button"]').first
    if not submit_btn.is_visible(timeout=5000):
        log("[!] 審査登録ボタンが見つかりません")
        return
    log(f"審査登録ボタン: {submit_btn.inner_text().strip()}")
    submit_btn.click()
    time.sleep(2)

    # --- Step C: ガイドライン同意チェックボックス (label クリック) ---
    try:
        for cb_id, label_text in [
            ("tc-reviewed-guidelines", "ガイドライン精読確認"),
            ("tc-understand-guidelines", "違反時アカウント停止の理解"),
        ]:
            el = page.locator(f'label[for="{cb_id}"]').first
            if not el.count():
                el = page.locator(f'#{cb_id}').first
            if el.is_visible(timeout=3000) and not page.locator(f'#{cb_id}').first.is_checked():
                el.click()
                time.sleep(0.6)
                log(f"同意チェック: {label_text}")
    except PWTimeout:
        log("[!] 同意チェックボックスが見つかりません")

    # --- Step D: 続行ボタン ---
    try:
        page.wait_for_selector(
            '[data-t="continue-moderation-button"]:not([disabled])',
            timeout=6000
        )
        page.locator('[data-t="continue-moderation-button"]').first.click()
        time.sleep(2.5)
        log("続行ボタンクリック")
    except PWTimeout:
        log("[!] 続行ボタンが有効になりませんでした")
        return

    # --- Step E: サムネイル確認ダイアログ -> 全選択確認 -> 審査へ ---
    try:
        send_btn = page.locator('[data-t="send-moderation-button"]').first
        if send_btn.is_visible(timeout=5000):
            # ダイアログ内の全選択チェックボックス（最初のものが「全て選択」）
            dlg_select_all = page.locator('.modal__body input[type=checkbox]').first
            if dlg_select_all.count() and not dlg_select_all.is_checked():
                dlg_select_all.click()
                time.sleep(0.8)
                log("サムネイルダイアログ: 全て選択")

            # send-moderation-button をクリック（force=True で inactive クラスを無視）
            send_btn.click(force=True)
            time.sleep(3)
            log("審査へボタンクリック")
    except PWTimeout:
        log("[!] サムネイル確認ダイアログが見つかりません")
        return

    # --- Step F: 5ワード入力チャレンジ（たまに出現）---
    _handle_five_word_challenge(page, log)

    log("[OK] 審査提出完了")


def _handle_five_word_challenge(page, log):
    """
    Adobe Stock のキャプチャ的な「画像を表す単語を5つ入力」チャレンジを処理する。
    出現しない場合は何もしない。
    """
    # チャレンジ画面の検出: テキスト入力欄が5つ並ぶ or 特定 data-t
    challenge_selectors = [
        '[data-t="image-challenge-input"]',
        'input[placeholder*="word" i]',
        'input[placeholder*="単語"]',
        '[class*="challenge"] input',
        '[class*="microtask"] input',
    ]
    challenge_input = None
    for sel in challenge_selectors:
        el = page.locator(sel).first
        try:
            if el.is_visible(timeout=2000):
                challenge_input = el
                log("5ワードチャレンジ検出")
                break
        except PWTimeout:
            continue

    if challenge_input is None:
        return  # チャレンジなし

    # 入力欄を全て取得して汎用的な単語を入力
    generic_words = ["light", "glow", "abstract", "neon", "color",
                     "bright", "wave", "energy", "motion", "digital"]
    inputs = page.locator(
        '[data-t="image-challenge-input"], '
        'input[placeholder*="word" i], '
        '[class*="challenge"] input'
    ).all()

    for i, inp in enumerate(inputs[:5]):
        try:
            inp.fill(generic_words[i])
            time.sleep(0.3)
        except Exception:
            pass

    # 送信ボタンを探してクリック
    submit_selectors = [
        '[data-t="challenge-submit"]',
        'button:has-text("送信")',
        'button:has-text("Submit")',
        'button[type="submit"]',
    ]
    for sel in submit_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                time.sleep(2)
                log("5ワードチャレンジ送信")
                break
        except PWTimeout:
            continue


# --------------------------------------------------------------
# スタンドアロン実行（デバッグ用）
# --------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        csv_folder = Path("csv_output")
        csvs = sorted(csv_folder.glob("adobe_stock_*.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not csvs:
            print("CSVが見つかりません。パスを引数で指定してください。")
            sys.exit(1)
        csv_path = csvs[0]
    else:
        csv_path = Path(sys.argv[1])

    print(f"使用するCSV: {csv_path}")

    result = run_portal_automation(
        csv_path=csv_path,
        progress_callback=print,
        headless=False,
    )
    print(f"\n完了: 提出={result['submitted']}件 / エラー={len(result['errors'])}件")
    if result["errors"]:
        for e in result["errors"]:
            print(f"  NG {e}")
