#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pixta 動画（フッテージ）アップロード自動化
- pixta_session.json のセッションを使用（ログイン不要）
- ファイルアップロード → サムネイル生成待機 → タイトル/タグ入力 → 全選択 → 登録 → 確認 → 審査申請
"""

import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SESSION_FILE = Path(__file__).parent / "pixta_session.json"
UPLOAD_URL = "https://pixta.jp/mypage/upload/new_footage"


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


def run_footage_upload(
    files: list,
    metadata: list,
    progress_callback=None,
) -> dict:
    """
    Pixta 動画アップロード → タイトル/タグ入力 → 審査申請 を全自動で実行する。

    フロー:
      【Phase 1: ファイルアップロード】
      1. /mypage/upload/new_footage を開く
      2. 「作品を選択」ボタン → file chooser で動画ファイルをセット
      3. disabled-btn が付くまで待機（アップロード開始確認）
      4. disabled-btn が外れるまで待機（アップロード完了）
      5. アップロードボタンをクリック → ペンディングリストへ確定

      【Phase 2: サムネイル生成待機 + タイトル/タグ入力】
      6. input.title が表示されるまで1分ごとにポーリング（最大10分）
      7. 各動画のタイトルを input.title に入力
      8. 各動画のタグを input.input-tags に1件ずつEnterキーで入力

      【Phase 3: 審査申請】
      9. #all-1 チェックボックスをクリック（全選択）
      10. input[value="選択した作品を登録"] をクリック
      11. 確認ページ → 最終登録ボタンをクリック（審査申請）
      12. 完了確認

    Args:
        files: アップロードするファイルのリスト（Pathオブジェクト）
        metadata: 各ファイルのメタデータリスト
                  [{"title": str, "tags": list[str]}, ...]
                  files と同じ順番・同じ件数で渡すこと
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

    if len(files) != len(metadata):
        raise ValueError(f"files ({len(files)}) and metadata ({len(metadata)}) must have the same length")

    errors = []
    uploaded = 0
    submitted = 0

    with sync_playwright() as p:
        browser, context = _launch(p)
        page = context.new_page()

        try:
            # -------------------------------------------------------
            # Phase 1: ファイルアップロード
            # -------------------------------------------------------
            log("Opening Pixta footage upload page...")
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            if "sign_in" in page.url or "login" in page.url:
                raise PermissionError("Session expired. Run pixta_login.py again.")
            log(f"Page loaded: {page.url}")

            # 「作品を選択」ボタン → file chooser で全ファイルをセット
            log(f"Selecting {len(files)} file(s) via file chooser...")
            upload_btn = page.locator("a.upload-button").first
            upload_btn.wait_for(state="visible", timeout=10000)

            with page.expect_file_chooser() as fc_info:
                upload_btn.click()
            fc_info.value.set_files([str(f) for f in files])
            log(f"Files set: {[Path(f).name for f in files]}")

            # 5秒待ってからアップロードボタンをクリック
            log("Waiting 5 seconds before clicking upload button...")
            time.sleep(5)
            submit_btn = page.locator("button[data-bind*='uploadEvent']")
            log("Clicking upload button...")
            submit_btn.wait_for(state="visible", timeout=10000)
            submit_btn.click()
            time.sleep(3)
            log(f"Upload button clicked. URL: {page.url}")

            uploaded = len(files)

            # -------------------------------------------------------
            # Phase 2: サムネイル生成待機 + タイトル/タグ入力
            # -------------------------------------------------------
            log("Waiting for thumbnail generation (3 min initial wait, then polling every 60s)...")
            log("  Waiting 3 minutes for thumbnail generation...")
            time.sleep(180)
            title_visible = False
            expected_inputs = len(files)
            for attempt in range(7):
                log(f"  Reloading page (attempt {attempt+1}/7)...")
                page.reload(wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)
                try:
                    page.locator("input.title").first.wait_for(state="visible", timeout=5000)
                    found = len(page.locator("input.title").all())
                    log(f"  Title inputs found: {found}/{expected_inputs}")
                    if found >= expected_inputs:
                        log("All title inputs ready!")
                        title_visible = True
                        break
                    else:
                        log(f"  Not all thumbnails ready yet. Waiting 60 seconds...")
                        time.sleep(60)
                except PWTimeout:
                    if attempt < 6:
                        log(f"  Title input not ready yet. Waiting 60 seconds...")
                        time.sleep(60)

            if not title_visible:
                raise TimeoutError("Title inputs did not appear within 10 minutes. Check Pixta portal manually.")

            # 各動画にタイトルとタグを入力（ファイル名で対応付け）
            import subprocess as _subprocess
            import platform as _platform
            _is_mac = _platform.system() == "Darwin"

            # metadataをファイル名（stem）→メタ のdictに変換
            meta_by_stem = {}
            for vf, m in zip(files, metadata):
                stem = Path(vf).stem
                meta_by_stem[stem] = m

            # ページ上のアイテムIDを取得してファイル名と照合
            item_ids = page.locator("input.submit_items").evaluate_all(
                "els => els.map(el => el.id)"
            )
            log(f"Found {len(item_ids)} item(s) on page")

            for item_id in item_ids:
                # ファイル名を取得（例: "1378581/footage / 260401_starburst_yellow.mp4"）
                try:
                    filename_raw = page.locator(f'[id="{item_id}-filename"]').inner_text(timeout=3000).strip()
                except Exception:
                    log(f"[{item_id}] [!] Could not read filename, skipping")
                    continue

                # stemを抽出（パス区切り後の最後の部分から拡張子除去）
                filename_part = filename_raw.split("/")[-1].strip()
                stem = Path(filename_part).stem

                meta = meta_by_stem.get(stem)
                if not meta:
                    log(f"[{item_id}] [!] No metadata for '{stem}', skipping")
                    continue

                title_text = meta.get("title", "")[:50]
                tags = meta.get("tags", [])[:50]
                log(f"[{item_id}] '{stem}': title='{title_text}', tags={len(tags)}")

                # タイトル入力
                if title_text:
                    try:
                        title_inp = page.locator(f'[id="{item_id}-title"]')
                        title_inp.scroll_into_view_if_needed()
                        title_inp.click(click_count=3)
                        title_inp.fill(title_text)
                        log(f"[{item_id}] Title set: {title_text}")
                        time.sleep(0.3)
                    except Exception as e:
                        log(f"[{item_id}] [!] Failed to set title: {e}")
                        errors.append(f"Title input failed for {stem}: {e}")

                # タグ入力（クリップボード経由でペースト）
                if tags:
                    try:
                        tag_str = ",".join(tags)
                        if _is_mac:
                            _subprocess.run(
                                ["pbcopy"],
                                input=tag_str.encode("utf-8"),
                                check=True,
                            )
                        else:
                            _subprocess.run(
                                ["powershell", "-command", f"Set-Clipboard -Value '{tag_str}'"],
                                check=True,
                            )
                        tag_inp = page.locator(f'[id="{item_id}-input-tags"]')
                        tag_inp.scroll_into_view_if_needed()
                        tag_inp.click()
                        time.sleep(0.2)
                        tag_inp.press("Meta+v" if _is_mac else "Control+v")
                        log(f"[{item_id}] Tags pasted: {len(tags)} tags")
                        time.sleep(0.8)
                    except Exception as e:
                        log(f"[{item_id}] [!] Failed to paste tags: {e}")
                        errors.append(f"Tag paste failed for {stem}: {e}")

            # -------------------------------------------------------
            # Phase 3: 審査申請
            # -------------------------------------------------------
            log("Selecting all items for submission...")
            all_cb = page.locator("#all-1")
            all_cb.wait_for(state="visible", timeout=10000)
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
                log("[!] No items checked after select-all click.")
                return {"uploaded": uploaded, "submitted": 0, "errors": errors + ["No items checked"]}

            log("Clicking register button...")
            reg_btn = page.locator("input[value='選択した作品を登録']")
            reg_btn.wait_for(state="visible", timeout=5000)
            reg_btn.click()
            time.sleep(4)
            log(f"After register URL: {page.url}")

            if "confirm" not in page.url:
                raise RuntimeError(f"Expected confirm page but got: {page.url}")

            log("Waiting for confirm page to fully load...")
            page.wait_for_load_state("networkidle", timeout=30000)
            time.sleep(2)

            log("Clicking submit-for-review button on confirm page...")
            confirm_btn = page.locator("input[type='submit'][value='審査申請']")
            confirm_btn.scroll_into_view_if_needed(timeout=5000)
            confirm_btn.wait_for(state="visible", timeout=30000)
            confirm_btn.click()
            log("Clicked 審査申請 button")

            time.sleep(5)
            log(f"Final URL: {page.url}")

            if "confirm_complete" in page.url or "complete" in page.url or "manager" in page.url:
                submitted = checked_count
                log(f"[OK] Upload & submission complete! {uploaded} uploaded, {submitted} submitted.")
            else:
                log(f"[?] Unexpected final URL: {page.url}")
                submitted = checked_count

        except Exception as e:
            log(f"[NG] Error: {e}")
            errors.append(str(e))
            raise
        finally:
            context.close()
            browser.close()

    return {"uploaded": uploaded, "submitted": submitted, "errors": errors}


