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
from paths import PIXTA_SESSION as SESSION_FILE
UPLOAD_URL = "https://pixta.jp/mypage/upload/new_footage"


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
        # ai_filenamesを小文字のstem(拡張子なし)セットに変換（Pixtaはファイル名を小文字化するため）
        from pathlib import PurePosixPath as _PurePath
        ai_stems_lower = {_PurePath(f).stem.lower() for f in ai_filenames}
        log(f">> AI生成チェックボックスを選択的にONにします（対象: {len(ai_filenames)}件, stems={ai_stems_lower}）...")
        for i in range(count):
            cb = ai_checkboxes.nth(i)
            try:
                # チェックボックスのid (例: "16911052-is_ai_generated") からアイテムIDを取得し、
                # {itemId}-filename のテキストからファイル名を取得
                item_id = cb.evaluate("el => (el.id || '').replace('-is_ai_generated', '')")
                filename_raw = ""
                if item_id:
                    fn_el = page.locator(f'[id="{item_id}-filename"]')
                    if fn_el.count() > 0:
                        filename_raw = fn_el.inner_text(timeout=3000).strip()
                # stemを抽出（パス区切り後の最後の部分から拡張子除去、小文字化）
                filename_part = filename_raw.split("/")[-1].strip() if filename_raw else ""
                stem = _PurePath(filename_part).stem.lower() if filename_part else ""
                log(f"  [{item_id}] filename='{filename_raw}' → stem='{stem}'")
                if stem and stem in ai_stems_lower:
                    if not cb.is_checked():
                        cb.click(force=True)
                        time.sleep(0.3)
                        log(f"  {filename_raw}: AI生成チェックON")
                elif not filename_raw:
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


# PIXTA はアクセス集中時、通常ページの代わりに静的な混雑ページを返す（2026-08-01 実事故）。
# HTTP 200 で返るためエラーにならず、「a.upload-button が出ない」というセレクタ待ちの
# タイムアウトとしてしか現れないので、DOM変更と見分けがつかない。ここで明示的に検出する。
CONGESTION_TITLE = "Too many users are trying to access"
CONGESTION_TEXT = "アクセスが集中しております"


def _is_congestion_page(page) -> bool:
    try:
        if CONGESTION_TITLE in (page.title() or ""):
            return True
    except Exception:
        pass
    try:
        return page.locator(f"text={CONGESTION_TEXT}").count() > 0
    except Exception:
        return False


def _goto_upload_page(page, url: str, log, attempts: int = 4, wait_sec: int = 45):
    """アップロードページを開く。混雑ページを掴んだら間を置いて開き直す。

    同一実行内でも片方のページだけが混雑に当たることがある（2026-08-01: イラスト側が被弾）。
    リトライしきれなかった場合だけ例外にする。
    """
    for attempt in range(1, attempts + 1):
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        if "sign_in" in page.url or "login" in page.url:
            raise PermissionError("Session expired. Run pixta_login.py again.")

        if not _is_congestion_page(page):
            return

        if attempt < attempts:
            log(f"[!] PIXTA が混雑ページを返しました（{attempt}/{attempts}）。{wait_sec}秒待って開き直します...")
            time.sleep(wait_sec)

    raise RuntimeError(
        f"PIXTA が混雑ページ（アクセス集中）を返し続けています。{attempts}回試行しました。"
        "時間をおいて再実行してください。"
    )


# PIXTA の重複素材確認モーダル
DUP_MODAL_SEL = "#modal-user-confirm"


def _dismiss_duplicate_confirm_modal(page, log, answer: str = "no") -> bool:
    """「以下の2つの素材は同一素材ですか？」モーダルを閉じる。

    出たまま放置すると `.jqmOverlay` が以降のクリックを全部インターセプトし、
    タイトル/タグ入力も審査申請も 30 秒タイムアウトを繰り返して全滅する。
    2026-08-18 の実運用で 6 件中 3 件がタイトル・タグ空のまま残った。

    answer:
      "no"  … 「いいえ」= 別素材として登録（既定・誤検出時の安全側）
      "yes" … 「はい」= すでに販売中の素材を今回の 4K で上書きして販売（**不可逆**）

    戻り値: モーダルを閉じたら True / そもそも出ていなければ False
    """
    try:
        modal = page.locator(DUP_MODAL_SEL)
        if modal.count() == 0 or not modal.first.is_visible():
            return False
    except Exception:
        return False

    btn_cls = "btn-yes" if answer == "yes" else "btn-no"
    label = "はい（既存素材を上書き）" if answer == "yes" else "いいえ（別素材として登録）"
    try:
        log(f"[modal] 重複素材確認モーダルを検出 → 「{label}」を選択")
        page.locator(
            f"{DUP_MODAL_SEL} button.btn-confirm-footage.{btn_cls}"
        ).first.click(timeout=10000)
    except Exception as e:
        log(f"[modal] [!] ボタンのクリックに失敗: {e}")
        return False

    # オーバーレイが消えるまで待つ（消えないと後続のクリックが全部食われる）
    for _ in range(20):
        try:
            ov = page.locator(".jqmOverlay")
            if ov.count() == 0 or not ov.first.is_visible():
                log("[modal] オーバーレイの消失を確認")
                return True
        except Exception:
            return True
        time.sleep(0.5)
    log("[modal] [!] オーバーレイが消えない（後続がブロックされる可能性）")
    return False


