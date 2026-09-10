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


# CSV を当ててから画面に反映されるまで待つ上限と、その間の再確認間隔。
# 反映されれば即抜けるので、速い日に待たされることはない。効かない日は何分待っても
# 入らない（Shutterstock 側が無音で無視している）ので、打ち切って戻り値で上へ伝える。
CSV_REFLECT_TIMEOUT_S = 120
CSV_REFLECT_POLL_S = 20


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


def _wait_not_submitted_cleared(page, log, label, timeout=90, reload_every=15):
    """「未送信」タブのカウンタが (0) になるまで reload しながら待つ。

    ★このカウンタは写真と動画を**合算した「未送信」全体の残数**であり、いま開いている
      種別だけの残数ではない。→ **写真・動画の提出が両方終わってから 1 回だけ**呼ぶこと。
      写真の提出直後に呼ぶと、まだ提出していない動画の分を
      「提出できなかった写真」として誤検知する。
      根拠: アップロード確認が `current >= expected`（expected = 写真+動画の合計）で
      成立している。種別ごとのカウンタならこの判定は成立しない。

    提出直後のカウンタは、ポータル側が数字を取り直して描き直すまで**提出前の値**を
    表示したままになる。提出ボタンを押して 4 秒待って 1 回読むだけだと、実際は全件提出
    できているのに「未提出が残っています」と誤って警告していた。
    アップロード確認と同じ reload ポーリング方式に揃える。

    カウンタが数字として一度も読めなかった場合（描画前でラベルしか無い等）は
    "unknown" を返し、「本当に残っている（remains）」と区別する。

    戻り値: ("cleared" | "remains" | "unknown", 最後に読めたタブ文字列)
    """
    deadline = time.time() + timeout
    next_reload = time.time() + reload_every
    last_text = ""
    last_count = None

    while True:
        try:
            text = page.locator('[data-testid="tab-not_submitted"]').inner_text(timeout=5000).strip()
            last_text = text
            m = re.search(r'\((\d+)\)', text)
            if m:
                last_count = int(m.group(1))
                if last_count == 0:
                    log(f"[OK] {label}: 全件提出完了")
                    return "cleared", text
        except Exception:
            pass

        if time.time() >= deadline:
            break
        time.sleep(2)

        if time.time() >= next_reload:
            # 提出後なので reload してよい（送信ボタンはもう使わないため選択解除は無害）
            try:
                page.reload(wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)
                _close_popups(page)
            except Exception:
                pass
            next_reload = time.time() + reload_every

    if last_count is not None:
        log(f"[!] {label}: {timeout}秒待っても未提出が残っています: {last_text}")
        return "remains", last_text

    log(f"[!] {label}: 未送信タブのカウンタを読めませんでした（提出の成否は未確認）")
    return "unknown", last_text


def _csv_english_keywords(csv_path: Path) -> set:
    """CSV に書かれた英語キーワードの集合（小文字）を返す。読めなければ空集合。"""
    import csv as _csv
    kws = set()
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            for row in _csv.DictReader(f):
                for k in (row.get("Keywords") or "").split(","):
                    k = k.strip().lower()
                    if k:
                        kws.add(k)
    except Exception:
        pass
    return kws