# --------------------------------------------------------------
# スタンドアロン実行（デバッグ用）
# --------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path

    # テスト用: 素材フォルダの動画ファイルを対象にする
    input_folder = Path("input")
    video_exts = {".mp4", ".mov", ".avi", ".m4v"}
    video_files = [f for f in input_folder.iterdir() if f.suffix.lower() in video_exts]

    if not video_files:
        print("No video files found in input folder.")
        sys.exit(1)

    print(f"Video files found: {[f.name for f in video_files]}")

    # テスト用メタデータ（実際はstock_tagger.pyのGemini分析結果を使う）
    test_metadata = [
        {
            "title": "紫色のキラキラ星が点滅する背景アニメーション【ループ】",
            "tags": [
                "紫", "パープル", "ピンク", "星", "スター", "キラキラ", "スパークル", "グリッター",
                "輝き", "煌めき", "光", "エフェクト", "アニメーション", "ループ", "背景",
                "バックグラウンド", "点滅", "瞬き", "十字", "クロス", "幻想的", "ファンタジー",
                "夢", "魔法", "ロマンチック", "かわいい", "ポップ", "ガーリー", "イベント",
                "パーティー", "演出", "装飾", "デジタル", "CG", "映像素材", "シンプル",
                "エレガント", "綺麗", "美しい", "暗闇", "夜空", "スペース", "コスモ",
                "宇宙", "明るい", "華やか", "ゴージャス", "デザイン", "素材", "黒背景",
            ],
        }
    ]

    # ファイル数とメタデータ数を合わせる
    files_to_upload = video_files[:len(test_metadata)]

    result = run_footage_upload(
        files=files_to_upload,
        metadata=test_metadata,
        progress_callback=print,
    )
    print(f"\nDone: uploaded={result['uploaded']} / submitted={result['submitted']} / errors={len(result['errors'])}")