def _act_with_modal_guard(page, log, action, desc: str, answer: str = "no", attempts: int = 2):
    """モーダルに食われたら閉じてから 1 回だけやり直す実行ラッパー。"""
    last_err = None
    for i in range(attempts):
        _dismiss_duplicate_confirm_modal(page, log, answer)
        try:
            action()
            return
        except Exception as e:
            last_err = e
            log(f"  [guard] {desc} 失敗 ({i + 1}/{attempts}): {str(e)[:120]}")
            # モーダルが原因でなければ再試行しても無駄なので抜ける
            if not _dismiss_duplicate_confirm_modal(page, log, answer):
                break
    raise last_err


_ERROR_TEXT_SELECTORS = [
    "#item_submit_form .error",
    "#item_submit_form .errors",
    ".error-message",
    ".alert",
    "p.error",
    "span.error",
]


def _page_error_texts(page) -> list:
    """ページ上のバリデーションエラー文言を集める（イラスト側 pixta_portal と同じ役割）。"""
    found = []
    for sel in _ERROR_TEXT_SELECTORS:
        try:
            for text in page.locator(sel).all_inner_texts():
                text = " ".join(text.split())
                if text and text not in found:
                    found.append(text)
        except Exception:
            pass
    return found[:10]


def _click_save_input(page, log, answer: str = "no") -> bool:
    """「入力を保存」でタイトル・タグをサーバーへ確定させる。

    自動保存だけに頼るとタグが乗らないことがある（2026-09-09 事故）。

    このリンクは「選択した作品を登録」と同じ操作バーに入っている**ページ単位**の要素で、
    一覧の全作品をまとめて保存する。作品ごとには存在しない（実 DOM で 1 個と確認済み）。
    """
    try:
        found = page.locator("a.save_input").count()
        if found != 1:
            log(f"[!] 「入力を保存」が {found} 個見つかりました（想定は 1 個・ページ単位の要素）")
        btn = page.locator("a.save_input").first
        btn.wait_for(state="visible", timeout=10000)
        # 重複素材モーダルが立っているとクリックがオーバーレイに食われる
        _act_with_modal_guard(page, log, btn.click, "「入力を保存」", answer=answer)
        log("「入力を保存」をクリックしました")
        time.sleep(6)
        return True
    except Exception as e:
        log(f"[!] 「入力を保存」を押せませんでした: {e}")
        return False


def _read_item_state(page, item_id: str):
    """リロード後の画面から、その作品のタイトル文字列と反映済みタグ数を読む。

    読めなかったときは (None, -1) を返し、「空欄」と「読めなかった」を区別する。
    """
    try:
        title = page.locator(f'[id="{item_id}-title"]').input_value(timeout=5000).strip()
    except Exception:
        title = None
    try:
        tags_n = int(
            page.locator(f'[id="{item_id}-tags-count"]').inner_text(timeout=5000).strip() or "0"
        )
    except Exception:
        tags_n = -1
    return title, tags_n


