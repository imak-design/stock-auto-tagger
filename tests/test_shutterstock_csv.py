"""Offline regression checks; never import portal startup or access live accounts."""
import ast
import csv
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch


PROGRAM = Path(__file__).resolve().parents[1] / "program"


def load_definitions(filename, names, namespace):
    tree = ast.parse((PROGRAM / filename).read_text(encoding="utf-8-sig"))
    selected = []
    for node in tree.body:
        assigned = [t.id for t in node.targets if isinstance(t, ast.Name)] if isinstance(node, ast.Assign) else []
        if getattr(node, "name", None) in names or set(assigned) & set(names):
            selected.append(node)
    exec(compile(ast.Module(body=selected, type_ignores=[]), filename, "exec"), namespace)


class ShutterstockCsvTests(unittest.TestCase):
    def setUp(self):
        self.ns = {"Path": Path, "csv": csv}
        load_definitions("stock_tagger.py", {
            "SHUTTERSTOCK_VIDEO_CATEGORIES", "SHUTTERSTOCK_VIDEO_CATEGORY_OVERRIDES",
            "UPLOAD_VIDEO_EXTENSIONS", "ADOBE_TO_SHUTTERSTOCK",
            "_validate_shutterstock_video_categories", "validate_shutterstock_csv",
            "write_shutterstock_csv", "filter_keywords",
        }, self.ns)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "shutterstock_test.csv"

    def record(self, filename="snow.mp4", category2="自然"):
        return {"filename": filename, "file_type": "video", "shutterstock_category1": "背景・テクスチャ",
                "shutterstock_category2": category2, "shutterstock_title_en": "White snow falls across a blue background.",
                "shutterstock_keywords_en": "snow,winter,white,blue,background,falling,flakes"}

    def test_video_rejects_image_only_categories_but_vectors_allow_them(self):
        validate = self.ns["_validate_shutterstock_video_categories"]
        for category in ("Abstract", "Interiors", "Miscellaneous", "Parks/Outdoor", "Vintage", "Beauty/Fashion", "Celebrities"):
            with self.subTest(category=category), self.assertRaises(ValueError):
                validate("snow.mp4", "Backgrounds/Textures," + category)
            validate("drawing.eps", category)

    def test_invalid_batch_preserves_existing_csv_for_write_and_append(self):
        original = b"existing CSV must survive unchanged"
        for append in (False, True):
            self.path.write_bytes(original)
            with self.subTest(append=append), self.assertRaisesRegex(ValueError, "bad.mp4"):
                self.ns["write_shutterstock_csv"]([
                    self.record(), self.record("bad.mp4", "抽象")
                ], self.path, append=append)
            self.assertEqual(original, self.path.read_bytes())

    def test_valid_csv_roundtrip_and_existing_csv_validation(self):
        self.ns["write_shutterstock_csv"]([self.record()], self.path)
        self.ns["validate_shutterstock_csv"](self.path)
        with self.path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["Categories"], "Backgrounds/Textures,Nature")
        self.assertEqual(rows[0]["illustration"], "no")
        self.assertEqual(self.path.read_bytes().decode("utf-8-sig").splitlines()[0],
                         "Filename,Description,Keywords,Categories,Editorial,Mature content,illustration")
        raw = self.path.read_bytes().replace(b"Backgrounds/Textures,Nature", b"Backgrounds/Textures,Abstract")
        self.path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "snow.mp4"):
            self.ns["validate_shutterstock_csv"](self.path)

    def test_invalid_existing_csv_stops_before_browser_start(self):
        self.ns["write_shutterstock_csv"]([self.record()], self.path)
        self.path.write_bytes(self.path.read_bytes().replace(b"Nature", b"Abstract"))
        session = Path(self.temp.name) / "session-placeholder"
        session.touch()
        start_browser = Mock(side_effect=AssertionError("browser must not start"))
        ns = {"Path": Path, "SESSION_FILE": session, "sync_playwright": start_browser}
        load_definitions("shutterstock_portal.py", {"run_portal_automation"}, ns)
        fake_module = types.ModuleType("stock_tagger")
        fake_module.validate_shutterstock_csv = self.ns["validate_shutterstock_csv"]
        with patch.dict("sys.modules", {"stock_tagger": fake_module}), self.assertRaises(ValueError):
            ns["run_portal_automation"](self.path)
        start_browser.assert_not_called()