def _verify_csv_applied(page, csv_path: Path, log, label: str = "") -> bool:
    """CSV が本当に適用されたかを、表示中の keyword が CSV の英語語彙と一致するかで判定する。

    keyword チップの「個数」だけを見る判定では不十分。静止画には Pixta 用の**日本語**
    IPTC/XMP が埋め込まれており、Shutterstock はアップロード時にそれを読む。そのため
    CSV が一度も適用されていなくてもチップは付いてしまい、個数チェックは [OK] を返す。
    実際に「keyword 76件」と出しながら CSV は全列未適用で、カテゴリーが空のまま
    素材が未送信タブに滞留したことがある。
    CSV は英語・埋め込みは日本語なので、語彙が重なるかどうかで切り分けられる。

    カテゴリーは CSV 以外から入る経路が無いので、「CSV が適用されたか」を
    正しく判定できれば、カテゴリー欠落も同時に検知できる。
    """
    prefix = f"{label}: " if label else ""
    chip_loc = page.locator('[data-testid^="selected-keyword-"]')
    try:
        chips = chip_loc.count()
    except Exception:
        chips = 0

    if chips == 0:
        log(f"[!] {prefix}keyword未検出。CSV が適用されていない可能性が高い")
        return False

    expected = _csv_english_keywords(csv_path)
    if not expected:
        log(f"[OK] {prefix}CSVメタデータ反映確認（keyword {chips}件 / CSV語彙を読めず照合スキップ）")
        return True

    shown = set()
    for i in range(min(chips, 60)):
        try:
            t = chip_loc.nth(i).inner_text().strip().lower()
            if t:
                shown.add(t)
        except Exception:
            pass

    hit = len(shown & expected)
    if hit == 0:
        log(f"[!!] {prefix}CSV未適用の疑いが濃厚です。表示中の keyword {chips}件が CSV の英語 keyword と 1 つも一致しません")
        log("     ファイル埋め込みの日本語メタデータが見えているだけで、CSV は効いていない可能性が高い")
        log("     → CSV のヘッダー書式（引用符が付くと全列無視される）と Filename 列の一致を確認すること")
        log("     → この状態ではカテゴリーが空のままなので提出できません")
        return False

    log(f"[OK] {prefix}CSVメタデータ反映確認（keyword {chips}件 / CSV語彙と {hit}件一致）")
    return True