def run_footage_upload(
    files: list,
    metadata: list,
    progress_callback=None,
    skip_submit: bool = False,
    is_ai: bool = False,
    ai_filenames: set = None,
    no_wait: bool = False,
    playwright_instance=None,
    dup_modal_answer: str = "no",
    skip_upload: bool = False,
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
    _keep_open = skip_submit

    _own_playwright = playwright_instance is None
    p = sync_playwright().start() if _own_playwright else playwright_instance
    try:
        browser, context = _launch(p)
        page = context.new_page()

        try:
            # -------------------------------------------------------
            # Phase 1: ファイルアップロード
            # -------------------------------------------------------
            log("Opening Pixta footage upload page...")
            _goto_upload_page(page, UPLOAD_URL, log)
            log(f"Page loaded: {page.url}")

            if skip_upload:
                # ファイルは既にペンディング一覧に載っている前提で Phase 1 を飛ばす。
                # 用途: タイトル/タグ入力や審査申請だけが失敗した回の復旧。
                # これが無いと復旧のたびに同じ動画を再アップロードして重複が増える
                # （2026-08-18 に必要になった）。
                log("[skip_upload] Phase 1 をスキップ（既存のペンディング一覧に対して処理する）")
                uploaded = 0
            else:
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
            import base64 as _base64
            _is_mac = _platform.system() == "Darwin"

            # metadataをファイル名（stem, 小文字）→メタ のdictに変換
            # ※ Pixtaはファイル名を小文字化するため、照合も小文字で行う
            meta_by_stem = {}
            for vf, m in zip(files, metadata):
                stem = Path(vf).stem.lower()
                meta_by_stem[stem] = m

            # 重複素材確認モーダルが開いていると以降のクリックが全部食われるので先に閉じる
            _dismiss_duplicate_confirm_modal(page, log, dup_modal_answer)

            # ページ上のアイテムIDを取得してファイル名と照合
            item_ids = page.locator("input.submit_items").evaluate_all(
                "els => els.map(el => el.id)"
            )
            log(f"Found {len(item_ids)} item(s) on page")

            # item_id -> (stem, title, tags)。保存検証と再投入で使い回す
            item_meta = {}

            def _apply_metadata(item_id, stem, title_text, tags):
                """1 アイテムにタイトルとタグを入力する（サーバー保存はしない）"""
                # タイトル入力
                if title_text:
                    try:
                        def _set_title():
                            title_inp = page.locator(f'[id="{item_id}-title"]')
                            title_inp.scroll_into_view_if_needed()
                            title_inp.click(click_count=3)
                            title_inp.fill(title_text)

                        _act_with_modal_guard(
                            page, log, _set_title,
                            f"[{item_id}] タイトル入力", answer=dup_modal_answer,
                        )
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
                            # タグ文字列は AI 生成のためコマンドラインに直接埋め込まない
                            # (Base64 経由で渡してインジェクションと文字化けを防ぐ)
                            _b64 = _base64.b64encode(tag_str.encode("utf-8")).decode("ascii")
                            _subprocess.run(
                                [
                                    "powershell", "-NoProfile", "-Command",
                                    "Set-Clipboard -Value ([Text.Encoding]::UTF8.GetString("
                                    f"[Convert]::FromBase64String('{_b64}')))",
                                ],
                                check=True,
                            )
                        def _paste_tags():
                            tag_inp = page.locator(f'[id="{item_id}-input-tags"]')
                            tag_inp.scroll_into_view_if_needed()
                            tag_inp.click()
                            time.sleep(0.2)
                            tag_inp.press("Meta+v" if _is_mac else "Control+v")

                        def _applied_tag_count() -> int:
                            """実際に反映されたタグ数を DOM から読む。
                            ペーストが例外を出さずに通っても commit されないことがあるため、
                            ログの "Tags pasted" を成功根拠にしない（2026-08-18 実事故）。"""
                            try:
                                return int(
                                    page.locator(f'[id="{item_id}-tags-count"]')
                                    .inner_text(timeout=5000).strip() or "0"
                                )
                            except Exception:
                                return -1

                        applied = 0
                        for attempt in range(1, 4):
                            _act_with_modal_guard(
                                page, log, _paste_tags,
                                f"[{item_id}] タグ貼り付け", answer=dup_modal_answer,
                            )
                            time.sleep(0.8)
                            applied = _applied_tag_count()
                            if applied > 0:
                                break
                            log(f"[{item_id}] [!] タグが 0 のまま。再試行 {attempt}/3")
                            time.sleep(1.0)

                        if applied > 0:
                            log(f"[{item_id}] タグ反映を確認: {applied} 個")
                        else:
                            log(f"[{item_id}] [!] タグが 0 のまま反映されていない（3回試行）")
                            errors.append(f"Tags not applied for {stem}")
                    except Exception as e:
                        log(f"[{item_id}] [!] Failed to paste tags: {e}")
                        errors.append(f"Tag paste failed for {stem}: {e}")

            for item_id in item_ids:
                # ファイル名を取得（例: "1378581/footage / 260401_starburst_yellow.mp4"）
                try:
                    filename_raw = page.locator(f'[id="{item_id}-filename"]').inner_text(timeout=3000).strip()
                except Exception:
                    log(f"[{item_id}] [!] Could not read filename, skipping")
                    continue

                # stemを抽出（パス区切り後の最後の部分から拡張子除去、小文字化）
                filename_part = filename_raw.split("/")[-1].strip()
                stem = Path(filename_part).stem.lower()

                meta = meta_by_stem.get(stem)
                if not meta:
                    log(f"[{item_id}] [!] No metadata for '{stem}', skipping")
                    continue

                title_text = meta.get("title", "")[:50]
                tags = meta.get("tags", [])[:50]
                log(f"[{item_id}] '{stem}': title='{title_text}', tags={len(tags)}")

                item_meta[item_id] = (stem, title_text, tags)
                _apply_metadata(item_id, stem, title_text, tags)

            # -------------------------------------------------------
            # Phase 2.5: 「入力を保存」で確定させ、リロードして保存されたか確かめる
            #
            # 2026-09-09 事故: タイトルは自動保存でサーバーに乗ったが、クリップボードで
            # 入れたタグは画面のカウンタが 26 と出ただけでサーバーには保存されておらず、
            # 登録が confirm へ遷移せずに落ちた。**画面のカウンタは保存された証拠にならない。**
            # 必ず保存 → リロード → 読み直しで確かめる。
            # -------------------------------------------------------
            # テストモード（skip_submit）では動かさない。保存はサーバーへの実書き込みで、
            # かつリロードでマスターが見ている画面を流してしまうため。
            if item_meta and not skip_submit:
                for repair in range(2):
                    if not _click_save_input(page, log, dup_modal_answer):
                        log("[!] 保存ボタンを押せませんでした。リロード後の読み直しで判定します。")
                    # item_id は素材固有の永続 ID で、リロードしても振り直されない
                    # （2026-09-09 に別セッションからの復旧で同一 ID が引けることを実測）
                    _goto_upload_page(page, UPLOAD_URL, log)
                    _dismiss_duplicate_confirm_modal(page, log, dup_modal_answer)

                    unsaved = []
                    for item_id, (stem, title_text, tags) in item_meta.items():
                        got_title, got_tags = _read_item_state(page, item_id)
                        ok_title = (not title_text) or got_title == title_text
                        ok_tags = (not tags) or got_tags == len(tags)
                        if ok_title:
                            title_state = "OK"
                        elif got_title is None:
                            title_state = "読めず"
                        else:
                            title_state = "NG"
                        log(
                            f"[{item_id}] 保存確認: title={title_state} "
                            f"tags={got_tags}/{len(tags)}"
                        )
                        if not (ok_title and ok_tags):
                            unsaved.append((item_id, stem, title_text, tags))

                    if not unsaved:
                        log("[OK] タイトル・タグがサーバーに保存されていることを確認しました")
                        break

                    if repair == 0:
                        log(f"[!] 未保存 {len(unsaved)} 件。入力し直します。")
                        for item_id, stem, title_text, tags in unsaved:
                            _apply_metadata(item_id, stem, title_text, tags)
                    else:
                        detail = ", ".join(f"{stem}({item_id})" for item_id, stem, _, _ in unsaved)
                        raise RuntimeError(
                            "タイトル/タグがサーバーに保存されませんでした（登録しても弾かれます）: "
                            + detail
                        )

            # -------------------------------------------------------
            # Phase 3: 審査申請
            # -------------------------------------------------------
            # AI生成チェックボックスをON
            if is_ai or ai_filenames:
                _check_ai_generated_on_list(page, log, ai_filenames=ai_filenames)

            if skip_submit:
                log("[テストモード] 審査申請をスキップしました。ブラウザを閉じると次の処理に進みます。")
            else:
                # 全選択・登録クリックもオーバーレイに食われるので直前に閉じておく
                _dismiss_duplicate_confirm_modal(page, log, dup_modal_answer)
                log("Selecting all items for submission...")
                all_cb = page.locator("#item_submit_form #all-1")
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
                    # 落ちた理由をページから拾って添える（イラスト側 _raise_no_progress と同じ趣旨）。
                    # URL だけ出しても原因が分からず、毎回スクショを見る羽目になっていた。
                    reasons = _page_error_texts(page)
                    if reasons:
                        detail = "ページ上のエラー表示: " + " / ".join(reasons)
                    else:
                        states = []
                        for item_id in (item_meta or {}):
                            got_title, got_tags = _read_item_state(page, item_id)
                            if got_title is None:
                                title_state = "読めず"
                            else:
                                title_state = "有" if got_title else "空"
                            states.append(f"{item_id}: title={title_state} tags={got_tags}")
                        detail = (
                            "ページ上にエラー表示は見つかりませんでした。各作品の入力状態: "
                            + (" / ".join(states) if states else "取得できず")
                        )
                    raise RuntimeError(
                        f"Expected confirm page but got: {page.url}。{detail}"
                    )

                log("Waiting for confirm page to fully load...")
                page.wait_for_load_state("networkidle", timeout=30000)
                time.sleep(2)

                log("Clicking submit-for-review button on confirm page...")
                confirm_btn = page.locator("input[type='submit'][value='審査申請']")
                confirm_btn.scroll_into_view_if_needed(timeout=5000)
                confirm_btn.wait_for(state="visible", timeout=30000)
                confirm_btn.click()
                log("Clicked 審査申請 button")

                # AI生成確認モーダルの処理
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
                    log(f"[?] Unexpected final URL: {page.url}")
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