class CsvImportResultTests(unittest.TestCase):
    upload_name = "current_attempt.csv"

    def setUp(self):
        import re
        self.ns = {"re": re}
        load_definitions("shutterstock_portal.py", {"CSVImportError", "_check_csv_import_result"}, self.ns)

    def page(self, text, errors, matches_upload=True):
        dialog = Mock()
        dialog.inner_text.return_value = text
        dialog.get_by_text.return_value.count.return_value = int(matches_upload)
        rows, close = Mock(), Mock()
        rows.all_inner_texts.return_value = errors
        dialog.locator.side_effect = lambda selector: rows if selector == '[data-testid="row error"]' else close
        header = Mock()
        header.count.return_value = 1
        header.first.is_visible.return_value = True
        header.first.locator.return_value = dialog
        page = Mock()
        page.locator.return_value = header
        return page, close

    def test_partial_import_reports_filename_and_reason(self):
        page, close = self.page("13行中3行が正常に処理されました。\nアップロードエラー: 10\nsnow.mp4: 無効なカテゴリー", ["snow.mp4: 無効なカテゴリー"])
        with self.assertRaisesRegex(self.ns["CSVImportError"], "snow.mp4"):
            self.ns["_check_csv_import_result"](page, Mock(), self.upload_name)
        close.click.assert_not_called()

    def test_all_success_ignores_stale_error_details(self):
        page, close = self.page("13行中13行が正常に処理されました。\nアップロードエラー: 0", ["previous failure"])
        self.ns["_check_csv_import_result"](page, Mock(), self.upload_name)
        close.click.assert_called_once()

    def test_inconsistent_totals_do_not_pass_as_success(self):
        page, _ = self.page("13行中3行が正常に処理されました。\nアップロードエラー: 0", [])
        with self.assertRaises(self.ns["CSVImportError"]):
            self.ns["_check_csv_import_result"](page, Mock(), self.upload_name)

    def test_counted_errors_without_row_details_still_fail(self):
        page, _ = self.page("Upload errors: 1", [])
        with self.assertRaises(self.ns["CSVImportError"]):
            self.ns["_check_csv_import_result"](page, Mock(), self.upload_name)

    def test_previous_import_failure_is_dismissed(self):
        page, close = self.page("アップロードエラー: 19", ["previous failure"], matches_upload=False)
        self.ns["_check_csv_import_result"](page, Mock(), self.upload_name)
        close.click.assert_called_once()


