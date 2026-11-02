"""Offline regression checks; never import portal startup or access live accounts."""
import ast
import csv
from pathlib import Path
import tempfile
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
            "SHUTTERSTOCK_VIDEO_CATEGORIES", "UPLOAD_VIDEO_EXTENSIONS", "ADOBE_TO_SHUTTERSTOCK",
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
    def setUp(self):
        import re
        self.ns = {"re": re}
        load_definitions("shutterstock_portal.py", {"CSVImportError", "_check_csv_import_result"}, self.ns)

    def page(self, text, errors):
        dialog = Mock()
        dialog.inner_text.return_value = text
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
            self.ns["_check_csv_import_result"](page, Mock())
        close.click.assert_not_called()

    def test_all_success_ignores_stale_error_details(self):
        page, close = self.page("13行中13行が正常に処理されました。\nアップロードエラー: 0", ["previous failure"])
        self.ns["_check_csv_import_result"](page, Mock())
        close.click.assert_called_once()

    def test_inconsistent_totals_do_not_pass_as_success(self):
        page, _ = self.page("13行中3行が正常に処理されました。\nアップロードエラー: 0", [])
        with self.assertRaises(self.ns["CSVImportError"]):
            self.ns["_check_csv_import_result"](page, Mock())

    def test_counted_errors_without_row_details_still_fail(self):
        page, _ = self.page("Upload errors: 1", [])
        with self.assertRaises(self.ns["CSVImportError"]):
            self.ns["_check_csv_import_result"](page, Mock())


if __name__ == "__main__":
    unittest.main()
