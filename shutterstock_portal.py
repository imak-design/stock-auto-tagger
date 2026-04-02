#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shutterstock コントリビューターポータル 自動化
- shutterstock_session.json のセッションを使用（ログイン不要）
- ブラウザアップロード → CSV アップロードでメタデータを一括適用 → 審査提出
"""

import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SESSION_FILE = Path(__file__).parent / "shutterstock_session.json"
PORTAL_URL = "https://submit.shutterstock.com/ja/portfolio/not_submitted/photo"


def _close_popups(page):
    """アナウンスポップアップを閉じる"""
    try:
        close_btn = page.locator('[data-testid="announcement-close"]').first
        if close_btn.is_visible(timeout=2000):
            close_btn.click()
            time.sleep(1)
    except PWTimeout:
        pass
    page.keyboard.press("Escape")
    time.sleep(1)


def run_portal_automation(csv_path: Path, progress_callback=None, headless: bool = False,
                          files: list = None, expected_count: int = 0):
    """
    Shutterstockポータルでブラウザアップロード → CSV メタデータ適用 → 審査提出する。

    フロー:
      1. /ja/portfolio/not_submitted/photo を開く
      2. files指定時: uploadButton → file chooser → ファイルセット → 60秒待機 → リロード確認
      3. チェックボックスで全選択
      4. data-testid="csv-upload" ボタン → file chooser で CSV をセット
      5. [data-testid="edit-dialog-submit-button"] で一括送信

    Args:
        csv_path: shutterstock_*.csv のパス
        progress_callback: ログ出力用コールバック (str) -> None
        headless: ヘッドレス実行するか
        files: ブラウザアップロードするファイルのリスト（Pathオブジェクト）
        expected_count: 反映を待つファイル数（filesを渡す場合は自動設定）
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
            f"Session file not found: {SESSION_FILE}\n"
            "Run shutterstock_login.py first."
        )

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    if files:
        missing = [f for f in files if not Path(f).exists()]
        if missing:
            raise FileNotFoundError(f"Files not found: {missing}")
        expected_count = len(files)

    errors = []
    submitted = 0

    with sync_playwright() as p:
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

        try:
            # --- ポートフォリオを開く ---
            log("Opening Shutterstock not-submitted portfolio...")
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)

            if "login" in page.url:
                raise PermissionError(
                    "Session expired. Run shutterstock_login.py again."
                )
            log(f"Portal OK: {page.url}")
            _close_popups(page)

            # --- ブラウザアップロード ---
            if files:
                log(f"Uploading {len(files)} file(s) via browser...")
                upload_btn = page.locator('button[data-testid="uploadButton"]')
                try:
                    upload_btn.wait_for(state="visible", timeout=10000)
                except PWTimeout:
                    raise RuntimeError("Upload button not found on portfolio page")

                with page.expect_file_chooser() as fc_info:
                    upload_btn.click()
                fc_info.value.set_files([str(f) for f in files])
                log(f"Files set: {[Path(f).name for f in files]}")

                log("Waiting 60 seconds for upload to complete...")
                time.sleep(60)

                # リロードして反映確認（最大3回リトライ）
                not_submitted_tab = page.locator('[data-testid="tab-not_submitted"]')
                for attempt in range(4):
                    log(f"Reloading to check upload status (attempt {attempt + 1}/4)...")
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                    time.sleep(4)
                    _close_popups(page)
                    not_submitted_tab = page.locator('[data-testid="tab-not_submitted"]')
                    try:
                        tab_text = not_submitted_tab.inner_text(timeout=5000).strip()
                        match = re.search(r'\((\d+)\)', tab_text)
                        current = int(match.group(1)) if match else 0
                        log(f"  Not submitted: {current}/{expected_count}")
                        if current >= expected_count:
                            log(f"Upload confirmed: {current} file(s) ready.")
                            break
                    except Exception:
                        pass
                    if attempt < 3:
                        log("Files not ready yet. Waiting 30 seconds...")
                        time.sleep(30)
                else:
                    log("[!] Upload confirmation timed out. Continuing anyway...")

            # --- 未送信ファイル数チェック ---
            not_submitted_tab = page.locator('[data-testid="tab-not_submitted"]')
            try:
                tab_text = not_submitted_tab.inner_text(timeout=5000).strip()
                log(f"Not submitted tab: {tab_text}")
                if "(0)" in tab_text:
                    log("[!] No files to submit.")
                    return {"submitted": 0, "errors": ["No unsubmitted files"]}
            except PWTimeout:
                log("Could not read tab count, continuing...")

            # --- 全選択 ---
            log("Selecting all files...")
            try:
                page.locator('input[type="checkbox"]').first.wait_for(state="visible", timeout=15000)
            except PWTimeout:
                raise RuntimeError("No checkboxes found on portfolio page")
            cbs = page.locator('input[type="checkbox"]').all()
            if not cbs:
                raise RuntimeError("No checkboxes found on portfolio page")
            for cb in cbs:
                try:
                    cb.click(force=True)
                    time.sleep(0.2)
                except Exception:
                    pass
            time.sleep(2)
            log(f"Selected {len(cbs)} files")

            # --- CSV アップロード ---
            log("Uploading CSV metadata...")

            # 1. ツールバーのCSVボタンをクリック → ダイアログを開く
            csv_toolbar_btn = page.locator('button[data-testid="csv-upload"]')
            try:
                csv_toolbar_btn.wait_for(state="visible", timeout=8000)
            except PWTimeout:
                raise RuntimeError("CSV upload button not found")
            csv_toolbar_btn.click()
            time.sleep(1)

            # 2. ダイアログが表示されるのを待つ
            import re as _re
            dialog = page.locator('[role="dialog"]')
            try:
                dialog.wait_for(state="visible", timeout=8000)
                log("CSV upload dialog opened")
            except PWTimeout:
                raise RuntimeError("CSV upload dialog did not open")

            # 3. ダイアログ内のアップロードボタン → file chooser
            with page.expect_file_chooser(timeout=10000) as fc_info:
                dialog.get_by_role("button", name=_re.compile("アップロード", _re.I)).click()
            fc_info.value.set_files(str(csv_path))
            log(f"CSV set: {csv_path.name}")
            time.sleep(4)

            # --- メタデータ反映待機 ---
            log("メタデータ反映を確認中...")
            # ダイアログが閉じるのを待つ
            for _ in range(10):
                if not dialog.is_visible():
                    break
                time.sleep(1)
            time.sleep(3)

            # ページリロードしてメタデータ反映を確認
            page.reload(wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)

            # ポップアップ閉じ
            _close_popups(page)

            # 再度全選択
            log("メタデータ反映確認のため再選択...")
            try:
                page.locator('input[type="checkbox"]').first.wait_for(state="visible", timeout=15000)
                cbs = page.locator('input[type="checkbox"]').all()
                for cb in cbs:
                    try:
                        cb.click(force=True)
                        time.sleep(0.2)
                    except Exception:
                        pass
                time.sleep(2)
                log(f"再選択完了: {len(cbs)}件")
            except PWTimeout:
                log("[!] 再選択失敗。続行します。")

            # メタデータ反映確認（description欄が空でないかチェック）
            try:
                descriptions = page.locator('textarea[data-testid="description-input"], [data-testid="description"] textarea').all()
                empty_count = 0
                for desc in descriptions:
                    val = desc.input_value().strip()
                    if not val:
                        empty_count += 1
                if empty_count > 0:
                    log(f"[!] {empty_count}件のファイルにタイトル/説明が未設定です。10秒待機してリロード...")
                    time.sleep(10)
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                    time.sleep(5)
                    _close_popups(page)
                    # 再選択
                    cbs = page.locator('input[type="checkbox"]').all()
                    for cb in cbs:
                        try:
                            cb.click(force=True)
                            time.sleep(0.2)
                        except Exception:
                            pass
                    time.sleep(2)
                else:
                    log(f"[OK] メタデータ反映確認: 全{len(descriptions)}件にタイトル設定済み")
            except Exception:
                log("[!] メタデータ確認をスキップ（要素が見つかりません）")

            # --- 審査提出 ---
            log("Submitting for review...")
            submit_btn = page.locator('[data-testid="edit-dialog-submit-button"]')
            try:
                submit_btn.wait_for(state="visible", timeout=8000)
                submit_btn.click()
                time.sleep(4)
                log("Submit button clicked")
            except PWTimeout:
                raise RuntimeError("Submit button not found")

            # 送信後の確認
            try:
                tab_text_after = not_submitted_tab.inner_text(timeout=5000).strip()
                pending_text = page.locator('[data-testid="tab-pending"]').inner_text(timeout=5000).strip()
                log(f"After submit - Not submitted: {tab_text_after} / Pending: {pending_text}")
                if "(0)" in tab_text_after:
                    submitted = len(cbs)
                    log(f"[OK] All {submitted} files submitted successfully!")
                else:
                    log("[!] Some files may not have been submitted. Check portal manually.")
            except PWTimeout:
                log("Could not verify submission count")

        except Exception as e:
            log(f"[NG] Error: {e}")
            errors.append(str(e))
            raise
        finally:
            context.close()
            browser.close()

    return {"submitted": submitted, "errors": errors}


# --------------------------------------------------------------
# スタンドアロン実行（デバッグ用）
# --------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        csv_folder = Path(r"D:\stock illust\00作成\csv_output")
        csvs = sorted(csv_folder.glob("shutterstock_*.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not csvs:
            print("No CSV found. Specify path as argument.")
            sys.exit(1)
        csv_path = csvs[0]
    else:
        csv_path = Path(sys.argv[1])

    input_folder = Path(r"D:\stock illust\00作成")
    from stock_tagger import get_upload_targets
    files_to_upload = get_upload_targets(input_folder, "shutterstock")
    print(f"CSV: {csv_path}")
    print(f"Files: {[f.name for f in files_to_upload]}")

    result = run_portal_automation(
        csv_path=csv_path,
        files=files_to_upload if files_to_upload else None,
        progress_callback=print,
        headless=False,
    )
    print(f"\nDone: submitted={result['submitted']} / errors={len(result['errors'])}")