class ShutterstockUploadFlowTests(unittest.TestCase):
    def setUp(self):
        import re

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.path = self.folder / "shutterstock.csv"
        self.path.write_text("Filename,Description\nexisting.mp4,Existing\nnew.mp4,New\nposter.jpg,Poster\n",
                             encoding="utf-8-sig")
        session = self.folder / "session-placeholder"
        session.touch()

        self.page = Mock()
        self.page.goto.side_effect = lambda url, **kwargs: setattr(self.page, "url", url)
        self.playwright = Mock()
        self.playwright.chromium.launch.return_value.new_context.return_value.new_page.return_value = self.page
        self.pending = {"video": {"existing.mp4", "waiting.mp4"}, "photo": set()}
        self.ingestion_planned = set()
        self.page.locator.return_value.first.set_input_files.side_effect = self.record_transfer
        self.page.request.get.side_effect = self.read_pending
        self.ns = {
            "csv": csv, "Path": Path, "re": re, "time": Mock(wraps=time),
            "TemporaryDirectory": tempfile.TemporaryDirectory, "SESSION_FILE": session,
            "_close_popups": Mock(), "_ensure_all_selected": Mock(),
            "_click_submit": Mock(), "_submit_one_by_one": Mock(),
            "_wait_not_submitted_cleared": Mock(return_value=("remains", "未送信 (1)")),
            "_apply_csv_on_tab": Mock(side_effect=self.read_csv_for_tab),
        }
        load_definitions("shutterstock_portal.py", {
            "run_portal_automation", "_ingested_names", "_wait_for_ingestion", "_read_csv_rows",
            "_row_filename", "_csv_for_pending", "_is_limit_error", "_VIDEO_EXTS", "PHOTO_URL",
            "VIDEO_URL", "INGESTED_URL", "UPLOAD_POLL_SECONDS", "UPLOAD_STALL_SECONDS",
        }, self.ns)
        self.ns["time"].sleep.side_effect = lambda *_: None
        self.csvs_applied = {}

    def record_transfer(self, files):
        self.ingestion_planned.update(Path(file).name for file in files)

    def read_pending(self, url, params):
        kind = params["mediaType"]
        if kind == "video" and self.page.wait_for_timeout.called:
            self.pending["video"].update(self.ingestion_planned)
        items = [{"originalFilename": name} for name in self.pending[kind]]
        return Mock(ok=True, json=Mock(return_value={"data": items, "totalCount": len(items)}))

    def read_csv_for_tab(self, page, path, log, label):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            self.csvs_applied[label] = {row["Filename"] for row in csv.DictReader(stream)}
        return True, True

    def run_portal(self, files, skip_submit=False):
        stock_tagger = types.ModuleType("stock_tagger")
        stock_tagger.validate_shutterstock_csv = Mock()
        with patch.dict("sys.modules", {"stock_tagger": stock_tagger}):
            return self.ns["run_portal_automation"](
                self.path, files=[self.folder / file for file in files], skip_submit=skip_submit,
                no_wait=True, playwright_instance=self.playwright, progress_callback=Mock(),
            )

    def test_video_test_mode_uploads_only_missing_and_applies_csv_before_stopping(self):
        result = self.run_portal(["existing.mp4", "new.mp4"], skip_submit=True)
        self.assertEqual(self.ingestion_planned, {"new.mp4"})
        self.assertEqual(self.csvs_applied, {"動画": {"existing.mp4", "new.mp4"}})
        self.assertEqual(result["submitted"], 0)
        self.page.reload.assert_not_called()
        self.ns["_click_submit"].assert_not_called()

    def test_missing_upload_never_reaches_csv(self):
        self.ns["UPLOAD_STALL_SECONDS"] = 0
        self.page.locator.return_value.first.set_input_files.side_effect = None
        with self.assertRaisesRegex(RuntimeError, r"CSV 適用前.*new\.mp4"):
            self.run_portal(["existing.mp4", "new.mp4"], skip_submit=True)
        self.ns["_apply_csv_on_tab"].assert_not_called()

    def test_previously_uploaded_items_still_get_weekly_limit_fallback(self):
        self.pending["photo"].add("poster.jpg")
        self.pending["video"] = {"waiting.mp4"}
        self.ns["_click_submit"].side_effect = [
            {"clicked": True, "code": "", "ok_count": 1},
            {"clicked": True, "code": "ERROR_VIDEO_MAX_SUBMISSION_COUNT_EXCEEDED", "message": "limit", "ok_count": 0},
        ]
        self.ns["_submit_one_by_one"].return_value = (0, 1, "limit")
        result = self.run_portal(["poster.jpg"])
        self.assertEqual(self.ingestion_planned, set())
        self.assertEqual(self.csvs_applied, {"画像": {"poster.jpg"}})
        self.ns["_submit_one_by_one"].assert_called_once_with(self.page, unittest.mock.ANY, "動画")
        self.assertEqual(result["limited"], [("動画", 1, "limit")])

    def test_video_csv_failure_does_not_click_submit(self):
        self.ns["_apply_csv_on_tab"].side_effect = None
        self.ns["_apply_csv_on_tab"].return_value = (True, False)
        result = self.run_portal(["existing.mp4"])
        self.assertEqual(result["csv_unapplied"], ["動画"])
        self.ns["_click_submit"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
