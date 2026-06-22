#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shutterstock コントリビューターポータル 自動化
- shutterstock_session.json のセッションを使用（ログイン不要）
- 画像アップロード → CSV適用 → 提出
- 動画タブ確認 → 提出
"""

import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from paths import SHUTTERSTOCK_SESSION as SESSION_FILE
PHOTO_URL = "https://submit.shutterstock.com/ja/portfolio/not_submitted/photo"
VIDEO_URL = "https://submit.shutterstock.com/ja/portfolio/not_submitted/video"


def _close_popups(page):
    try:
        close_btn = page.locator('[data-testid="announcement-close"]').first
        if close_btn.is_visible(timeout=2000):
            close_btn.click()
            time.sleep(1)
    except PWTimeout:
        pass
    page.keyboard.press("Escape")
    time.sleep(1)


def _select_all(page, log):
    """input[type="checkbox"] を全て取得してクリック（0.2秒間隔）"""
    cbs = page.locator('input[type="checkbox"]').all()
    for cb in cbs:
        try:
            cb.click(force=True)
            time.sleep(0.2)
        except Exception:
            pass
    log(f"全選択: {len(cbs)}件")
    time.sleep(2)
    return len(cbs)


def _ensure_all_selected(page, log):
    """未選択のチェックボックスのみクリック（既選択は維持）"""
    cbs = page.locator('input[type="checkbox"]').all()
    clicked = 0
    for cb in cbs:
        try:
            if not cb.is_checked():
                cb.click(force=True)
                time.sleep(0.2)
                clicked += 1
        except Exception:
            pass
    log(f"全選択確認: {len(cbs)}件中 {clicked}件クリック")
    time.sleep(2)
    return len(cbs)


def run_portal_automation(csv_path: Path, progress_callback=None, headless: bool = False,
                          files: list = None, expected_count: int = 0, skip_submit: bool = False,
                          no_wait: bool = False, playwright_instance=None):
    def log(msg: str):
        if progress_callback:
            progress_callback(msg)
        else:
            print(msg)

    if not SESSION_FILE.exists():
        raise FileNotFoundError(
            f"Session file not found: {SESSION_FILE}\n"
            "Run shutterstock_login.py first."
        )
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    submitted = 0
    errors = []

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
        _keep_open = skip_submit

        try:
            # ============================================================
            # STEP 1: ブラウザ起動・ポータルを開く
            # ============================================================
            log("Opening Shutterstock not-submitted portfolio (photo)...")
            page.goto(PHOTO_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)

            if "login" in page.url:
                raise PermissionError("Session expired. Run shutterstock_login.py again.")
            log(f"Portal OK: {page.url}")
            _close_popups(page)

            # ============================================================
            # STEP 2: ファイルをブラウザアップロード（files引数がある場合のみ）
            # ============================================================
            if files:
                log(f"アップロード開始: {len(files)}件...")
                # アップロードボタンでドロップゾーンのモーダルを開く
                upload_btn = page.locator('button[data-testid="uploadButton"]').first
                try:
                    upload_btn.wait_for(state="visible", timeout=15000)
                except PWTimeout:
                    raise RuntimeError("アップロードボタンが見つかりません")
                upload_btn.click()

                # Shutterstock は native file chooser ではなく dropzone 方式に変更されたため、
                # dropzone 内の input[type=file] へ直接ファイルをセットする
                file_input = page.locator('[data-testid="dropzone-container"] input[type="file"]').first
                try:
                    file_input.wait_for(state="attached", timeout=10000)
                except PWTimeout:
                    file_input = page.locator('input[type="file"]').last  # フォールバック
                file_input.set_input_files([str(f) for f in files])
                log(f"Files set: {[Path(f).name for f in files]}")

                log("アップロード完了待機中（60秒）...")
                time.sleep(60)

                expected = len(files)
                for attempt in range(4):
                    log(f"  アップロード確認中 ({attempt + 1}/4)...")
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                    time.sleep(4)
                    _close_popups(page)
                    try:
                        tab_text = page.locator('[data-testid="tab-not_submitted"]').inner_text(timeout=5000).strip()
                        match = re.search(r'\((\d+)\)', tab_text)
                        current = int(match.group(1)) if match else 0
                        log(f"  Not submitted: {current}/{expected}")
                        if current >= expected:
                            log(f"  アップロード確認完了: {current}件")
                            break
                    except Exception:
                        pass
                    if attempt < 3:
                        log("  まだ準備中... 30秒待機")
                        time.sleep(30)
                else:
                    log("[!] アップロード確認タイムアウト。再待機します...")
                    for retry in range(6):
                        time.sleep(30)
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                        time.sleep(4)
                        _close_popups(page)
                        try:
                            tab_text = page.locator('[data-testid="tab-not_submitted"]').inner_text(timeout=5000).strip()
                            match = re.search(r'\((\d+)\)', tab_text)
                            current = int(match.group(1)) if match else 0
                            if current >= expected:
                                log(f"  再待機で反映確認: {current}件")
                                break
                            log(f"  ...再待機 {(retry+1)*30}秒, 現在{current}/{expected}件")
                        except Exception:
                            pass
                    else:
                        log(f"[!] 再待機タイムアウト: {current}/{expected}件で続行します")

            # ============================================================
            # STEP 3: CSVメタデータを適用（全選択はCSV反映後に行う）
            # ============================================================
            log(f"CSV適用中: {csv_path.name}")
            csv_btn = page.locator('button[data-testid="csv-upload"]')
            try:
                csv_btn.wait_for(state="visible", timeout=8000)
            except PWTimeout:
                raise RuntimeError("CSV アップロードボタンが見つかりません")
            csv_btn.click()
            time.sleep(1)

            dialog = page.locator('[role="dialog"]')
            try:
                dialog.wait_for(state="visible", timeout=8000)
                log("CSVアップロードダイアログを開きました")
            except PWTimeout:
                raise RuntimeError("CSVアップロードダイアログが開きません")

            with page.expect_file_chooser(timeout=10000) as fc_info:
                dialog.get_by_role("button", name=re.compile("アップロード", re.I)).click()
            fc_info.value.set_files(str(csv_path))
            log(f"CSV セット完了: {csv_path.name}")
            time.sleep(4)

            # ダイアログが閉じるのを待つ（最大10秒）
            for _ in range(10):
                if not dialog.is_visible():
                    break
                time.sleep(1)

            # ページリロード → ポップアップ閉じ
            page.reload(wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            _close_popups(page)

            # 全選択 → CSVメタデータ反映を確認（keywordチップが付けば適用済み）
            # ※ ページreloadは選択を解除し、送信ボタン（選択時のみ表示）を消してしまうため行わない
            _select_all(page, log)
            time.sleep(2)
            kw_chips = page.locator('[data-testid^="selected-keyword-"]').count()
            if kw_chips > 0:
                log(f"[OK] CSVメタデータ反映確認（keyword {kw_chips}件）")
            else:
                log("[!] keyword未検出。10秒待って再選択し再確認...")
                time.sleep(10)
                _ensure_all_selected(page, log)
                kw_chips = page.locator('[data-testid^="selected-keyword-"]').count()
                log(f"[!] 再確認: keyword {kw_chips}件")

            log("CSV適用完了")

            # ============================================================
            # STEP 4: 画像を審査提出
            # ============================================================
            if skip_submit:
                log("[テストモード] 審査提出ボタンの手前で停止します。ブラウザで手動操作してください。")
            else:
                log("画像: 審査提出中...")
                # 送信ボタンは素材が選択されている時のみ表示されるため、直前に再選択
                _ensure_all_selected(page, log)
                submit_btn = page.locator('[data-testid="edit-dialog-submit-button"]')
                try:
                    submit_btn.wait_for(state="visible", timeout=10000)
                    submit_btn.click()
                    time.sleep(4)
                    log("[OK] 画像: 送信ボタンクリック完了")
                except PWTimeout:
                    log("[NG] 提出ボタンが見つかりません")

                try:
                    tab_text = page.locator('[data-testid="tab-not_submitted"]').inner_text(timeout=5000).strip()
                    if "(0)" in tab_text:
                        log("[OK] 画像: 全件提出完了")
                    else:
                        log(f"[!] 画像: 未提出が残っています: {tab_text}")
                except PWTimeout:
                    log("[!] 画像: 提出後の確認ができませんでした")

                submitted_photo = submitted
                submitted += 1  # カウントは概算

                # ============================================================
                # STEP 5: 動画タブに切り替えて提出
                # ============================================================
                log("\n動画タブに切り替え中...")
                page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=30000)
                time.sleep(4)
                _close_popups(page)

                try:
                    tab_text = page.locator('[data-testid="tab-not_submitted"]').inner_text(timeout=5000).strip()
                    log(f"動画 not_submitted: {tab_text}")
                    if "(0)" in tab_text:
                        log("未提出動画なし。スキップ")
                    else:
                        log("動画: 全選択して提出...")
                        try:
                            page.locator('input[type="checkbox"]').first.wait_for(state="visible", timeout=15000)
                        except PWTimeout:
                            log("[!] 動画チェックボックスが見つかりません")
                        _select_all(page, log)

                        submit_btn = page.locator('[data-testid="edit-dialog-submit-button"]')
                        try:
                            submit_btn.wait_for(state="visible", timeout=8000)
                            submit_btn.click()
                            time.sleep(4)
                            log("[OK] 動画: 提出ボタンクリック完了")
                        except PWTimeout:
                            log("[NG] 動画: 提出ボタンが見つかりません")

                        try:
                            tab_text_after = page.locator('[data-testid="tab-not_submitted"]').inner_text(timeout=5000).strip()
                            if "(0)" in tab_text_after:
                                log("[OK] 動画: 全件提出完了")
                            else:
                                log(f"[!] 動画: 未提出が残っています: {tab_text_after}")
                        except PWTimeout:
                            log("[!] 動画: 提出後の確認ができませんでした")

                except PWTimeout:
                    log("[!] 動画: not_submittedタブの確認ができませんでした")

        except Exception as e:
            log(f"[NG] Error: {e}")
            errors.append(str(e))
            raise
        finally:
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


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python shutterstock_portal.py <csv_path>")
        sys.exit(1)
    result = run_portal_automation(
        csv_path=Path(sys.argv[1]),
        progress_callback=print,
        headless=False,
    )
    print(f"\nDone: submitted={result['submitted']} / errors={len(result['errors'])}")