def _wait_csv_reflected(page, csv_path: Path, log, label: str = "",
                        timeout_s: int = CSV_REFLECT_TIMEOUT_S) -> bool:
    """CSV が画面に反映されるまで、ページを更新しながら待つ。

    Shutterstock 側は CSV を受け取ってから実際に各素材へ反映するまでに多少のラグがある。
    その間は説明・カテゴリー・キーワードがすべて空のまま見える（CSV が壊れているわけでは
    ない）。従来の「固定 4+5 秒 → reload → 1 回確認」では届かず、毎回 keyword未検出 →
    カテゴリー空のまま提出 →「未送信」に滞留していた。

    ただし待ち上限は短くてよい（2026-09-03 に 10 分 → 2 分）。効く日は 1 回目の確認で
    通り、効かない日は何分待っても永久に入らない（SS 側が CSV を無音で無視している）。
    長く待っても判定は変わらないので、打ち切って False を返し先へ進む。**呼び出し元は
    この False を握り潰さないこと**（ファイル移動を止める判断材料になる）。

    反映を検知したら即座に True を返すので、速い日は待たされない。
    """
    prefix = f"{label}: " if label else ""
    deadline = time.time() + timeout_s
    attempt = 0
    while True:
        attempt += 1
        _close_popups(page)
        _select_all(page, log)
        time.sleep(2)
        if _verify_csv_applied(page, csv_path, log, label):
            if attempt > 1:
                log(f"[OK] {prefix}CSV 反映を確認（{attempt} 回目 / 約 {int(time.time() - (deadline - timeout_s))} 秒）")
            return True
        if time.time() >= deadline:
            log(f"[!] {prefix}CSV 反映を {timeout_s} 秒待っても確認できませんでした。"
                f"待つのを打ち切って次へ進みます（「未送信」に残る可能性があります）")
            return False
        remain = int(deadline - time.time())
        log(f"[..] {prefix}CSV 反映待ち（{attempt} 回目 / 残り約 {remain} 秒）"
            f"{CSV_REFLECT_POLL_S} 秒後に更新して再確認します")
        time.sleep(CSV_REFLECT_POLL_S)
        try:
            page.reload(wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log(f"[!] {prefix}更新に失敗（続行）: {e}")
        time.sleep(3)


def _apply_csv_on_tab(page, csv_path: Path, log, label: str):
    """今開いているタブで CSV メタデータを適用する。

    戻り値は `(ok, reflected)` の 2 要素タプル。
      ok        … CSV をセットする一連の操作が最後まで通ったか（True なら全選択まで済んでいる）
      reflected … その CSV が実際に画面へ反映されたことを確認できたか

    **reflected=False を握り潰さないこと。** 2026-08-19〜08-22 の 4 日間、CSV が一度も
    適用されないまま「警告をログに出して続行」し、空メタデータで提出 →「未送信」に滞留 →
    それでもファイル移動と CSV のゴミ箱送りが走り、マスターが毎日ゴミ箱から CSV を
    拾って手作業でメタデータを入れ直す羽目になった。呼び出し元は reflected を
    run_portal_automation の戻り値 `csv_unapplied` に積み、app.py 側でファイル移動を
    止める判断材料にする。

    CSV 適用は元々 写真タブ (STEP 3) でしか行っていなかったが、「CSVをアップロード」ボタンは
    そのタブに素材が 1 つも無いと画面に存在しない。静止画が無い日（fx 動画だけの日）は
    CSV が一度も当たらず、動画が説明もキーワードも空のまま「未送信」に滞留していた
    （2026-08-01 判明: 魔法陣動画 6 本が 1 週間分積み上がっていた）。

    失敗しても例外にしない（ここで落とすとブラウザの後始末まで巻き添えになる）。
    代わりに戻り値で必ず上へ伝える。
    """
    try:
        csv_btn = page.locator('button[data-testid="csv-upload"]')
        csv_btn.wait_for(state="visible", timeout=8000)
    except PWTimeout:
        log(f"[!] {label}: CSVアップロードボタンが見つかりません。CSV適用をスキップします")
        return False, False

    try:
        log(f"{label}: CSV適用中: {csv_path.name}")
        csv_btn.click()
        time.sleep(1)

        dialog = page.locator('[role="dialog"]')
        dialog.wait_for(state="visible", timeout=8000)

        with page.expect_file_chooser(timeout=10000) as fc_info:
            dialog.get_by_role("button", name=re.compile("アップロード", re.I)).click()
        fc_info.value.set_files(str(csv_path))
        log(f"{label}: CSV セット完了: {csv_path.name}")
        time.sleep(4)

        for _ in range(10):
            if not dialog.is_visible():
                break
            time.sleep(1)

        page.reload(wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        # 反映を更新しながら待つ（上限 CSV_REFLECT_TIMEOUT_S。超えたら諦めて先へ進む）。
        # 全選択は _wait_csv_reflected の中で毎回やり直す
        # （reload は選択を解除し、送信ボタンを消すため）。
        reflected = _wait_csv_reflected(page, csv_path, log, label)
        return True, reflected
    except Exception as e:
        log(f"[!] {label}: CSV適用に失敗しました: {e}")
        return False, False


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
    # 提出ボタンを押しても「未送信」タブに残ってしまった素材
    # （カテゴリー未設定など提出条件を満たしていないと、押しても提出されない）
    unsubmitted = []
    # カウンタを数値として読めず提出の成否を確認できなかったケース。
    # 「読めなかった」を unsubmitted に混ぜると誤検知になるので分ける。
    unverified = []
    # CSV を当てたのに画面へ反映されなかったタブのラベル（"画像" / "動画"）。
    # 空でなければ、その素材は説明・キーワード・カテゴリーが入っていない。
    # app.py はこれを見てファイル移動を止める。
    csv_unapplied = []

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

            # CSVメタデータ反映を待つ（上限 CSV_REFLECT_TIMEOUT_S。超えたら諦めて先へ進む）。
            # 全選択は _wait_csv_reflected が毎回やり直す
            # （reload は選択を解除し、送信ボタンを消すため）。
            if _wait_csv_reflected(page, csv_path, log):
                log("CSV適用完了")
            else:
                # 反映を確認できないまま提出しても、必須項目が空なので「未送信」に残るだけ。
                # ここで握り潰すと後段のファイル移動まで走ってしまうので、必ず戻り値に載せる。
                log("[NG] 画像: CSV が適用されていません。ファイル移動を止めます")
                csv_unapplied.append("画像")

            # ============================================================
            # STEP 4: 画像を審査提出
            # ============================================================
            if skip_submit:
                # 送信ボタンは素材が選択されている時のみ表示されるため、
                # 手動操作できるよう選択済みの状態で停止する
                log("\n>> [テストモード] 全ファイルを選択して停止します...")
                _ensure_all_selected(page, log)
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

                # ★ここでは成否を判定しない。
                #   「未送信」カウンタは写真+動画を合算した全体の残数なので、
                #   この時点ではまだ提出していない動画の分が必ず残って見える。
                #   （写真提出直後「未送信 (1)」→ 動画タブでも同じ「未送信 (1)」→
                #     動画提出後に (0)。写真は最初から全件提出できていた）
                #   → 判定は写真・動画の提出が両方終わった STEP 5 で 1 回だけ行う。
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
                        # 合算カウンタが 0 = 写真も動画も残っていない。
                        # ここが写真提出の成否確認も兼ねる（種別ごとの数字は取れないため）。
                        log("[OK] 未送信タブ: 全件提出完了（写真・動画とも残りなし）")
                    else:
                        log("動画: 全選択して提出...")
                        try:
                            page.locator('input[type="checkbox"]').first.wait_for(state="visible", timeout=15000)
                        except PWTimeout:
                            log("[!] 動画チェックボックスが見つかりません")

                        # 動画タブでも CSV を当てる（2026-08-01 追加）。
                        # 写真タブでの適用は、その日に静止画が無いと「CSVをアップロード」ボタン自体が
                        # 存在せず実行できない。動画だけの日に動画のメタデータが空のまま残るのを防ぐ。
                        # 成功時は全選択まで済むので、失敗したときだけ従来どおり全選択する。
                        _video_ok, _video_reflected = _apply_csv_on_tab(page, csv_path, log, "動画")
                        if not _video_ok:
                            _select_all(page, log)
                        if not _video_reflected:
                            log("[NG] 動画: CSV が適用されていません。ファイル移動を止めます")
                            csv_unapplied.append("動画")

                        submit_btn = page.locator('[data-testid="edit-dialog-submit-button"]')
                        try:
                            submit_btn.wait_for(state="visible", timeout=8000)
                            submit_btn.click()
                            time.sleep(4)
                            log("[OK] 動画: 提出ボタンクリック完了")
                        except PWTimeout:
                            log("[NG] 動画: 提出ボタンが見つかりません")

                        # ★写真・動画の提出が両方終わったのでここで 1 回だけ判定する。
                        #   カウンタは合算なので、残っていても種別は特定できない。
                        #   ラベルは「未送信」にして、写真のせいだと決めつけない。
                        state, tab_text_after = _wait_not_submitted_cleared(page, log, "未送信")
                        if state == "remains":
                            unsubmitted.append(("未送信", tab_text_after))
                        elif state == "unknown":
                            unverified.append(("未送信", tab_text_after or "カウンタ読み取り不能"))

                except PWTimeout:
                    log("[!] 動画: not_submittedタブの確認ができませんでした")
                    # 写真側の判定もここに集約したので、読めなかった = 未検証として残す
                    unverified.append(("未送信", "カウンタ読み取り不能"))

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

    return {
        "submitted": submitted,
        "errors": errors,
        "unsubmitted": unsubmitted,
        "unverified": unverified,
        "csv_unapplied": csv_unapplied,
    }


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
