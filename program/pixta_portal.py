#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pixta イラストアップロード自動化
- pixta_session.json のセッションを使用（ログイン不要）
- ファイルアップロード → IPTC反映 → 確定 → 全選択 → 登録 → 審査申請
"""

import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from paths import PIXTA_SESSION as SESSION_FILE
UPLOAD_URL = "https://pixta.jp/mypage/upload/illustration#per_page=100&sort=4"
UPLOAD_URL_PHOTO = "https://pixta.jp/mypage/upload/photo#per_page=100&sort=4"


def _check_ai_generated_on_list(page, log, ai_filenames=None):
    """
    アップロードページのペンディングリストでAI生成チェックボックスをONにする。
    ai_filenames: Noneなら全件チェック、setならファイル名が一致するもののみチェック。
    """
    ai_checkboxes = page.locator("input.is_ai_generated")
    count = ai_checkboxes.count()
    if count == 0:
        log("[!] AI生成チェックボックスが見つかりません")
        return

    if ai_filenames is None:
        # 全件チェック（後方互換）
        log(f">> AI生成チェックボックスを {count} 件ONにします...")
        for i in range(count):
            cb = ai_checkboxes.nth(i)
            try:
                if not cb.is_checked():
                    cb.click(force=True)
                    time.sleep(0.3)
            except Exception as e:
                log(f"  [!] チェックボックス {i+1} の操作に失敗: {e}")
    else:
        # 選択的チェック: ファイル名がai_filenamesに含まれるもののみ
        log(f">> AI生成チェックボックスを選択的にONにします（対象: {len(ai_filenames)}件）...")
        for i in range(count):
            cb = ai_checkboxes.nth(i)
            try:
                # チェックボックスのid (例: "131463966-is_ai_generated") からアイテムIDを取得し、
                # 対応する div#<id>-checkbox-field 内の span からファイル名を取得
                filename = cb.evaluate("""(el) => {
                    let cbId = el.id || '';
                    let itemId = cbId.replace('-is_ai_generated', '');
                    if (itemId) {
                        let header = document.getElementById(itemId + '-checkbox-field');
                        if (header) {
                            let spans = header.querySelectorAll('span');
                            for (let s of spans) {
                                let t = s.textContent.trim();
                                if (t.match(/\\.(png|jpg|jpeg|gif|eps|zip|mp4|mov|ai)$/i)) return t;
                            }
                        }
                    }
                    return '';
                }""")
                if filename and filename in ai_filenames:
                    if not cb.is_checked():
                        cb.click(force=True)
                        time.sleep(0.3)
                        log(f"  {filename}: AI生成チェックON")
                elif not filename:
                    log(f"  [!] アイテム{i+1}: ファイル名取得できず、スキップ")
            except Exception as e:
                log(f"  [!] チェックボックス {i+1} の操作に失敗: {e}")

    checked = page.locator("input.is_ai_generated:checked").count()
    log(f"[OK] AI生成チェックボックス: {checked}/{count} ON")


def _launch(p):
    """ブラウザ・コンテキスト共通設定"""
    browser = p.chromium.launch(
        headless=False,
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
    return browser, context


def run_upload_and_submit(files: list, progress_callback=None, skip_submit: bool = False, is_ai: bool = False, is_photo: bool = False, ai_filenames: set = None, no_wait: bool = False, playwright_instance=None) -> dict:
    """
    Pixta イラストアップロード → 審査申請 を全自動で実行する。

    フロー:
      【Phase 1: ファイルアップロード】
      1. /mypage/upload/illustration を開く
      2. 「作品を選択」ボタン → file chooser で全ファイルをセット
      3. disabled-btn が付くまで待機（アップロード開始確認）
      4. disabled-btn が外れるまで待機（アップロード完了）
      5. #use_exif チェックボックスをON（IPTC情報を反映）
      6. アップロードボタンをクリック → ファイルをペンディングリストへ確定

      【Phase 2: 審査申請】
      7. #all-1 チェックボックスをクリック（全選択）
      8. input[value="選択した作品を登録"] をクリック
      9. /confirm ページへ遷移確認
      10. #confirm_upload_btn をクリック（審査申請）
      11. /confirm_complete への遷移で完了確認

    Args:
        files: アップロードするファイルのリスト（Pathオブジェクト）
        progress_callback: ログ出力用コールバック (str) -> None
    Returns:
        {"uploaded": int, "submitted": int, "errors": list}
    """

    def log(msg: str):
        if progress_callback:
            progress_callback(msg)
        else:
            print(msg)

    if not SESSION_FILE.exists():
        raise FileNotFoundError(
            f"Session file not found: {SESSION_FILE}\n"
            "Run pixta_login.py first."
        )

    missing = [f for f in files if not Path(f).exists()]
    if missing:
        raise FileNotFoundError(f"Files not found: {missing}")

    if not files:
        log("[!] No files to upload.")
        return {"uploaded": 0, "submitted": 0, "errors": []}

    errors = []
    uploaded = 0
    submitted = 0

    url = UPLOAD_URL_PHOTO if is_photo else UPLOAD_URL

    _own_playwright = playwright_instance is None
    p = sync_playwright().start() if _own_playwright else playwright_instance
    try:
        browser, context = _launch(p)
        page = context.new_page()
        _keep_open = skip_submit

        try:
            # -------------------------------------------------------
            # Phase 1: ファイルアップロード
            # -------------------------------------------------------
            log(f"Opening Pixta upload page ({'写真' if is_photo else 'イラスト'})...")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)

            if "sign_in" in page.url or "login" in page.url:
                raise PermissionError("Session expired. Run pixta_login.py again.")
            log(f"Page loaded: {page.url}")

            # アップロード前のペンディング件数を記録
            items_before = page.locator("input.submit_items").count()
            log(f"Items already in pending list: {items_before}")

            # 「作品を選択」ボタン → file chooser で全ファイルをセット
            log(f"Selecting {len(files)} file(s) via file chooser...")
            upload_btn = page.locator("a.upload-button").first
            upload_btn.wait_for(state="visible", timeout=10000)

            with page.expect_file_chooser() as fc_info:
                upload_btn.click()
            fc_info.value.set_files([str(f) for f in files])
            log(f"Files set: {[Path(f).name for f in files]}")

            # アップロード開始を待機（disabled-btn が付くまで最大60秒）
            log("Waiting for upload to start...")
            submit_btn = page.locator("input[name='commit'][value='アップロード']")
            upload_started = False
            for i in range(60):
                time.sleep(1)
                try:
                    cls = submit_btn.get_attribute("class") or ""
                    if "disabled-btn" in cls:
                        log(f"Upload started (button disabled at {i+1}s)")
                        upload_started = True
                        break
                except Exception:
                    pass

            if not upload_started:
                log("[!] disabled-btn never appeared - upload may have started differently, continuing...")

            # アップロード完了を待機（disabled-btn が外れるまで最大5分）
            log("Waiting for upload to complete...")
            for i in range(300):
                time.sleep(1)
                try:
                    cls = submit_btn.get_attribute("class") or ""
                    if "disabled-btn" not in cls:
                        log(f"Upload complete ({i+1}s elapsed)")
                        break
                    if i % 15 == 0 and i > 0:
                        log(f"  ...still uploading ({i}s)")
                except Exception:
                    pass
            else:
                raise TimeoutError("Upload timed out after 5 minutes")

            # IPTC情報を反映チェックボックスをON
            log("Enabling IPTC metadata checkbox...")
            use_exif = page.locator("#use_exif")
            try:
                use_exif.wait_for(state="visible", timeout=5000)
                if not use_exif.is_checked():
                    page.locator("label[for='use_exif']").click()
                    time.sleep(0.5)
                log("IPTC checkbox: " + ("ON" if use_exif.is_checked() else "[!] may not be checked"))
            except PWTimeout:
                log("[!] IPTC checkbox not found - continuing")

            # サーバー側の処理完了を待機（ファイル数に応じて調整）
            wait_sec = max(10, len(files) * 2)
            log(f"Waiting {wait_sec}s for server to process {len(files)} file(s)...")
            time.sleep(wait_sec)

            # アップロ���ドボタンをクリック（ペンディングリストへ確定）
            log("Clicking upload confirm button...")
            submit_btn.wait_for(state="visible", timeout=5000)
            try:
                submit_btn.click(timeout=60000)
            except PWTimeout:
                log("[!] Click navigation timeout - continuing (page may still be loading)")
            time.sleep(5)
            log(f"After upload-confirm URL: {page.url}")

            # per_page=100で再読み込みして全件表示
            log("Reloading with per_page=100 to show all items...")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)

            # ペンディングリストに件数が増えるまで待機
            log("Waiting for files to appear in pending list...")
            expected_total = items_before + len(files)
            for i in range(60):
                items_now = page.locator("input.submit_items").count()
                if items_now >= expected_total:
                    log(f"Files confirmed in pending list ({items_now} total)")
                    uploaded = len(files)
                    break
                if i % 10 == 0:
                    log(f"  ...waiting for list update ({i}s, current: {items_now}/{expected_total})")
                time.sleep(2)
            else:
                items_now = page.locator("input.submit_items").count()
                log(f"[!] Expected {expected_total} items, got {items_now}. 再読み込みで再試行...")
                for retry in range(6):
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    time.sleep(5)
                    items_now = page.locator("input.submit_items").count()
                    if items_now >= expected_total:
                        log(f"再試行で反映確認: {items_now}件")
                        break
                    log(f"  ...再試行 {retry+1}/6, 現在{items_now}/{expected_total}件")
                else:
                    log(f"[!] タイムアウト: {items_now}/{expected_total}件で続行します")
                uploaded = len(files)

            # -------------------------------------------------------
            # Phase 2: 審査申請
            # -------------------------------------------------------
            # ページネーション対応: 全ページの作品を選択→登録を繰り返す
            total_submitted_pages = 0
            page_num = 0
            while True:
                page_num += 1
                log(f"--- ページ {page_num} の処理 ---")

                # AI素材: 全選択の前に各アイテムのAI生成チェックボックスをON
                if is_ai or ai_filenames:
                    _check_ai_generated_on_list(page, log, ai_filenames=ai_filenames)

                all_cb = page.locator("#item_submit_form #all-1")
                try:
                    all_cb.wait_for(state="visible", timeout=10000)
                except PWTimeout:
                    log("選択チェックボックスが見つかりません（全ページ処理済み）")
                    break

                all_cb.click()
                time.sleep(1)

                total_items = page.locator("input.submit_items").count()
                checked_count = page.locator("input.submit_items:checked").count()
                log(f"Checked items: {checked_count}/{total_items}")

                if checked_count < total_items and total_items > 0:
                    log("[!] Select-all missed some items. Clicking individually...")
                    for item in page.locator("input.submit_items").all():
                        try:
                            if not item.is_checked():
                                item.click(force=True)
                                time.sleep(0.1)
                        except Exception:
                            pass
                    time.sleep(0.5)
                    checked_count = page.locator("input.submit_items:checked").count()
                    log(f"Checked after individual click: {checked_count}/{total_items}")

                if checked_count == 0:
                    if total_submitted_pages == 0:
                        log("[!] No items checked after select-all click.")
                        return {"uploaded": uploaded, "submitted": 0, "errors": ["No items checked"]}
                    break

                total_submitted_pages += checked_count

                if skip_submit:
                    log("[テストモード] 全選択完了。登録ボタンの手前で停止します。ブラウザで手動操作してください。")
                    break

                log("Clicking register button...")
                reg_btn = page.locator("input[value='選択した作品を登録']")
                reg_btn.wait_for(state="visible", timeout=5000)
                reg_btn.click()
                time.sleep(4)
                log(f"After register URL: {page.url}")

                # 登録後にまだアップロードページにいる場合 → 次ページの作品がある
                if "confirm" not in page.url:
                    # まだ残りがある可能性: ページをリロードして次の作品を処理
                    remaining = page.locator("input.submit_items").count()
                    if remaining > 0:
                        log(f"残り {remaining} 件の作品があります。次のページを処理します...")
                        time.sleep(2)
                        continue
                    else:
                        raise RuntimeError(f"Expected confirm page but got: {page.url}")
                break

            checked_count = total_submitted_pages
            log(f"Total items selected across all pages: {checked_count}")

            if skip_submit:
                pass  # 登録ページで停止済み — confirmページには遷移しない
            elif "confirm" not in page.url:
                raise RuntimeError(f"Expected confirm page but got: {page.url}")
            else:
                log("Waiting for confirm page to fully load...")
                page.wait_for_load_state("networkidle", timeout=30000)
                time.sleep(2)

                log("Clicking submit-for-review button...")
                confirm_btn = page.locator("#confirm_upload_btn")
                try:
                    confirm_btn.scroll_into_view_if_needed(timeout=5000)
                    confirm_btn.wait_for(state="visible", timeout=30000)
                except PWTimeout:
                    log(f"[DEBUG] confirm page URL: {page.url}")
                    log(f"[DEBUG] page title: {page.title()}")
                    exists = confirm_btn.count()
                    log(f"[DEBUG] #confirm_upload_btn count in DOM: {exists}")
                    if exists > 0:
                        log("[DEBUG] Element exists but not visible, trying force click...")
                        confirm_btn.click(force=True)
                        log("Force-clicked 審査申請 button")
                    else:
                        raise RuntimeError("#confirm_upload_btn not found on confirm page")
                else:
                    confirm_btn.click()
                    log("Clicked 審査申請 button")

                # AI生成モーダルが表示された場合の処理
                if is_ai or ai_filenames:
                    try:
                        modal = page.locator("div.modal-ai-generated-submit")
                        modal.wait_for(state="visible", timeout=5000)
                        log("AI生成確認モーダルが表示されました")
                        submit_continue = page.locator("#submit-continue")
                        submit_continue.click()
                        log("[OK] モーダルの続行ボタンをクリック")
                        time.sleep(3)
                    except PWTimeout:
                        log("(AI生成モーダルは表示されませんでした)")

                time.sleep(5)
                log(f"Final URL: {page.url}")

                if "confirm_complete" in page.url or "complete" in page.url or "manager" in page.url:
                    submitted = checked_count
                    log(f"[OK] Upload & submission complete! {uploaded} uploaded, {submitted} submitted.")
                else:
                    log(f"[?] Unexpected URL: {page.url}")
                    submitted = checked_count

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

    return {"uploaded": uploaded, "submitted": submitted, "errors": errors}


def run_submit(progress_callback=None, is_ai: bool = False, is_photo: bool = False) -> dict:
    """
    ファイルがすでにPixtaにアップロード済みの場合に、審査申請のみ実行する。

    フロー:
      1. /mypage/upload/illustration を開く
      2. #all-1 チェックボックスをクリック（全選択）
      3. input[value="選択した作品を登録"] をクリック
      4. /confirm ページ → #confirm_upload_btn をクリック（審査申請）
      5. /confirm_complete への遷移で完了確認

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
            "Run pixta_login.py first."
        )

    errors = []
    submitted = 0

    url = UPLOAD_URL_PHOTO if is_photo else UPLOAD_URL

    with sync_playwright() as p:
        browser, context = _launch(p)
        page = context.new_page()

        try:
            log(f"Opening Pixta upload page ({'写真' if is_photo else 'イラスト'})...")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)

            if "sign_in" in page.url or "login" in page.url:
                raise PermissionError("Session expired. Run pixta_login.py again.")
            log(f"Page loaded: {page.url}")

            # ページネーション対応: 全ページの作品を選択→登録を繰り返す
            total_submitted_pages = 0
            page_num = 0
            while True:
                page_num += 1
                log(f"--- ページ {page_num} の処理 ---")

                # AI素材: 全選択の前に各アイテムのAI生成チェックボックスをON
                if is_ai or ai_filenames:
                    _check_ai_generated_on_list(page, log, ai_filenames=ai_filenames)

                all_cb = page.locator("#item_submit_form #all-1")
                try:
                    all_cb.wait_for(state="visible", timeout=10000)
                except PWTimeout:
                    log("選択チェックボックスが見つかりません（全ページ処理済み）")
                    break

                all_cb.click()
                time.sleep(1)

                total_items = page.locator("input.submit_items").count()
                checked_count = page.locator("input.submit_items:checked").count()
                log(f"Checked items: {checked_count}/{total_items}")

                if checked_count < total_items and total_items > 0:
                    log("[!] Select-all missed some items. Clicking individually...")
                    for item in page.locator("input.submit_items").all():
                        try:
                            if not item.is_checked():
                                item.click(force=True)
                                time.sleep(0.1)
                        except Exception:
                            pass
                    time.sleep(0.5)
                    checked_count = page.locator("input.submit_items:checked").count()
                    log(f"Checked after individual click: {checked_count}/{total_items}")

                if checked_count == 0:
                    if total_submitted_pages == 0:
                        log("[!] No items to submit.")
                        return {"submitted": 0, "errors": ["No items checked"]}
                    break

                total_submitted_pages += checked_count

                log("Clicking register button...")
                reg_btn = page.locator("input[value='選択した作品を登録']")
                reg_btn.wait_for(state="visible", timeout=5000)
                reg_btn.click()
                time.sleep(4)
                log(f"After register URL: {page.url}")

                if "confirm" not in page.url:
                    remaining = page.locator("input.submit_items").count()
                    if remaining > 0:
                        log(f"残り {remaining} 件の作品があります。次のページを処理します...")
                        time.sleep(2)
                        continue
                    else:
                        raise RuntimeError(f"Expected confirm page but got: {page.url}")
                break

            checked_count = total_submitted_pages
            log(f"Total items selected across all pages: {checked_count}")

            log("Waiting for confirm page to fully load...")
            page.wait_for_load_state("networkidle", timeout=30000)
            time.sleep(2)

            log("Clicking submit-for-review button...")
            confirm_btn = page.locator("#confirm_upload_btn")
            try:
                confirm_btn.scroll_into_view_if_needed(timeout=5000)
                confirm_btn.wait_for(state="visible", timeout=30000)
            except PWTimeout:
                log(f"[DEBUG] confirm page URL: {page.url}")
                log(f"[DEBUG] page title: {page.title()}")
                exists = confirm_btn.count()
                log(f"[DEBUG] #confirm_upload_btn count in DOM: {exists}")
                if exists > 0:
                    log("[DEBUG] Element exists but not visible, trying force click...")
                    confirm_btn.click(force=True)
                    log("Force-clicked 審査申請 button")
                else:
                    raise RuntimeError("#confirm_upload_btn not found on confirm page")
            else:
                confirm_btn.click()
                log("Clicked 審査申請 button")

            # AI生成モーダルが表示された場合の処理
            if is_ai:
                try:
                    modal = page.locator("div.modal-ai-generated-submit")
                    modal.wait_for(state="visible", timeout=5000)
                    log("AI生成確認モーダルが表示されました")
                    submit_continue = page.locator("#submit-continue")
                    submit_continue.click()
                    log("[OK] モーダルの続行ボタンをクリック")
                    time.sleep(3)
                except PWTimeout:
                    log("(AI生成モーダルは表示されませんでした)")

            time.sleep(5)
            log(f"Final URL: {page.url}")

            if "confirm_complete" in page.url or "complete" in page.url or "manager" in page.url:
                submitted = checked_count
                log(f"[OK] Submission complete! {submitted} file(s) submitted.")
            else:
                log(f"[?] Unexpected URL: {page.url}")
                submitted = checked_count

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
    sys.path.insert(0, str(Path(__file__).parent))
    from stock_tagger import get_upload_targets

    input_folder = Path("input")
    targets = get_upload_targets(input_folder, "pixta")

    if not targets:
        print("No Pixta upload targets found.")
        sys.exit(1)

    print(f"Files to upload: {[f.name for f in targets]}")
    result = run_upload_and_submit(files=targets, progress_callback=print)
    print(f"\nDone: uploaded={result['uploaded']} / submitted={result['submitted']} / errors={len(result['errors'])}")
