#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock Media Auto Tagger - GUIアプリケーション
ダブルクリックで起動します
"""

import os
import sys
import json
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from dotenv import load_dotenv

ENV_DIR = Path.home() / ".stock-auto-tagger"
ENV_FILE = ENV_DIR / ".env"
load_dotenv(ENV_FILE)

# メイン処理をインポート
sys.path.insert(0, str(Path(__file__).parent))
from stock_tagger import (
    process_folder, move_processed_files, rename_variation_folders,
    upload_to_shutterstock, get_upload_targets,
    process_vector_files, move_vector_subfolders,
    get_vector_eps_files, prepare_vector_zips_with_xmp,
    estimate_api_requests, validate_upload_files,
    write_adobe_stock_csv, write_shutterstock_csv,
)
from adobe_portal import run_portal_automation

CONFIG_FILE = Path(__file__).parent / "config.json"

# ============================================================
# 設定の読み書き
# ============================================================

def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {"api_key": "", "input_folder": "", "variation_folder": ""}
    # APIキーは.envからのみ読み込む（config.jsonには保存しない）
    config["api_key"] = os.environ.get("GEMINI_API_KEY", "")
    return config

def save_config(config: dict):
    save_data = {k: v for k, v in config.items() if k != "api_key"}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

# ============================================================
# GUIアプリ
# ============================================================

class StockTaggerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Stock Media Auto Tagger")
        self.root.geometry("860x680")
        self.root.resizable(True, True)
        self.root.configure(bg="#1a1a2e")

        self.config = load_config()
        self.is_running = False
        self._timer_id = None
        self._timer_start = None
        self.last_results = []   # 工程1の結果を保持
        self.last_photo_results = []  # 写真素材の結果を保持
        self.last_ai_results = []  # AI素材の結果を保持
        self.last_folder = ""    # 処理したフォルダを保持
        # 有効サイト設定（デフォルト全ON）
        enabled = self.config.get("enabled_sites", ["adobe", "shutterstock", "pixta"])
        self.adobe_enabled = tk.BooleanVar(value="adobe" in enabled)
        self.ss_enabled = tk.BooleanVar(value="shutterstock" in enabled)
        self.pixta_enabled = tk.BooleanVar(value="pixta" in enabled)
        self.test_mode = bool(self.config.get("test_mode", False))

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._setup_styles()
        self._build_ui()

    def _on_close(self):
        self.root.destroy()
        os._exit(0)

    def _show_topmost_popup(self, title: str, message: str, error: bool = False):
        """最前面ポップアップで結果を通知する"""
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.attributes("-topmost", True)
        if error:
            messagebox.showwarning(title, message, parent=top)
        else:
            messagebox.showinfo(title, message, parent=top)
        top.destroy()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TFrame", background="#1a1a2e")
        style.configure("Card.TFrame", background="#16213e", relief="flat")

        style.configure("TLabel",
            background="#1a1a2e",
            foreground="#e0e0e0",
            font=("Helvetica", 10))

        style.configure("Title.TLabel",
            background="#1a1a2e",
            foreground="#e94560",
            font=("Helvetica", 18, "bold"))

        style.configure("Sub.TLabel",
            background="#1a1a2e",
            foreground="#8892b0",
            font=("Helvetica", 9))

        style.configure("Card.TLabel",
            background="#16213e",
            foreground="#e0e0e0",
            font=("Helvetica", 10))

        style.configure("TEntry",
            fieldbackground="#0f3460",
            foreground="#e0e0e0",
            insertcolor="#e94560",
            relief="flat",
            font=("Helvetica", 10))

        style.configure("Run.TButton",
            background="#e94560",
            foreground="#ffffff",
            font=("Helvetica", 12, "bold"),
            relief="flat",
            padding=(20, 10))
        style.map("Run.TButton",
            background=[("active", "#c73652"), ("disabled", "#4a4a6a")],
            foreground=[("disabled", "#888888")])

        style.configure("Browse.TButton",
            background="#0f3460",
            foreground="#e0e0e0",
            font=("Helvetica", 9),
            relief="flat",
            padding=(8, 4))
        style.map("Browse.TButton",
            background=[("active", "#1a4a80")])

        # --- 工程ボタン用スタイル ---
        style.configure("Step0.TButton",
            background="#64ffda",
            foreground="#0a0a1a",
            font=("BIZ UDゴシック", 10, "bold"),
            relief="flat",
            padding=(16, 8))
        style.map("Step0.TButton",
            background=[("active", "#4adbc0"), ("disabled", "#4a4a6a")],
            foreground=[("disabled", "#888888")])

        style.configure("Step1.TButton",
            background="#e94560",
            foreground="#ffffff",
            font=("BIZ UDゴシック", 10, "bold"),
            relief="flat",
            padding=(16, 8))
        style.map("Step1.TButton",
            background=[("active", "#c73652"), ("disabled", "#4a4a6a")],
            foreground=[("disabled", "#888888")])

        style.configure("Adobe.TButton",
            background="#1a6fa8",
            foreground="#e0e0e0",
            font=("BIZ UDゴシック", 10, "bold"),
            relief="flat",
            padding=(16, 8))
        style.map("Adobe.TButton",
            background=[("active", "#155a8a"), ("disabled", "#4a4a6a")],
            foreground=[("disabled", "#888888")])

        style.configure("SS.TButton",
            background="#cc5500",
            foreground="#e0e0e0",
            font=("BIZ UDゴシック", 10, "bold"),
            relief="flat",
            padding=(16, 8))
        style.map("SS.TButton",
            background=[("active", "#aa4400"), ("disabled", "#4a4a6a")],
            foreground=[("disabled", "#888888")])

        style.configure("Pixta.TButton",
            background="#b5338a",
            foreground="#e0e0e0",
            font=("BIZ UDゴシック", 10, "bold"),
            relief="flat",
            padding=(16, 8))
        style.map("Pixta.TButton",
            background=[("active", "#8f2870"), ("disabled", "#4a4a6a")],
            foreground=[("disabled", "#888888")])

        style.configure("Move.TButton",
            background="#0f3460",
            foreground="#e0e0e0",
            font=("BIZ UDゴシック", 10, "bold"),
            relief="flat",
            padding=(16, 8))
        style.map("Move.TButton",
            background=[("active", "#1a4a80"), ("disabled", "#4a4a6a")],
            foreground=[("disabled", "#888888")])

        style.configure("TProgressbar",
            background="#e94560",
            troughcolor="#0f3460",
            bordercolor="#0f3460",
            lightcolor="#e94560",
            darkcolor="#e94560")

    def _build_ui(self):
        # メインフレーム
        main = ttk.Frame(self.root, style="TFrame", padding=24)
        main.pack(fill=tk.BOTH, expand=True)

        # タイトル
        title_frame = ttk.Frame(main, style="TFrame")
        title_frame.pack(fill=tk.X, pady=(0, 20))

        ttk.Label(title_frame, text="Stock Media", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(title_frame, text=" Auto Tagger",
            background="#1a1a2e", foreground="#64ffda",
            font=("Helvetica", 18, "bold")).pack(side=tk.LEFT)
        ttk.Label(title_frame, text="  Adobe Stock · Shutterstock · Pixta",
            style="Sub.TLabel").pack(side=tk.LEFT, padx=(12, 0), pady=(6, 0))

        # --- 設定カード ---
        card = ttk.Frame(main, style="Card.TFrame", padding=16)
        card.pack(fill=tk.X, pady=(0, 12))

        # APIキーは.envで管理（UIには表示しない）
        self.api_key_var = tk.StringVar(value=self.config.get("api_key", ""))

        # 素材フォルダ
        ttk.Label(card, text="素材フォルダ", style="Card.TLabel",
            font=("Helvetica", 9, "bold")).grid(row=1, column=0, sticky="w", pady=(8, 0))

        folder_frame = ttk.Frame(card, style="Card.TFrame")
        folder_frame.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(8, 0))

        self.folder_var = tk.StringVar(value=self.config.get("input_folder", ""))
        self.folder_var.trace_add("write", self._update_estimate)
        folder_entry = ttk.Entry(folder_frame, textvariable=self.folder_var, width=48)
        folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(folder_frame, text="参照",
            style="Browse.TButton",
            command=self._browse_folder).pack(side=tk.LEFT, padx=(6, 0))

        # バリエーションフォルダ
        ttk.Label(card, text="バリエーションフォルダ", style="Card.TLabel",
            font=("Helvetica", 9, "bold")).grid(row=2, column=0, sticky="w", pady=(8, 0))

        variation_frame = ttk.Frame(card, style="Card.TFrame")
        variation_frame.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(8, 0))

        self.variation_folder_var = tk.StringVar(value=self.config.get("variation_folder", ""))
        self.variation_folder_var.trace_add("write", self._update_estimate)
        variation_entry = ttk.Entry(variation_frame, textvariable=self.variation_folder_var, width=48)
        variation_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(variation_frame, text="参照",
            style="Browse.TButton",
            command=self._browse_variation_folder).pack(side=tk.LEFT, padx=(6, 0))

        card.columnconfigure(1, weight=1)

        # アップロード先サイト
        ttk.Label(card, text="アップロード先", style="Card.TLabel",
            font=("Helvetica", 9, "bold")).grid(row=3, column=0, sticky="w", pady=(8, 0))

        sites_frame = ttk.Frame(card, style="Card.TFrame")
        sites_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(8, 0))

        for text, var, color in [
            ("Adobe Stock", self.adobe_enabled, "#1a6fa8"),
            ("Shutterstock", self.ss_enabled, "#cc5500"),
            ("Pixta", self.pixta_enabled, "#b5338a"),
        ]:
            tk.Checkbutton(sites_frame,
                text=text, variable=var,
                bg="#16213e", fg=color, activebackground="#16213e",
                activeforeground=color, selectcolor="#0f3460",
                relief="flat", font=("Helvetica", 9), cursor="hand2"
            ).pack(side=tk.LEFT, padx=(0, 12))

        # テストモード切替
        ttk.Label(card, text="モード", style="Card.TLabel",
            font=("Helvetica", 9, "bold")).grid(row=4, column=0, sticky="w", pady=(8, 0))

        mode_frame = ttk.Frame(card, style="Card.TFrame")
        mode_frame.grid(row=4, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(8, 0))

        self.test_mode_var = tk.BooleanVar(value=self.test_mode)
        self.test_mode_cb = tk.Checkbutton(mode_frame,
            text="テストモード（審査申請をスキップ）", variable=self.test_mode_var,
            bg="#16213e", fg="#f38ba8", activebackground="#16213e",
            activeforeground="#f38ba8", selectcolor="#0f3460",
            relief="flat", font=("Helvetica", 9), cursor="hand2",
            command=self._toggle_test_mode)
        self.test_mode_cb.pack(side=tk.LEFT)

        self.test_mode_label = tk.Label(mode_frame,
            text="ON" if self.test_mode else "",
            bg="#16213e", fg="#f38ba8",
            font=("Helvetica", 9, "bold"))
        self.test_mode_label.pack(side=tk.LEFT, padx=(8, 0))

        # 設定保存ボタン
        ttk.Button(card, text="設定を保存",
            style="Browse.TButton",
            command=self._save_settings).grid(row=5, column=2, sticky="e", pady=(10, 0))

        # --- 実行ボタン (1行目) ---
        btn_frame1 = ttk.Frame(main, style="TFrame")
        btn_frame1.pack(fill=tk.X, pady=(4, 4))

        self.step0_btn = ttk.Button(btn_frame1,
            text="⚡  【工程0】リネーム → 全自動",
            style="Step0.TButton",
            command=self._start_step0)
        self.step0_btn.pack(side=tk.LEFT)

        self.run_btn = ttk.Button(btn_frame1,
            text="▶  【工程1】タグ生成 & CSV出力",
            style="Step1.TButton",
            command=self._start_processing)
        self.run_btn.pack(side=tk.LEFT, padx=(12, 0))

        self.status_label = tk.Label(btn_frame1,
            text="待機中",
            bg="#1a1a2e", fg="#8892b0",
            font=("Helvetica", 9))
        self.status_label.pack(side=tk.LEFT, padx=(16, 0))

        # API概算リクエスト数ラベル
        self.estimate_label = tk.Label(btn_frame1,
            text="",
            bg="#1a1a2e", fg="#6c7086",
            font=("Helvetica", 8))
        self.estimate_label.pack(side=tk.RIGHT)

        # --- 実行ボタン (2行目) ---
        btn_frame2 = ttk.Frame(main, style="TFrame")
        btn_frame2.pack(fill=tk.X, pady=(0, 12))

        self.adobe_btn = ttk.Button(btn_frame2,
            text="☁  【工程2】Adobe",
            style="Adobe.TButton",
            command=self._upload_adobe)
        self.adobe_btn.pack(side=tk.LEFT)

        self.ss_btn = ttk.Button(btn_frame2,
            text="☁  【工程3】Shutterstock",
            style="SS.TButton",
            command=self._upload_shutterstock)
        self.ss_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.pixta_btn = ttk.Button(btn_frame2,
            text="🌸  【工程4】Pixta",
            style="Pixta.TButton",
            command=self._upload_pixta)
        self.pixta_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.move_btn = ttk.Button(btn_frame2,
            text="📦  【工程5】ファイル移動",
            style="Move.TButton",
            command=self._move_files)
        self.move_btn.pack(side=tk.LEFT, padx=(8, 0))

        # プログレスバー
        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(main,
            variable=self.progress_var,
            maximum=100,
            style="TProgressbar",
            length=400)
        self.progress.pack(fill=tk.X, pady=(0, 12))

        # --- ログエリア ---
        log_label = ttk.Label(main, text="処理ログ", style="Sub.TLabel")
        log_label.pack(anchor="w", pady=(0, 4))

        self.log_text = scrolledtext.ScrolledText(main,
            height=10,
            bg="#0a0a1a",
            fg="#cdd6f4",
            font=("BIZ UDゴシック", 9),
            relief="flat",
            insertbackground="#e94560",
            selectbackground="#e94560",
            wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # ログのカラータグ
        self.log_text.tag_config("success", foreground="#a6e3a1")
        self.log_text.tag_config("error", foreground="#f38ba8")
        self.log_text.tag_config("info", foreground="#89b4fa")
        self.log_text.tag_config("dim", foreground="#6c7086")

        # --- Pixta動画コピーエリア（処理完了後に表示）---
        self.video_panel_outer = tk.Frame(main, bg="#1a1a2e")
        self.video_panel_outer.pack(fill=tk.X, pady=(8, 0))
        self.video_panel_outer.pack_forget()  # 初期は非表示

        # スクロール可能なCanvasを内部に配置
        self._video_canvas = tk.Canvas(self.video_panel_outer,
            bg="#1a1a2e", highlightthickness=0, height=260)
        self._video_scrollbar = ttk.Scrollbar(self.video_panel_outer,
            orient="vertical", command=self._video_canvas.yview)
        self._video_canvas.configure(yscrollcommand=self._video_scrollbar.set)

        self._video_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._video_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Canvas内に実際のコンテンツを乗せるFrame
        self.video_panel_frame = tk.Frame(self._video_canvas, bg="#1a1a2e")
        self._video_canvas_window = self._video_canvas.create_window(
            (0, 0), window=self.video_panel_frame, anchor="nw")

        # Frameサイズ変更時にCanvas scrollregionを更新
        def _on_frame_configure(event):
            self._video_canvas.configure(
                scrollregion=self._video_canvas.bbox("all"))
        self.video_panel_frame.bind("<Configure>", _on_frame_configure)

        # Canvas幅変更時に内部Frameの幅を合わせる
        def _on_canvas_configure(event):
            self._video_canvas.itemconfig(
                self._video_canvas_window, width=event.width)
        self._video_canvas.bind("<Configure>", _on_canvas_configure)

        # マウスホイールでスクロール
        def _on_mousewheel(event):
            self._video_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._video_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # APIキー読み込み状況をログに表示
        api_key = self.config.get("api_key", "")
        if api_key:
            masked = api_key[:8] + "..." + api_key[-4:]
            self._log(f"✓ APIキー読み込み済み: {masked}", "success")
        else:
            env_path = ENV_DIR / ".env"
            self._log(f"✗ APIキーが見つかりません。{env_path} にGEMINI_API_KEYを設定してください。", "error")
        self._log("準備完了。フォルダを確認して実行ボタンを押してください。", "info")
        self._update_estimate()

    def _update_estimate(self, *args):
        """素材フォルダ＋バリエーションフォルダの内容からAPI概算リクエスト数を表示"""
        folder = self.folder_var.get().strip()
        if not folder or not Path(folder).exists():
            self.estimate_label.config(text="")
            return
        try:
            variation = self.variation_folder_var.get().strip() if hasattr(self, 'variation_folder_var') else ""
            est = estimate_api_requests(folder, variation)
            if est["total_requests"] > 0:
                self.estimate_label.config(
                    text=f"概算 {est['total_requests']} リクエスト")
            else:
                self.estimate_label.config(text="")
        except Exception:
            self.estimate_label.config(text="")

    def _get_saved_video_metadata(self, folder: str) -> dict:
        """工程1の動画メタデータを取得する（メモリ → JSONファイルの順で検索）。
        戻り値: {filename: metadata_dict}"""
        # まずメモリから
        all_saved = self.last_results + self.last_photo_results + self.last_ai_results
        result = {r["filename"]: r for r in all_saved if r.get("file_type") == "video"}
        if result:
            return result
        # JSONファイルから
        json_path = Path(folder) / "csv_output" / "video_metadata.json"
        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    video_list = json.load(f)
                return {r["filename"]: r for r in video_list}
            except Exception:
                pass
        return {}

    def _check_upload_limits(self, folder: str) -> bool:
        """アップロード制限チェック。問題があれば警告を出してユーザーに確認する。
        戻り値: 続行してよければTrue"""
        warnings = validate_upload_files(folder)
        if not warnings:
            return True
        msg = "以下のファイルがアップロード制限を超えています:\n\n"
        msg += "\n".join(f"• {w}" for w in warnings)
        msg += "\n\nこのまま続行しますか？\n（該当サイトでアップロードエラーになる可能性があります）"
        return messagebox.askyesno("アップロード制限の警告", msg)

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="素材フォルダを選択")
        if folder:
            self.folder_var.set(folder)

    def _browse_variation_folder(self):
        folder = filedialog.askdirectory(title="バリエーションフォルダを選択")
        if folder:
            self.variation_folder_var.set(folder)

    def _get_enabled_sites(self) -> list:
        sites = []
        if self.adobe_enabled.get():
            sites.append("adobe")
        if self.ss_enabled.get():
            sites.append("shutterstock")
        if self.pixta_enabled.get():
            sites.append("pixta")
        return sites

    def _toggle_test_mode(self):
        self.test_mode = self.test_mode_var.get()
        self.test_mode_label.config(text="ON" if self.test_mode else "")

    def _start_timer(self):
        import time as _time
        self._timer_start = _time.time()
        self._update_timer()

    def _update_timer(self):
        if self._timer_start is None:
            return
        import time as _time
        elapsed = int(_time.time() - self._timer_start)
        m, s = divmod(elapsed, 60)
        self.status_label.config(text=f"処理中 {m:02d}:{s:02d}")
        self._timer_id = self.root.after(1000, self._update_timer)

    def _stop_timer(self):
        if self._timer_id is not None:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None
        self._timer_start = None

    def _disable_btn(self, btn, **_kwargs):
        btn.state(["disabled"])

    def _enable_btn(self, btn, **_kwargs):
        btn.state(["!disabled"])

    def _save_settings(self):
        config = {
            "input_folder": self.folder_var.get().strip(),
            "variation_folder": self.variation_folder_var.get().strip(),
            "enabled_sites": self._get_enabled_sites(),
            "test_mode": self.test_mode_var.get()
        }
        save_config(config)
        self._log("設定を保存しました。", "success")

    def _start_step0(self):
        """工程0: バリエーションフォルダをリネーム → 自動で工程1へ"""
        if self.is_running:
            return
        api_key = self.api_key_var.get().strip()
        folder = self.folder_var.get().strip()
        variation_folder = self.variation_folder_var.get().strip()

        if not api_key:
            messagebox.showerror("エラー", "Gemini APIキーを入力してください。")
            return
        if not folder or not variation_folder:
            messagebox.showerror("エラー", "素材フォルダとバリエーションフォルダを指定してください。")
            return
        if not self._check_upload_limits(folder):
            return

        self._save_settings()
        self.is_running = True
        self._disable_btn(self.step0_btn)
        self._disable_btn(self.run_btn)
        self.progress_var.set(0)
        self._log("\n" + "─" * 50, "dim")
        self._log("工程0開始: バリエーションフォルダをスキャン中...", "info")

        threading.Thread(
            target=self._run_step0,
            args=(folder, api_key, variation_folder),
            daemon=True
        ).start()

    def _run_step0(self, folder: str, api_key: str, variation_folder: str):
        try:
            def progress_cb(message: str):
                self.root.after(0, lambda m=message: self._log(m))

            count = rename_variation_folders(api_key, folder, variation_folder or None, progress_cb)

            def on_rename_done():
                self._log(f"\n✓ リネーム完了！ {count}件を移動しました", "success")
                if count == 0:
                    self._log("バリエーションフォルダに素材がありませんでした。素材フォルダの既存ファイルで続行します。", "info")
                self._log("タグ生成を開始します...", "info")
                self.is_running = False
                self._start_processing()

            self.root.after(0, on_rename_done)

        except Exception as e:
            def on_error(err=str(e)):
                self._stop_timer()
                self._log(f"\n✗ エラー: {err}", "error")
                self.status_label.config(text="エラー")
                self.is_running = False
                self._enable_btn(self.step0_btn, bg="#64ffda", fg="#0a0a1a")
                self._enable_btn(self.run_btn, bg="#e94560", fg="#ffffff")
                messagebox.showerror("エラー", err)
            self.root.after(0, on_error)

    def _log(self, message: str, tag: str = ""):
        self.log_text.configure(state="normal")
        if tag:
            self.log_text.insert(tk.END, message + "\n", tag)
        else:
            self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")
        # テストモードのブラウザ待機通知
        if "ブラウザを開いたままにします" in message:
            self._show_topmost_popup("テストモード", "ブラウザを閉じると次の処理に進みます。")

    def _start_processing(self):
        if self.is_running:
            return
        api_key = self.api_key_var.get().strip()
        folder = self.folder_var.get().strip()

        if not api_key:
            messagebox.showerror("エラー", "Gemini APIキーを入力してください。")
            return
        if not folder:
            messagebox.showerror("エラー", "素材フォルダを指定してください。")
            return
        if not self._check_upload_limits(folder):
            return

        self._ensure_sessions_then_start(folder, api_key)

    def _login_then_run(self, name: str, mod_name: str, continuation):
        """セッションがない場合にログインを促し、完了後にcontinuationを実行する。"""
        if not messagebox.askyesno("ログインが必要です", f"{name} にログインしていません。\n今すぐブラウザでログインしますか？"):
            return
        import importlib
        mod = importlib.import_module(mod_name)
        done_event = threading.Event()
        def show_dialog(ev=done_event, n=name):
            messagebox.showinfo(f"{n} ログイン", f"ブラウザで{n}にログインしてください。\nログインが完了したらOKを押してください。")
            ev.set()
        def run_login():
            mod._confirm_callback = lambda ev=done_event: (self.root.after(0, show_dialog), ev.wait())
            mod.save_session()
            mod._confirm_callback = None
            self.root.after(0, continuation)
        threading.Thread(target=run_login, daemon=True).start()

    def _ensure_sessions_then_start(self, folder: str, api_key: str):
        from adobe_portal import SESSION_FILE as ADOBE_SESSION
        from shutterstock_portal import SESSION_FILE as SS_SESSION
        from pixta_portal import SESSION_FILE as PIXTA_SESSION

        login_map = [
            ("Adobe Stock", ADOBE_SESSION, "adobe_login"),
            ("Shutterstock", SS_SESSION, "shutterstock_login"),
            ("Pixta", PIXTA_SESSION, "pixta_login"),
        ]
        missing = [(name, mod) for name, sf, mod in login_map if not sf.exists()]

        if missing:
            names = "\n".join(f"• {name}" for name, _ in missing)
            if not messagebox.askyesno(
                "ログインが必要です",
                f"以下のサイトにログインしていません:\n{names}\n\n今すぐブラウザでログインしますか？"
            ):
                return

            self._save_settings()
            self.is_running = True
            self._disable_btn(self.run_btn)
            self._log("\n" + "─" * 50, "dim")
            self._log("ログイン処理を開始します...", "info")

            def run_logins():
                import importlib
                for name, mod_name in missing:
                    self.root.after(0, lambda n=name: self._log(f"🔐 {n} のブラウザを開きます。ログインしてください...", "info"))
                    mod = importlib.import_module(mod_name)

                    done_event = threading.Event()
                    def show_dialog(ev=done_event, n=name):
                        messagebox.showinfo(
                            f"{n} ログイン",
                            f"ブラウザで{n}にログインしてください。\nログインが完了したらOKを押してください。"
                        )
                        ev.set()
                    mod._confirm_callback = lambda ev=done_event: (
                        self.root.after(0, show_dialog),
                        ev.wait()
                    )
                    mod.save_session()
                    mod._confirm_callback = None

                    self.root.after(0, lambda n=name: self._log(f"[OK] {n} ログイン完了", "info"))
                self.root.after(0, lambda: self._do_start_processing(folder, api_key, already_started=True))

            threading.Thread(target=run_logins, daemon=True).start()
        else:
            self._do_start_processing(folder, api_key)

    def _do_start_processing(self, folder: str, api_key: str, already_started: bool = False):
        if not already_started:
            self._save_settings()
            self.is_running = True
            self._disable_btn(self.run_btn)
            self.progress_var.set(0)
            self._log("\n" + "─" * 50, "dim")
        self._log(f"処理開始: {folder}", "info")
        if self.test_mode:
            self._start_timer()

        thread = threading.Thread(
            target=self._run_processing,
            args=(folder, api_key),
            daemon=True
        )
        thread.start()

    def _run_processing(self, folder: str, api_key: str):
        if self.test_mode:
            self.root.after(0, lambda: self._log("⚠ テストモードで実行中（審査申請はスキップされます）", "error"))
        try:
            total_files = [0]

            def progress_cb(message: str):
                self.root.after(0, lambda m=message: self._log(m))

            def status_cb(current: int, total: int, filename: str):
                total_files[0] = total
                pct = (current / total) * 100
                self.root.after(0, lambda p=pct: self.progress_var.set(p))
                self.root.after(0, lambda c=current, t=total, f=filename:
                    self.status_label.config(text=f"{c}/{t} 処理中"))

            result = process_folder(folder, api_key, progress_cb, status_cb)

            # ベクター処理
            vector_result = process_vector_files(folder, api_key, progress_cb)
            self.last_vector_results = vector_result.get("results", [])

            # ベクター結果をメインCSVに追記 + JSON保存
            if self.last_vector_results:
                csv_folder = Path(folder) / "csv_output"
                adobe_csvs = sorted(csv_folder.glob("adobe_stock_*.csv"),
                                    key=lambda f: f.stat().st_mtime, reverse=True)
                ss_csvs = sorted(csv_folder.glob("shutterstock_*.csv"),
                                 key=lambda f: f.stat().st_mtime, reverse=True)
                from datetime import datetime as _dt
                _timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
                if adobe_csvs:
                    write_adobe_stock_csv(self.last_vector_results, adobe_csvs[0], append=True)
                    progress_cb(f"  [Vector] Adobe CSV追記: {len(self.last_vector_results)}件 → {adobe_csvs[0].name}")
                else:
                    new_adobe = csv_folder / f"adobe_stock_{_timestamp}.csv"
                    write_adobe_stock_csv(self.last_vector_results, new_adobe)
                    progress_cb(f"  [Vector] Adobe CSV新規作成: {len(self.last_vector_results)}件 → {new_adobe.name}")
                if ss_csvs:
                    write_shutterstock_csv(self.last_vector_results, ss_csvs[0], append=True)
                    progress_cb(f"  [Vector] SS CSV追記: {len(self.last_vector_results)}件 → {ss_csvs[0].name}")
                else:
                    new_ss = csv_folder / f"shutterstock_{_timestamp}.csv"
                    write_shutterstock_csv(self.last_vector_results, new_ss)
                    progress_cb(f"  [Vector] SS CSV新規作成: {len(self.last_vector_results)}件 → {new_ss.name}")
                # Pixta工程4で使い回すためJSONに保存
                import json as _json
                vector_meta_path = csv_folder / "vector_metadata.json"
                with open(vector_meta_path, "w", encoding="utf-8") as _f:
                    _json.dump(self.last_vector_results, _f, ensure_ascii=False, indent=2)
                progress_cb(f"  [Vector] メタデータ保存: {vector_meta_path.name}（{len(self.last_vector_results)}件）")

            # 完了
            def on_complete(vr=vector_result):
                self._stop_timer()
                self.progress_var.set(100)
                self.status_label.config(text="完了")
                self._log("\n✓ 工程1完了！", "success")
                self._log(f"  成功: {result['success']}件 / エラー: {result['error']}件", "success")
                self._log(f"  処理時間: {result.get('elapsed', '不明')}", "success")
                self._log(f"  CSV出力先: {result['csv_folder']}", "info")
                if result["errors"]:
                    self._log("\n以下のファイルでエラーが発生しました:", "error")
                    for e in result["errors"]:
                        self._log(f"  • {e['filename']}: {e['error']}", "error")
                # 結果を保存して工程2ボタンを有効化
                self.last_results = result.get("results", [])
                self.last_photo_results = result.get("photo_results", [])
                self.last_ai_results = result.get("ai_results", [])
                self.last_folder = folder
                if self.last_results:
                    self._enable_btn(self.move_btn, bg="#e94560")
                # 動画がある場合はコピーパネルを表示
                if result.get("video_results"):
                    self._show_video_panel(result["video_results"])
                if vr["success"] > 0:
                    self._log(f"  [Vector] {vr['success']}件のベクターデータを処理しました（Gemini解析 + CSV出力）", "success")
                if vr["errors"]:
                    for ve in vr["errors"]:
                        self._log(f"  [Vector] エラー: {ve['filename']} - {ve['error']}", "error")

                # タグ生成完了 → アップロードパイプラインへ
                self._log("\nアップロードパイプラインを開始します...", "info")
                threading.Thread(
                    target=self._run_pipeline_uploads,
                    args=(folder, api_key),
                    daemon=True
                ).start()

            self.root.after(0, on_complete)

        except Exception as e:
            def on_error(err=str(e)):
                self._stop_timer()
                self._log(f"\n✗ エラー: {err}", "error")
                self.status_label.config(text="エラー")
                self.is_running = False
                self._enable_btn(self.run_btn, bg="#e94560", fg="#ffffff")
                self._enable_btn(self.step0_btn, bg="#64ffda", fg="#0a0a1a")
                messagebox.showerror("エラー", err)

            self.root.after(0, on_error)

    def _run_pipeline_uploads(self, folder: str, api_key: str = ""):
        """工程0フルパイプライン: Adobe SFTP+ポータル → Shutterstock FTPS+ポータル → Pixta画像 → Pixta動画 → ファイル移動"""
        from pathlib import Path as _Path
        from adobe_portal import SESSION_FILE as ADOBE_SESSION
        from shutterstock_portal import run_portal_automation as ss_portal, SESSION_FILE as SS_SESSION
        from pixta_portal import run_upload_and_submit as pixta_upload, SESSION_FILE as PIXTA_SESSION
        from pixta_footage_portal import run_footage_upload
        from stock_tagger import get_upload_targets, get_ai_files, get_photo_files, analyze_video, UPLOAD_VIDEO_EXTENSIONS, move_processed_files

        def log(msg):
            self.root.after(0, lambda m=msg: self._log(m))

        import time as _time
        _pipeline_start = _time.time()

        folder_path = _Path(folder)
        csv_folder = folder_path / "csv_output"
        failed_services = []  # エラーが発生したサービス名を記録
        enabled_sites = self._get_enabled_sites()

        # ---- Adobe Stock（全素材を1回でアップロード）----
        adobe_csvs = sorted(csv_folder.glob("adobe_stock_*.csv"),
                            key=lambda f: f.stat().st_mtime, reverse=True)
        adobe_targets = get_upload_targets(folder_path, "adobe")
        vector_eps_files = get_vector_eps_files(folder_path)
        ai_files = get_ai_files(folder_path)
        photo_files = get_photo_files(folder_path)

        # 全Adobeファイルを統合（動画含む）
        all_adobe_files = list(adobe_targets) + list(vector_eps_files) + list(ai_files) + list(photo_files)

        # file_settings: ファイル名→{content_type, is_ai}（動画・ベクターは自動設定のためスキップされる）
        adobe_file_settings = {}
        for f in adobe_targets:
            adobe_file_settings[f.name] = {"content_type": "illustration", "is_ai": False}
        for f in vector_eps_files:
            adobe_file_settings[f.name] = {"content_type": "illustration", "is_ai": False}
        for f in ai_files:
            adobe_file_settings[f.name] = {"content_type": "illustration", "is_ai": True}
        for f in photo_files:
            adobe_file_settings[f.name] = {"content_type": "photo", "is_ai": False}

        if "adobe" not in enabled_sites:
            log("[—] Adobe Stock は無効です。スキップします。")
        elif not ADOBE_SESSION.exists():
            log("[!] adobe_session.json が見つかりません。Adobeをスキップします。")
            failed_services.append("Adobe Stock")
        elif not all_adobe_files:
            log("[!] Adobeにアップロード対象のファイルがありません。Adobeをスキップします。")
        elif not adobe_csvs:
            log("[!] Adobe用CSVが見つかりません。Adobeをスキップします。")
        else:
            try:
                log("\n" + "─" * 40)
                log(f"☁ Adobe Stock 一括アップロード開始... ({len(all_adobe_files)}件)")
                details = []
                if adobe_targets:
                    details.append(f"通常{len(adobe_targets)}")
                if vector_eps_files:
                    details.append(f"ベクター{len(vector_eps_files)}")
                if ai_files:
                    details.append(f"AI{len(ai_files)}")
                if photo_files:
                    details.append(f"写真{len(photo_files)}")
                log(f"  内訳: {' / '.join(details)}")

                portal_result = run_portal_automation(
                    csv_path=adobe_csvs[0],
                    progress_callback=log,
                    headless=False,
                    files=all_adobe_files,
                    confirm_submit_callback=lambda: not self.test_mode,
                    file_settings=adobe_file_settings,
                )
                log(f"[OK] Adobe ポータル提出完了: {portal_result['submitted']}件")
            except Exception as e:
                log(f"[NG] Adobe エラー: {e}")
                failed_services.append("Adobe Stock")

        # ---- Shutterstock（全素材を1回でアップロード）----
        ss_csvs = sorted(csv_folder.glob("shutterstock_*.csv"),
                         key=lambda f: f.stat().st_mtime, reverse=True)
        ss_targets = get_upload_targets(folder_path, "shutterstock")
        # vector_eps_files は Adobe セクションで既に取得済み
        # photo_files も Adobe セクションで既に取得済み

        # 全SSファイルを統合（通常 + ベクター + 写真、AI除外）
        photo_ss_files = [f for f in photo_files if f.suffix.lower() not in UPLOAD_VIDEO_EXTENSIONS]
        all_ss_files = list(ss_targets) + list(vector_eps_files) + photo_ss_files

        if "shutterstock" not in enabled_sites:
            log("[—] Shutterstock は無効です。スキップします。")
        elif not SS_SESSION.exists():
            log("[!] shutterstock_session.json が見つかりません。Shutterstockをスキップします。")
            failed_services.append("Shutterstock")
        elif not all_ss_files:
            log("[!] Shutterstockにアップロード対象のファイルがありません。Shutterstockをスキップします。")
        elif not ss_csvs:
            log("[!] Shutterstock用CSVが見つかりません。Shutterstockをスキップします。")
        else:
            try:
                log("\n" + "─" * 40)
                log(f"☁ Shutterstock 一括アップロード開始... ({len(all_ss_files)}件)")
                details = []
                if ss_targets:
                    details.append(f"通常{len(ss_targets)}")
                if vector_eps_files:
                    details.append(f"ベクター{len(vector_eps_files)}")
                if photo_ss_files:
                    details.append(f"写真{len(photo_ss_files)}")
                log(f"  内訳: {' / '.join(details)}")

                ss_portal_result = ss_portal(
                    csv_path=ss_csvs[0],
                    files=all_ss_files,
                    progress_callback=log,
                    headless=False,
                    skip_submit=self.test_mode,
                )
                log(f"[OK] Shutterstock ポータル提出完了: {ss_portal_result['submitted']}件")
            except Exception as e:
                log(f"[NG] Shutterstock エラー: {e}")
                failed_services.append("Shutterstock")

        # ---- Pixta（イラスト + 動画 + 写真の3工程）----
        video_targets = []  # ファイル移動で参照するため初期化
        if "pixta" not in enabled_sites:
            log("[—] Pixta は無効です。スキップします。")
        elif not PIXTA_SESSION.exists():
            log("[!] pixta_session.json が見つかりません。Pixtaをスキップします。")
            failed_services.append("Pixta")
        else:
            # ファイル収集
            all_pixta = get_upload_targets(folder_path, "pixta")
            pixta_images = [f for f in all_pixta if f.suffix.lower() not in UPLOAD_VIDEO_EXTENSIONS]
            pixta_videos = [f for f in all_pixta if f.suffix.lower() in UPLOAD_VIDEO_EXTENSIONS]

            ai_files_all = get_ai_files(folder_path)
            ai_images = [f for f in ai_files_all if f.suffix.lower() not in UPLOAD_VIDEO_EXTENSIONS]
            ai_videos = [f for f in ai_files_all if f.suffix.lower() in UPLOAD_VIDEO_EXTENSIONS]

            photo_files_all = get_photo_files(folder_path)
            photo_images = [f for f in photo_files_all if f.suffix.lower() not in UPLOAD_VIDEO_EXTENSIONS]

            # ベクターZIP
            vector_results = getattr(self, 'last_vector_results', [])
            vector_zips = []
            if vector_results:
                try:
                    vector_zips = prepare_vector_zips_with_xmp(folder, vector_results, log)
                except Exception as e:
                    log(f"[NG] Pixta Vector ZIP作成エラー: {e}")
                    failed_services.append("Pixta Vector")

            # ---- 工程A: イラストページ（通常画像 + ベクターZIP + AI画像）----
            illust_files = pixta_images + vector_zips + ai_images
            ai_image_names = {f.name for f in ai_images} if ai_images else None

            if illust_files:
                try:
                    log("\n" + "─" * 40)
                    log(f"🌸 Pixta イラスト一括アップロード開始: {len(illust_files)}件...")
                    details = []
                    if pixta_images:
                        details.append(f"通常{len(pixta_images)}")
                    if vector_zips:
                        details.append(f"ベクター{len(vector_zips)}")
                    if ai_images:
                        details.append(f"AI{len(ai_images)}")
                    log(f"  内訳: {' / '.join(details)}")
                    pixta_result = pixta_upload(
                        files=illust_files,
                        progress_callback=log,
                        skip_submit=self.test_mode,
                        ai_filenames=ai_image_names,
                    )
                    log(f"[OK] Pixtaイラスト完了: アップロード{pixta_result['uploaded']}件 / 審査申請{pixta_result['submitted']}件")
                except Exception as e:
                    log(f"[NG] Pixtaイラスト エラー: {e}")
                    failed_services.append("Pixtaイラスト")
            else:
                log("[!] Pixtaイラストアップロード対象がありません。スキップします。")

            # ---- 工程B: 動画（通常動画 + AI動画）----
            all_videos = pixta_videos + ai_videos
            video_targets = all_videos  # ファイル移動用
            if all_videos:
                try:
                    log("\n" + "─" * 40)
                    log(f"🎬 Pixta 動画一括アップロード開始: {len(all_videos)}件...")
                    if ai_videos:
                        log(f"  内訳: 通常{len(pixta_videos)} / AI{len(ai_videos)}")

                    saved_video_meta = self._get_saved_video_metadata(folder)
                    video_metadata = []
                    for vf in all_videos:
                        meta = saved_video_meta.get(vf.name)
                        if meta:
                            title = meta.get("pixta_title_ja", vf.stem)
                            tags = [t.strip() for t in meta.get("pixta_keywords_ja", "").split(",") if t.strip()][:50]
                            log(f"  工程1の結果を使用: {vf.name} → {title[:40]}")
                        else:
                            log(f"  Geminiで動画解析中: {vf.name}...")
                            stem = vf.stem
                            filename_hint = stem.split("_", 1)[1].replace("_", " ") if "_" in stem else stem
                            meta = analyze_video(vf, api_key, log, filename_hint=filename_hint)
                            title = meta.get("pixta_title_ja", filename_hint)
                            tags = [t.strip() for t in meta.get("pixta_keywords_ja", "").split(",") if t.strip()][:50]
                            log(f"  解析完了: タイトル={title[:40]} / タグ{len(tags)}件")
                        video_metadata.append({"title": title, "tags": tags})

                    ai_video_names = {f.name for f in ai_videos} if ai_videos else None
                    footage_result = run_footage_upload(
                        files=all_videos,
                        metadata=video_metadata,
                        progress_callback=log,
                        skip_submit=self.test_mode,
                        ai_filenames=ai_video_names,
                    )
                    log(f"[OK] Pixta動画完了: アップロード{footage_result['uploaded']}件 / 審査申請{footage_result['submitted']}件")
                except Exception as e:
                    log(f"[NG] Pixta動画 エラー: {e}")
                    failed_services.append("Pixta動画")

            # ---- 工程C: 写真ページ（写真のみ）----
            if photo_images:
                try:
                    log("\n" + "─" * 40)
                    log(f"📷 Pixta 写真アップロード開始... ({len(photo_images)}件)")
                    photo_pixta_result = pixta_upload(
                        files=photo_images,
                        progress_callback=log,
                        skip_submit=self.test_mode,
                        is_photo=True,
                    )
                    log(f"[OK] Pixta 写真 完了: アップロード{photo_pixta_result['uploaded']}件 / 審査申請{photo_pixta_result['submitted']}件")
                except Exception as e:
                    log(f"[NG] Pixta 写真 エラー: {e}")
                    failed_services.append("Pixta 写真")

        # ---- ファイル移動（自動） ----
        log("\n" + "─" * 40)
        if failed_services:
            log(f"[!] 以下のサービスでエラーが発生したため、ファイル移動をスキップしました:")
            for svc in failed_services:
                log(f"    • {svc}")
            log("    問題を解消した後、【工程5】ボタンで手動移動してください。")
            def on_pipeline_error():
                self._stop_timer()
                self.status_label.config(text="一部エラーあり")
                self.is_running = False
                self._enable_btn(self.run_btn, bg="#e94560", fg="#ffffff")
                self._enable_btn(self.move_btn, bg="#e94560")
                svc_list = "\n".join(f"• {s}" for s in failed_services)
                self._show_topmost_popup(
                    "一部エラーあり",
                    f"以下のサービスでエラーが発生しました:\n{svc_list}\n\nファイル移動をスキップしました。",
                    error=True
                )
            self.root.after(0, on_pipeline_error)
            return
        if self.test_mode:
            log("[テストモード] ファイル移動をスキップしました。")
        else:
            log("📦 ファイルを移動中...")
            try:
                move_results = list(self.last_results) + list(self.last_photo_results) + list(self.last_ai_results)
                for vf in video_targets:
                    move_results.append({"original_path": str(vf)})
                move_result = move_processed_files(move_results, self.last_folder, log)
                log(f"[OK] ファイル移動完了: {move_result['moved']}件 → {move_result['dest_folder']}")
                if move_result["errors"]:
                    for e in move_result["errors"]:
                        log(f"  [NG] 移動失敗: {e}")
            except Exception as e:
                log(f"[NG] ファイル移動エラー: {e}")
            # Vectorサブフォルダも移動
            try:
                vec_move = move_vector_subfolders(self.last_folder, log)
                if vec_move["moved"] > 0:
                    log(f"[OK] Vectorフォルダ移動完了: {vec_move['moved']}件")
            except Exception as e:
                log(f"[NG] Vectorフォルダ移動エラー: {e}")

        # ---- 全工程完了 ----
        _elapsed = _time.time() - _pipeline_start
        _minutes = int(_elapsed // 60)
        _seconds = int(_elapsed % 60)

        def on_pipeline_done():
            self._stop_timer()
            self._log("\n" + "─" * 50, "dim")
            self._log(f"✓ 全工程完了！（処理時間: {_minutes}分{_seconds}秒）", "success")
            self.status_label.config(text="完了")
            self.is_running = False
            self._enable_btn(self.run_btn, bg="#e94560", fg="#ffffff")
            self._enable_btn(self.step0_btn, bg="#64ffda", fg="#0a0a1a")
            _time_str = f"{_minutes}分{_seconds}秒" if _minutes > 0 else f"{_seconds}秒"
            if self.test_mode:
                self._show_topmost_popup("全工程完了", f"アップロード完了しました（テストモード：ファイル移動はスキップ）。\n処理時間: {_time_str}")
            else:
                self._show_topmost_popup("全工程完了", f"アップロード＆ファイル移動まで全て完了しました。\n処理時間: {_time_str}")

        self.root.after(0, on_pipeline_done)

    def _upload_adobe(self):
        """工程2: Adobe Stock ブラウザアップロード → ポータル提出"""
        if not self.adobe_enabled.get():
            messagebox.showinfo("無効", "Adobe Stock は無効になっています。\n設定のチェックボックスを確認してください。")
            return
        from pathlib import Path as _Path
        from adobe_portal import SESSION_FILE

        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showerror("エラー", "素材フォルダを指定してください。")
            return
        if not self._check_upload_limits(folder):
            return

        if not SESSION_FILE.exists():
            self._login_then_run("Adobe Stock", "adobe_login", self._upload_adobe)
            return

        folder_path = _Path(folder)
        from stock_tagger import get_ai_files as _get_ai_files, get_photo_files as _get_photo_files
        targets = get_upload_targets(folder_path, "adobe")
        vector_eps = get_vector_eps_files(folder_path)
        photo_files = _get_photo_files(folder_path)
        ai_files = _get_ai_files(folder_path)
        if not targets and not vector_eps and not photo_files and not ai_files:
            messagebox.showerror("エラー", "Adobeにアップロード対象のファイルがありません。")
            return

        csv_folder = folder_path / "csv_output"
        csvs = sorted(csv_folder.glob("adobe_stock_*.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not csvs:
            messagebox.showerror("エラー", "Adobe用CSVが見つかりません。先に工程1を実行してください。")
            return

        csv_path = csvs[0]

        # --- 開始前確認ダイアログ ---
        all_targets = targets + vector_eps + photo_files + ai_files
        file_list = "\n".join(f"  {f.name}" for f in all_targets[:10])
        if len(all_targets) > 10:
            file_list += f"\n  ...他{len(all_targets) - 10}件"
        extra_notes = []
        if vector_eps:
            extra_notes.append(f"ベクター: {len(vector_eps)}件")
        if photo_files:
            extra_notes.append(f"写真: {len(photo_files)}件")
        if ai_files:
            extra_notes.append(f"AI: {len(ai_files)}件")
        extra_note = f"\n（うち{' / '.join(extra_notes)}）" if extra_notes else ""
        if not messagebox.askyesno(
            "Adobe Stockアップロード確認",
            f"以下の{len(all_targets)}件をAdobe Stockにアップロードします。{extra_note}\n\n{file_list}\n\n続行しますか？"
        ):
            return

        self._disable_btn(self.adobe_btn)
        self._log("\n☁ Adobe Stock アップロード開始（ブラウザ直接アップロード）...", "info")

        def run():
            import time as _time
            _step_start = _time.time()
            log = lambda m: self.root.after(0, lambda msg=m: self._log(msg))

            try:
                # 全ファイルを統合（動画含む）
                all_files = list(targets) + list(vector_eps) + list(ai_files) + list(photo_files)

                # file_settings
                file_settings = {}
                for f in targets:
                    file_settings[f.name] = {"content_type": "illustration", "is_ai": False}
                for f in vector_eps:
                    file_settings[f.name] = {"content_type": "illustration", "is_ai": False}
                for f in ai_files:
                    file_settings[f.name] = {"content_type": "illustration", "is_ai": True}
                for f in photo_files:
                    file_settings[f.name] = {"content_type": "photo", "is_ai": False}

                if not all_files:
                    log("[!] アップロード対象がありません。")
                else:
                    log(f"対象ファイル: {len(all_files)}件")
                    details = []
                    if targets:
                        details.append(f"通常{len(targets)}")
                    if vector_eps:
                        details.append(f"ベクター{len(vector_eps)}")
                    if ai_files:
                        details.append(f"AI{len(ai_files)}")
                    if photo_files:
                        details.append(f"写真{len(photo_files)}")
                    log(f"  内訳: {' / '.join(details)}")
                    log("\n🌐 Adobeポータル一括アップロード & 提出開始...")
                    portal_result = run_portal_automation(
                        csv_path=csv_path,
                        progress_callback=log,
                        headless=False,
                        files=all_files,
                        confirm_submit_callback=lambda: not self.test_mode,
                        file_settings=file_settings,
                    )
                    log(f"[OK] ポータル提出完了: {portal_result['submitted']}件")

                _el = int(_time.time() - _step_start)
                _m, _s = divmod(_el, 60)
                _t = f"{_m}分{_s}秒" if _m > 0 else f"{_s}秒"
                def on_adobe_done(_t=_t):
                    self._log("\n✓ 工程2 Adobe 完了！", "success")
                    self._enable_btn(self.adobe_btn, bg="#1a6fa8")
                    self._show_topmost_popup("工程2 完了", f"Adobe Stock アップロード完了！\n処理時間: {_t}")
                self.root.after(0, on_adobe_done)
            except Exception as e:
                def on_adobe_err(err=str(e)):
                    self._log(f"\n[NG] Adobeエラー: {err}", "error")
                    self._enable_btn(self.adobe_btn, bg="#1a6fa8")
                    self._show_topmost_popup("工程2 エラー", f"Adobeエラー:\n{err}", error=True)
                self.root.after(0, on_adobe_err)

        threading.Thread(target=run, daemon=True).start()

    def _upload_shutterstock(self):
        """工程1.7: Shutterstock ブラウザアップロード → ポータルCSV適用 → 審査提出"""
        if not self.ss_enabled.get():
            messagebox.showinfo("無効", "Shutterstock は無効になっています。\n設定のチェックボックスを確認してください。")
            return
        from shutterstock_portal import run_portal_automation as ss_portal, SESSION_FILE as SS_SESSION
        from stock_tagger import get_upload_targets

        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showerror("エラー", "素材フォルダを指定してください。")
            return
        if not self._check_upload_limits(folder):
            return

        if not SS_SESSION.exists():
            self._login_then_run("Shutterstock", "shutterstock_login", self._upload_shutterstock)
            return

        from pathlib import Path as _Path
        folder_path = _Path(folder)
        csv_folder = folder_path / "csv_output"
        csvs = sorted(csv_folder.glob("shutterstock_*.csv"),
                      key=lambda f: f.stat().st_mtime, reverse=True)

        from stock_tagger import get_photo_files as _get_photo_files, UPLOAD_VIDEO_EXTENSIONS
        ss_targets = get_upload_targets(folder_path, "shutterstock")
        photo_files = _get_photo_files(folder_path)

        if not csvs:
            messagebox.showerror("エラー", "Shutterstock用CSVが見つかりません。先に工程1を実行してください。")
            return

        # ベクター EPS ファイルも準備
        vector_eps_ss = get_vector_eps_files(folder_path)

        # 全SSファイルを統合（通常 + ベクター + 写真、AI除外）
        photo_ss_files = [f for f in photo_files if f.suffix.lower() not in UPLOAD_VIDEO_EXTENSIONS]
        all_ss_files = list(ss_targets) + list(vector_eps_ss) + photo_ss_files

        if not all_ss_files:
            messagebox.showerror("エラー", "Shutterstockにアップロード対象のファイルがありません。")
            return

        csv_path = csvs[0]

        all_targets = all_ss_files
        # --- 開始前確認ダイアログ ---
        file_list = "\n".join(f"  {f.name}" for f in all_targets[:10])
        if len(all_targets) > 10:
            file_list += f"\n  ...他{len(all_targets) - 10}件"
        extra_notes = []
        if photo_files:
            extra_notes.append(f"写真: {len(photo_files)}件")
        if vector_eps_ss:
            extra_notes.append(f"Vector: {len(vector_eps_ss)}件")
        extra_note = f"\n（うち{' / '.join(extra_notes)}）" if extra_notes else ""
        if not messagebox.askyesno(
            "Shutterstockアップロード確認",
            f"以下の{len(all_targets)}件をShutterstockにアップロードします。{extra_note}\n\n{file_list}\n\n続行しますか？"
        ):
            return

        self._disable_btn(self.ss_btn)
        self._log(f"\n☁ Shutterstock ブラウザアップロード開始... ({len(all_targets)}件)", "info")

        def run():
            import time as _time
            _step_start = _time.time()
            log = lambda m: self.root.after(0, lambda msg=m: self._log(msg))
            try:
                log(f"対象ファイル: {len(all_ss_files)}件")
                details = []
                if ss_targets:
                    details.append(f"通常{len(ss_targets)}")
                if vector_eps_ss:
                    details.append(f"ベクター{len(vector_eps_ss)}")
                if photo_ss_files:
                    details.append(f"写真{len(photo_ss_files)}")
                log(f"  内訳: {' / '.join(details)}")

                portal_result = ss_portal(
                    csv_path=csv_path,
                    files=all_ss_files,
                    progress_callback=log,
                    headless=False,
                    skip_submit=self.test_mode,
                )
                log(f"[OK] Shutterstock ポータル提出完了！")
                if portal_result["errors"]:
                    for e in portal_result["errors"]:
                        log(f"  [NG] {e}")

                _el = int(_time.time() - _step_start)
                _m, _s = divmod(_el, 60)
                _t = f"{_m}分{_s}秒" if _m > 0 else f"{_s}秒"
                def on_done(_t=_t):
                    self._log("\n✓ 工程3 Shutterstock 完了！", "success")
                    self._enable_btn(self.ss_btn, bg="#cc5500")
                    self._show_topmost_popup("工程3 完了", f"Shutterstock アップロード完了！\n処理時間: {_t}")
                self.root.after(0, on_done)

            except Exception as e:
                def on_ss_err(err=str(e)):
                    self._log(f"\n[NG] エラー: {err}", "error")
                    self._enable_btn(self.ss_btn, bg="#cc5500")
                    self._show_topmost_popup("工程3 エラー", f"Shutterstockエラー:\n{err}", error=True)
                self.root.after(0, on_ss_err)

        threading.Thread(target=run, daemon=True).start()

    def _upload_pixta(self):
        """工程4: Pixta 画像アップロード → 動画アップロード（Gemini解析 → 審査申請）"""
        if not self.pixta_enabled.get():
            messagebox.showinfo("無効", "Pixta は無効になっています。\n設定のチェックボックスを確認してください。")
            return
        from pixta_portal import run_upload_and_submit as pixta_upload, SESSION_FILE as PIXTA_SESSION
        from pixta_footage_portal import run_footage_upload
        from pathlib import Path as _Path
        from stock_tagger import get_upload_targets, get_ai_files as _get_ai_files, analyze_video, UPLOAD_VIDEO_EXTENSIONS

        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showerror("エラー", "素材フォルダを指定してください。")
            return
        if not self._check_upload_limits(folder):
            return

        if not PIXTA_SESSION.exists():
            self._login_then_run("Pixta", "pixta_login", self._upload_pixta)
            return

        api_key = self.api_key_var.get().strip()
        folder_path = _Path(folder)
        all_targets = get_upload_targets(folder_path, "pixta")
        ai_files = _get_ai_files(folder_path)
        from stock_tagger import get_photo_files as _get_photo_files
        photo_files = _get_photo_files(folder_path)
        image_targets = [f for f in all_targets if f.suffix.lower() not in UPLOAD_VIDEO_EXTENSIONS]
        video_targets = [f for f in all_targets if f.suffix.lower() in UPLOAD_VIDEO_EXTENSIONS]

        vector_results = getattr(self, 'last_vector_results', [])
        if not vector_results:
            # メモリになければJSONから読み込み
            import json as _json
            vector_meta_path = folder_path / "csv_output" / "vector_metadata.json"
            if vector_meta_path.exists():
                try:
                    with open(vector_meta_path, "r", encoding="utf-8") as _f:
                        vector_results = _json.load(_f)
                    self.last_vector_results = vector_results
                except Exception:
                    pass

        if not all_targets and not ai_files and not photo_files and not vector_results:
            messagebox.showinfo("確認", "Pixtaアップロード対象のファイルが見つかりません。")
            return

        # --- 開始前確認ダイアログ ---
        confirm_targets = list(all_targets) + photo_files + ai_files
        file_list = "\n".join(f"  {f.name}" for f in confirm_targets[:10])
        if len(confirm_targets) > 10:
            file_list += f"\n  ...他{len(confirm_targets) - 10}件"
        extra_notes = []
        if photo_files:
            extra_notes.append(f"写真: {len(photo_files)}件")
        if ai_files:
            extra_notes.append(f"AI: {len(ai_files)}件")
        if video_targets:
            extra_notes.append(f"動画: {len(video_targets)}件")
        if vector_results:
            extra_notes.append(f"Vector: {len(vector_results)}件")
        extra_note = f"\n（うち{' / '.join(extra_notes)}）" if extra_notes else ""
        if not messagebox.askyesno(
            "Pixtaアップロード確認",
            f"以下の{len(confirm_targets)}件をPixtaにアップロードします。{extra_note}\n\n{file_list}\n\n続行しますか？"
        ):
            return

        self._disable_btn(self.pixta_btn)
        self._log(
            f"\n🌸 Pixta アップロード開始（画像:{len(image_targets)}件 / 動画:{len(video_targets)}件 / Vector:{len(vector_results)}件）...",
            "info"
        )

        def run():
            import time as _time
            _step_start = _time.time()
            log = lambda m: self.root.after(0, lambda msg=m: self._log(msg))
            try:
                # ファイル収集
                ai_images = [f for f in ai_files if f.suffix.lower() not in UPLOAD_VIDEO_EXTENSIONS]
                ai_videos = [f for f in ai_files if f.suffix.lower() in UPLOAD_VIDEO_EXTENSIONS]
                photo_img = [f for f in photo_files if f.suffix.lower() not in UPLOAD_VIDEO_EXTENSIONS]

                # ベクターZIP
                vector_zips = []
                if vector_results:
                    try:
                        vector_zips = prepare_vector_zips_with_xmp(folder, vector_results, log)
                    except Exception as e:
                        log(f"[NG] Pixta Vector ZIP作成エラー: {e}")

                # ---- 工程A: イラストページ（通常画像 + ベクターZIP + AI画像）----
                illust_files = image_targets + vector_zips + ai_images
                ai_image_names = {f.name for f in ai_images} if ai_images else None

                if illust_files:
                    log("\n" + "─" * 40)
                    log(f"🌸 Pixta イラスト一括アップロード開始: {len(illust_files)}件...")
                    details = []
                    if image_targets:
                        details.append(f"通常{len(image_targets)}")
                    if vector_zips:
                        details.append(f"ベクター{len(vector_zips)}")
                    if ai_images:
                        details.append(f"AI{len(ai_images)}")
                    log(f"  内訳: {' / '.join(details)}")
                    illust_result = pixta_upload(
                        files=illust_files,
                        progress_callback=log,
                        skip_submit=self.test_mode,
                        ai_filenames=ai_image_names,
                    )
                    log(f"[OK] Pixtaイラスト完了: アップロード{illust_result['uploaded']}件 / 審査申請{illust_result['submitted']}件")
                else:
                    log("[!] Pixtaイラストアップロード対象がありません。スキップします。")

                # ---- 工程B: 動画（通常動画 + AI動画）----
                all_videos = video_targets + ai_videos
                if all_videos:
                    log("\n" + "─" * 40)
                    log(f"🎬 Pixta 動画一括アップロード開始: {len(all_videos)}件...")
                    if ai_videos:
                        log(f"  内訳: 通常{len(video_targets)} / AI{len(ai_videos)}")

                    saved_video_meta = self._get_saved_video_metadata(folder)
                    video_metadata = []
                    for vf in all_videos:
                        meta = saved_video_meta.get(vf.name)
                        if meta:
                            title = meta.get("pixta_title_ja", vf.stem)
                            tags = [t.strip() for t in meta.get("pixta_keywords_ja", "").split(",") if t.strip()][:50]
                            log(f"  工程1の結果を使用: {vf.name} → {title[:40]}")
                        else:
                            log(f"  Geminiで動画解析中: {vf.name}...")
                            stem = vf.stem
                            filename_hint = stem.split("_", 1)[1].replace("_", " ") if "_" in stem else stem
                            meta = analyze_video(vf, api_key, log, filename_hint=filename_hint)
                            title = meta.get("pixta_title_ja", filename_hint)
                            tags = [t.strip() for t in meta.get("pixta_keywords_ja", "").split(",") if t.strip()][:50]
                            log(f"  解析完了: タイトル={title[:40]} / タグ{len(tags)}件")
                        video_metadata.append({"title": title, "tags": tags})

                    ai_video_names = {f.name for f in ai_videos} if ai_videos else None
                    footage_result = run_footage_upload(
                        files=all_videos,
                        metadata=video_metadata,
                        progress_callback=log,
                        skip_submit=self.test_mode,
                        ai_filenames=ai_video_names,
                    )
                    log(f"[OK] Pixta動画完了: アップロード{footage_result['uploaded']}件 / 審査申請{footage_result['submitted']}件")

                # ---- 工程C: 写真ページ（写真のみ）----
                if photo_img:
                    log("\n" + "─" * 40)
                    log(f"📷 Pixta 写真アップロード開始... ({len(photo_img)}件)")
                    photo_result = pixta_upload(
                        files=photo_img,
                        progress_callback=log,
                        skip_submit=self.test_mode,
                        is_photo=True,
                    )
                    log(f"[OK] Pixta 写真 完了: アップロード{photo_result['uploaded']}件 / 審査申請{photo_result['submitted']}件")

                _el = int(_time.time() - _step_start)
                _m, _s = divmod(_el, 60)
                _t = f"{_m}分{_s}秒" if _m > 0 else f"{_s}秒"
                def on_pixta_done(_t=_t):
                    self._log("\n✓ 工程4 Pixta 完了！", "success")
                    self._enable_btn(self.pixta_btn, bg="#b5338a")
                    self._show_topmost_popup("工程4 完了", f"Pixta アップロード完了！\n処理時間: {_t}")
                self.root.after(0, on_pixta_done)
            except Exception as e:
                def on_pixta_err(err=str(e)):
                    self._log(f"\n[NG] Pixtaエラー: {err}", "error")
                    self._enable_btn(self.pixta_btn, bg="#b5338a")
                    self._show_topmost_popup("工程4 エラー", f"Pixtaエラー:\n{err}", error=True)
                self.root.after(0, on_pixta_err)

        threading.Thread(target=run, daemon=True).start()

    def _move_files(self):
        """工程5: ファイル移動（独立実行可能）"""
        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showerror("エラー", "素材フォルダを指定してください。")
            return

        if not messagebox.askyesno("確認", "各サイトへのアップロードは完了しましたか？\nOKを押すとファイルが移動されます。"):
            return

        from pathlib import Path as _Path
        from stock_tagger import get_upload_targets, UPLOAD_VIDEO_EXTENSIONS, move_processed_files as _move

        # last_resultsがあればそれを使い、なければフォルダ内の全対象ファイルをリストアップ
        if self.last_results or self.last_photo_results or self.last_ai_results:
            move_results = list(self.last_results) + list(self.last_photo_results) + list(self.last_ai_results)
        else:
            all_files = list(_Path(folder).iterdir())
            move_results = [{"original_path": str(f)} for f in all_files if f.is_file() and f.suffix.lower() not in {".csv", ".json", ".txt"}]

        move_folder = self.last_folder if self.last_folder else folder

        self._disable_btn(self.move_btn)
        self._log("\n📦 ファイル移動中...", "info")

        def run_move():
            log = lambda m: self.root.after(0, lambda msg=m: self._log(msg))
            try:
                result = _move(
                    move_results, move_folder,
                    lambda m: self.root.after(0, lambda msg=m: self._log(msg))
                )
                # Vectorサブフォルダも移動
                vec_move = move_vector_subfolders(
                    move_folder,
                    lambda m: self.root.after(0, lambda msg=m: self._log(msg))
                )

                def on_done():
                    total_moved = result['moved'] + vec_move['moved']
                    self._log(f"\n✓ 工程5完了！ {total_moved}件を移動しました（通常:{result['moved']} / Vector:{vec_move['moved']}）", "success")
                    self._log(f"  移動先: {result['dest_folder']}", "info")
                    if result["errors"]:
                        self._log(f"  移動失敗: {', '.join(result['errors'])}", "error")
                    if vec_move["errors"]:
                        self._log(f"  Vectorフォルダ移動失敗: {', '.join(vec_move['errors'])}", "error")
                    self.last_results = []
                    self.last_photo_results = []
                    self.last_ai_results = []
                    self.last_folder = ""
                    self._enable_btn(self.move_btn, bg="#0f3460")
                self.root.after(0, on_done)
            except Exception as e:
                self.root.after(0, lambda err=str(e): (
                    self._log(f"✗ エラー: {err}", "error"),
                    self._enable_btn(self.move_btn, bg="#0f3460")
                ))

        threading.Thread(target=run_move, daemon=True).start()

    def _show_video_panel(self, video_results: list):
        """動画用Pixta日本語コピーパネルをメイン画面下部に表示"""
        for widget in self.video_panel_frame.winfo_children():
            widget.destroy()

        # outerフレーム（Canvas含む）を表示
        self.video_panel_outer.pack(fill=tk.X, pady=(8, 0))

        tk.Label(self.video_panel_frame,
            text="📹 Pixta動画用 タイトル・タグ（日本語）",
            bg="#1a1a2e", fg="#64ffda",
            font=("BIZ UDゴシック", 10, "bold"),
            anchor="w").pack(fill=tk.X, pady=(4, 6))

        def copy_to_clipboard(text: str, btn: tk.Button):
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            original = btn["text"]
            btn.config(text="✓ コピー完了", bg="#a6e3a1", fg="#1a1a2e")
            self.root.after(1500, lambda: btn.config(text=original, bg="#e94560", fg="#ffffff"))

        for r in video_results:
            filename = r.get("filename", "")
            title_ja = r.get("pixta_title_ja", "")
            keywords_raw = r.get("pixta_keywords_ja", "")
            keywords_list = [k.strip() for k in keywords_raw.split(",") if k.strip()]
            keywords_50 = ",".join(keywords_list[:50])

            card = tk.Frame(self.video_panel_frame, bg="#16213e", padx=10, pady=8)
            card.pack(fill=tk.X, pady=3)

            # ファイル名
            tk.Label(card, text=f"📹 {filename}",
                bg="#16213e", fg="#e94560",
                font=("BIZ UDゴシック", 9, "bold"),
                anchor="w").pack(fill=tk.X, pady=(0, 6))

            # タイトル行：ボタン固定、テキストは省略
            title_row = tk.Frame(card, bg="#16213e")
            title_row.pack(fill=tk.X, pady=(0, 4))

            title_btn = tk.Button(title_row, text="📋 タイトルをコピー",
                bg="#e94560", fg="#ffffff",
                font=("BIZ UDゴシック", 9, "bold"),
                relief="flat", cursor="hand2", padx=12, pady=4)
            title_btn.config(command=lambda t=title_ja, b=title_btn: copy_to_clipboard(t, b))
            title_btn.pack(side=tk.LEFT)

            tk.Label(title_row,
                text=title_ja[:40] + ("..." if len(title_ja) > 40 else ""),
                bg="#16213e", fg="#8892b0",
                font=("BIZ UDゴシック", 8),
                anchor="w").pack(side=tk.LEFT, padx=(10, 0))

            # タグ行：ボタン固定、テキストは省略
            tag_row = tk.Frame(card, bg="#16213e")
            tag_row.pack(fill=tk.X)

            tag_btn = tk.Button(tag_row, text=f"📋 タグをコピー（{len(keywords_list[:50])}個）",
                bg="#e94560", fg="#ffffff",
                font=("BIZ UDゴシック", 9, "bold"),
                relief="flat", cursor="hand2", padx=12, pady=4)
            tag_btn.config(command=lambda t=keywords_50, b=tag_btn: copy_to_clipboard(t, b))
            tag_btn.pack(side=tk.LEFT)

            tk.Label(tag_row,
                text=keywords_50[:40] + ("..." if len(keywords_50) > 40 else ""),
                bg="#16213e", fg="#8892b0",
                font=("BIZ UDゴシック", 8),
                anchor="w").pack(side=tk.LEFT, padx=(10, 0))

# ============================================================
# 起動
# ============================================================

def main():
    root = tk.Tk()
    app = StockTaggerApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()