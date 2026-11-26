#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shutterstock コントリビューターポータル 自動化
- shutterstock_session.json のセッションを使用（ログイン不要）
- 画像アップロード → CSV適用 → 提出
- 動画タブ確認 → 提出
"""

import csv
import re
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from paths import SHUTTERSTOCK_SESSION as SESSION_FILE
PHOTO_URL = "https://submit.shutterstock.com/ja/portfolio/not_submitted/photo"
VIDEO_URL = "https://submit.shutterstock.com/ja/portfolio/not_submitted/video"
INGESTED_URL = "https://submit.shutterstock.com/api/next/media/list"
UPLOAD_POLL_SECONDS = 15
UPLOAD_STALL_SECONDS = 420

# CSV反映の待ち上限。行の拒否は処理結果モーダルを読んで即時エラーにし、
# 処理結果がエラーでない場合だけキーワード反映を最大120秒確認する。
# 2026-11-02: 13行中10行が動画非対応のAbstractで拒否されていた。
# 取り込み失敗を一律に「サイトが無音で無視」と診断しない。
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


# 提出の本番の通信（画面は何も出さないので、成否はこの応答でしか分からない）
_SUBMIT_API = "/api/next/media/submit"


def _is_limit_error(code: str) -> bool:
    return "MAX_SUBMISSION_COUNT_EXCEEDED" in (code or "")


def _click_submit(page, log, label: str, timeout_s: int = 20):
    """送信ボタンを押し、本番の提出通信（preview=false）の応答を読んで返す。

    Shutterstock の提出上限（7日で動画100本・画像500点）の残り枠より多い束を送ると、
    batchError ERROR_VIDEO_MAX_SUBMISSION_COUNT_EXCEEDED で束ごと拒否され、1本も
    提出されない。画面には何も出ない（2026-11-19 確認）。1本ずつなら残り枠ぶんは通る。

    戻り値: {"clicked": bool, "seen": bool, "code": str, "message": str,
             "ok_count": int, "item_errors": list}
    """
    result = {"clicked": False, "seen": False, "code": "", "message": "",
              "ok_count": 0, "item_errors": []}
    finals = []

    def _on_response(r):
        try:
            if _SUBMIT_API not in r.url or r.request.method != "POST":
                return
            body = r.request.post_data or ""
            if '"preview":"true"' in body.replace(" ", ""):
                return
            finals.append(r.json())
        except Exception:
            pass

    page.on("response", _on_response)
    try:
        btn = page.locator('[data-testid="edit-dialog-submit-button"]')
        try:
            btn.wait_for(state="visible", timeout=8000)
        except PWTimeout:
            log(f"[NG] {label}: 提出ボタンが見つかりません")
            return result
        btn.click()
        result["clicked"] = True
        deadline = time.time() + timeout_s
        while not finals and time.time() < deadline:
            page.wait_for_timeout(500)
        time.sleep(2)
    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass

    if finals:
        data = finals[-1] or {}
        be = data.get("batchError") or {}
        result.update(seen=True,
                      code=be.get("code") or "",
                      message=be.get("message") or "",
                      ok_count=len(data.get("success") or []),
                      item_errors=data.get("itemErrors") or [])
    return result


def _deselect_all(page):
    for cb in page.locator('input[type="checkbox"]').all():
        try:
            if cb.is_checked():
                cb.click(force=True)
                time.sleep(0.1)
        except Exception:
            pass


def _submit_one_by_one(page, log, label: str):
    """上限で束ごと拒否されたとき、1本ずつ送って残り枠ぶんだけ提出する。

    上限に当たった時点で止める。戻り値: (提出できた数, 上限で残した数, 上限のメッセージ)
    """
    sent = 0
    limit_msg = ""
    while True:
        try:
            page.reload(wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        time.sleep(4)
        _close_popups(page)
        cbs = page.locator('input[type="checkbox"]')
        try:
            cbs.first.wait_for(state="visible", timeout=15000)
        except PWTimeout:
            return sent, 0, limit_msg  # 残りなし
        total = cbs.count()
        if total == 0:
            return sent, 0, limit_msg
        _deselect_all(page)
        try:
            cbs.first.click(force=True)
        except Exception as e:
            log(f"[!] {label}: 1本目の選択に失敗しました: {e}")
            return sent, total, limit_msg
        time.sleep(2)
        r = _click_submit(page, log, label)
        if _is_limit_error(r["code"]):
            limit_msg = r["message"]
            log(f"[!] {label}: Shutterstock の提出上限（直近7日で動画100本・画像500点）に達しました。"
                f"{sent}本を提出し、残り{total}本は未送信に残します")
            return sent, total, limit_msg
        if not r["seen"] or r["code"] or r["item_errors"]:
            log(f"[!] {label}: 1本ずつの提出で想定外の応答（{r['code']} {r['message']} "
                f"{r['item_errors']}）。ここで止めます")
            return sent, total, limit_msg
        sent += 1


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


# 動画タブで当てる CSV に入れる拡張子（Shutterstock が動画として受ける形式）
_VIDEO_EXTS = {".mp4", ".mov", ".avi"}


def _read_csv_rows(csv_path):
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def _row_filename(row):
    return (row.get("Filename") or "").strip()


def _csv_for_pending(csv_path, folder, fields, rows, pending_names):
    """現在の未送信タブに実在する素材の行だけを一時CSVにまとめる。"""
    selected = [row for row in rows if _row_filename(row) in pending_names]
    if not selected:
        return None
    path = Path(folder) / csv_path.name
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selected)
    return path


class CSVImportError(RuntimeError):
    """Shutterstock がCSVの行を拒否した。待機や提出では解消しない。"""


def _check_csv_import_result(page, log, upload_name):
    header = page.locator('[data-testid="CSV error modal header"]')
    if not header.count() or not header.first.is_visible():
        return
    dialog = header.first.locator('xpath=ancestor::*[@role="dialog"][1]')
    if not dialog.get_by_text(upload_name, exact=True).count():
        log("Shutterstock: 前回のCSV処理通知を閉じます")
        dialog.locator('[data-testid="lower-close-button"]').click()
        return
    details = dialog.inner_text()
    row_errors = dialog.locator('[data-testid="row error"]').all_inner_texts()
    error_count = re.search(r'(?:アップロードエラー|Upload errors?)\s*:\s*(\d+)', details, re.I)
    summary = re.search(r'(\d+)行中(\d+)行', details)
    # 同じCSV名を再投入すると、全行成功・エラー0でも前回の行明細が残る。
    # 全行成功を明示した最新の集計を優先し、古い明細で成功を覆さない。
    all_succeeded = bool(error_count and int(error_count.group(1)) == 0 and summary
                         and int(summary.group(1)) == int(summary.group(2)))
    if ((error_count and int(error_count.group(1)) > 0)
            or (row_errors and not all_succeeded)
            or (summary and int(summary.group(1)) != int(summary.group(2)))):
        raise CSVImportError("Shutterstock CSV取り込みエラー:\n" + details)
    log("[OK] CSV処理結果: " + " / ".join(details.splitlines()[:7]))
    dialog.locator('[data-testid="lower-close-button"]').click()


def _wait_csv_reflected(page, csv_path: Path, upload_name, log, label: str = "",
                        timeout_s: int = CSV_REFLECT_TIMEOUT_S) -> bool:
    """CSV が画面に反映されるまで、ページを更新しながら待つ。

    Shutterstock 側は CSV を受け取ってから実際に各素材へ反映するまでに多少のラグがある。
    その間は説明・カテゴリー・キーワードがすべて空のまま見える（CSV が壊れているわけでは
    ない）。従来の「固定 4+5 秒 → reload → 1 回確認」では届かず、毎回 keyword未検出 →
    カテゴリー空のまま提出 →「未送信」に滞留していた。

    行ごとの取り込みエラーはモーダルの具体的な理由を例外で返す。
    エラーが出ていない場合も上限でFalseを返す。呼び出し元はFalseを握り潰さず、
    ファイル移動を止める判断材料にする。

    反映を検知したら即座に True を返すので、速い日は待たされない。
    """
    prefix = f"{label}: " if label else ""
    deadline = time.time() + timeout_s
    attempt = 0
    while True:
        attempt += 1
        _check_csv_import_result(page, log, upload_name)
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
        page.wait_for_timeout(CSV_REFLECT_POLL_S * 1000)
        _check_csv_import_result(page, log, upload_name)
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
        # 同じ名前の CSV を再送すると、ポータルが前回のエラー通知を出すことがある。
        # 元の CSV はそのまま使い、送信時のファイル名で今回の処理結果を区別する。
        upload_name = f"{csv_path.stem[:128]}_{uuid4().hex[:12]}.csv"
        log(f"{label}: CSV適用中: {upload_name}")
        csv_btn.click()
        time.sleep(1)

        choose_file = page.get_by_role("button", name=re.compile(r"^(アップロード|Upload)$", re.I))
        dialog = page.get_by_role("dialog").filter(has=choose_file)
        dialog.wait_for(state="visible", timeout=8000)

        with page.expect_file_chooser(timeout=10000) as fc_info:
            dialog.get_by_role("button", name=re.compile(r"^(アップロード|Upload)$", re.I)).click()
        fc_info.value.set_files({"name": upload_name, "mimeType": "text/csv", "buffer": csv_path.read_bytes()})
        log(f"{label}: CSV セット完了: {upload_name}")
        page.wait_for_timeout(4000)
        _check_csv_import_result(page, log, upload_name)

        for _ in range(10):
            _check_csv_import_result(page, log, upload_name)
            if not dialog.is_visible():
                break
            page.wait_for_timeout(1000)

        page.reload(wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        # 反映を更新しながら待つ（上限 CSV_REFLECT_TIMEOUT_S。超えたら諦めて先へ進む）。
        # 全選択は _wait_csv_reflected の中で毎回やり直す
        # （reload は選択を解除し、送信ボタンを消すため）。
        reflected = _wait_csv_reflected(page, csv_path, upload_name, log, label)
        return True, reflected
    except CSVImportError:
        raise
    except Exception as e:
        log(f"[!] {label}: CSV適用に失敗しました: {e}")
        return False, False


def _ingested_names(page, media_types):
    """未送信一覧と同じ API で、アップロードを中断せずにファイル名を照合する。"""
    names = set()
    for media_type in sorted(media_types):
        page_number = 1
        while True:
            response = page.request.get(INGESTED_URL, params={
                "mediaType": media_type, "workflowStatus": "ingested", "normalize": "true",
                "includeMediumThumb": "true", "order": "newest", "page": page_number, "perPage": 100,
            })
            if not response.ok:
                raise RuntimeError(f"Shutterstock: 未送信の{media_type}一覧を取得できません（HTTP {response.status}）")
            payload = response.json()
            items = payload.get("data")
            total = payload.get("totalCount")
            if (not isinstance(items, list) or not isinstance(total, int)
                    or (not items and total > (page_number - 1) * 100)):
                raise RuntimeError(f"Shutterstock: 未送信の{media_type}一覧の形式を確認できません")
            for item in items:
                filename = item.get("originalFilename") or item.get("uploadedFilename")
                if not filename:
                    raise RuntimeError(f"Shutterstock: 未送信の{media_type}に元ファイル名を確認できない素材があります")
                names.add(filename)
            if not items or page_number * 100 >= total:
                break
            page_number += 1
    return names


def _wait_for_ingestion(page, expected, media_types, already_present, log):
    """対象が全部そろってから次の画面へ進む。転送中のページは更新しない。"""
    seen = expected & already_present
    deadline = time.monotonic() + UPLOAD_STALL_SECONDS
    while seen != expected:
        page.wait_for_timeout(UPLOAD_POLL_SECONDS * 1000)
        current = expected & _ingested_names(page, media_types)
        if current - seen:
            deadline = time.monotonic() + UPLOAD_STALL_SECONDS
        if current != seen:
            log(f"  Shutterstock 未送信の反映: {len(current)}/{len(expected)}件")
        seen = current
        if seen != expected and time.monotonic() >= deadline:
            missing = ", ".join(sorted(expected - seen))
            raise RuntimeError(f"Shutterstock: 未送信の反映は {len(seen)}/{len(expected)}件。"
                               f"CSV 適用前に停止しました。未反映: {missing}")


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

    from stock_tagger import validate_shutterstock_csv
    validate_shutterstock_csv(csv_path)
    fields, rows = _read_csv_rows(csv_path)
    targets = {Path(file).name for file in (files or [])}
    missing_rows = targets - {_row_filename(row) for row in rows}
    if missing_rows:
        raise ValueError("Shutterstock: 対象素材の CSV 行がありません: " + ", ".join(sorted(missing_rows)))
    # 動画だけの実行では画像タブの提出を省く。同じタブの上限待ちの素材は従来どおり一緒に提出する。
    use_photo_tab = not files or any(Path(name).suffix.lower() not in _VIDEO_EXTS for name in targets)

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
    # 提出上限（7日で動画100本・画像500点）で残した素材 [(種類, 本数, ポータルのメッセージ)]。
    # メタデータは入っていて、上限が空けば次回の「全選択→送信」で一緒に出る（21日以内）。
    limited = []

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
            files_to_upload = []
            if files:
                media_types = {"photo" if Path(name).suffix.lower() not in _VIDEO_EXTS else "video"
                               for name in targets}
                existing = targets & _ingested_names(page, media_types)
                files_to_upload = [file for file in files if Path(file).name not in existing]
                if existing:
                    log(f"Shutterstock: 未送信にある {len(existing)}件を再利用します")

            if files_to_upload:
                log(f"アップロード開始: {len(files_to_upload)}件...")
                # アップロードボタンでドロップゾーンのモーダルを開く。
                # 2026-09-16 に data-testid が "uploadButton" から "desktop-upload-button" に変わった
                # （同じ日の 4 分違いの実行で新旧両方を確認＝切り替えの最中）。押すとどちらも同じ dropzone-container が開く。
                # どちらの名前でも見つかるようにし、無いときはボタンの文言（アップロード）でも探す。
                upload_btn = page.locator(
                    'button[data-testid="uploadButton"], button[data-testid="desktop-upload-button"]'
                ).first
                try:
                    upload_btn.wait_for(state="visible", timeout=15000)
                except PWTimeout:
                    upload_btn = page.get_by_role("button", name=re.compile(r"^(アップロード|Upload)$", re.I)).first
                    try:
                        upload_btn.wait_for(state="visible", timeout=5000)
                    except PWTimeout:
                        raise RuntimeError("アップロードボタンが見つかりません（data-testid=uploadButton / desktop-upload-button、"
                                           "文言「アップロード」のどれも表示されていません。ポータルのUIが変わった可能性があります）")
                upload_btn.click()

                # Shutterstock は native file chooser ではなく dropzone 方式に変更されたため、
                # dropzone 内の input[type=file] へ直接ファイルをセットする
                file_input = page.locator('[data-testid="dropzone-container"] input[type="file"]').first
                try:
                    file_input.wait_for(state="attached", timeout=10000)
                except PWTimeout:
                    file_input = page.locator('input[type="file"]').last  # フォールバック
                file_input.set_input_files([str(file) for file in files_to_upload])
                log(f"Files set: {[Path(file).name for file in files_to_upload]}")
                log("Shutterstock: アップロード画面を開いたまま未送信一覧への反映を確認します")
                _wait_for_ingestion(page, targets, media_types, existing, log)
                log(f"[OK] Shutterstock: 対象 {len(targets)}件の反映を確認")
                page.goto(PHOTO_URL, wait_until="domcontentloaded", timeout=30000)
                _close_popups(page)

            for media_type, label, url in (("photo", "画像", PHOTO_URL), ("video", "動画", VIDEO_URL)):
                if media_type == "photo" and not use_photo_tab:
                    continue
                names = _ingested_names(page, {media_type})
                if not names:
                    log(f"{label}: 未送信なし")
                    continue
                if page.url != url:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(4000)
                    _close_popups(page)

                with TemporaryDirectory(prefix="stock_tagger_ss_csv_") as folder:
                    tab_csv = _csv_for_pending(csv_path, folder, fields, rows, names)
                    if tab_csv is not None:
                        _, reflected = _apply_csv_on_tab(page, tab_csv, log, label)
                        if not reflected:
                            log(f"[NG] {label}: CSV が適用されていません。審査提出を止めます")
                            csv_unapplied.append(label)
                            continue

                # 上限で以前から残っている素材は、メタデータ設定済みなら次回に提出できる。
                _ensure_all_selected(page, log)
                if skip_submit:
                    log(f"[テストモード] {label}: 審査提出ボタンの手前で停止しました。")
                    continue

                result = _click_submit(page, log, label)
                if result["clicked"]:
                    log(f"[OK] {label}: 送信ボタンクリック完了")
                if _is_limit_error(result["code"]):
                    log(f"[!] {label}: Shutterstock の提出上限で一括提出できません（{result['message']}）")
                    sent, left, message = _submit_one_by_one(page, log, label)
                    submitted += sent
                    log(f"    1件ずつ提出 {sent}件 / 上限で残した {left}件")
                    if left:
                        limited.append((label, left, message or result["message"]))
                else:
                    submitted += result["ok_count"]

            if not skip_submit:
                state, detail = _wait_not_submitted_cleared(page, log, "未送信")
                if state == "remains" and limited:
                    log(f"[!] 未送信に残った分は提出上限によるものです: {detail}")
                elif state == "remains":
                    unsubmitted.append(("未送信", detail))
                elif state == "unknown":
                    unverified.append(("未送信", detail or "カウンタ読み取り不能"))

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
        "limited": limited,
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
