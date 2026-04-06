#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock Media Auto Tagger - メイン処理モジュール
"""

import os
import re
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path.home() / ".stock-auto-tagger" / ".env")
import csv
import json
import time
import shutil
import base64
import struct
import mimetypes
import tempfile
from datetime import datetime

# ============================================================
# 定数
# ============================================================
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tiff", ".tif", ".bmp", ".svg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

ADOBE_CATEGORIES = [
    "動物", "建物・建築", "ビジネス", "飲み物", "環境", "感情と情緒",
    "食べ物", "グラフィック素材", "趣味とレジャー", "産業", "風景",
    "ライフスタイル", "人物", "植物・花", "宗教・文化", "科学",
    "社会問題", "スポーツ", "テクノロジー", "交通手段", "旅行"
]

SHUTTERSTOCK_CATEGORIES = [
    "動物・野生生物", "アート", "背景・テクスチャ", "建物・都市",
    "ビジネス・金融", "教育", "食べ物・飲み物", "ヘルスケア・医療",
    "年中行事・ホリデー", "産業・工業", "自然", "物",
    "人物", "宗教", "科学", "アイコン・記号・標識",
    "スポーツ・娯楽", "テクノロジー", "交通"
]

ADOBE_TO_SHUTTERSTOCK = {
    "動物":         ("動物・野生生物", "自然"),
    "建物・建築":   ("建物・都市", "アート"),
    "ビジネス":     ("ビジネス・金融", "人物"),
    "飲み物":       ("食べ物・飲み物", "物"),
    "環境":         ("自然", "背景・テクスチャ"),
    "感情と情緒":   ("人物", "アート"),
    "食べ物":       ("食べ物・飲み物", "物"),
    "グラフィック素材": ("アート", "背景・テクスチャ"),
    "趣味とレジャー": ("スポーツ・娯楽", "人物"),
    "産業":         ("産業・工業", "テクノロジー"),
    "風景":         ("自然", "背景・テクスチャ"),
    "ライフスタイル": ("人物", "スポーツ・娯楽"),
    "人物":         ("人物", "アート"),
    "植物・花":     ("自然", "背景・テクスチャ"),
    "宗教・文化":   ("宗教", "人物"),
    "科学":         ("科学", "テクノロジー"),
    "社会問題":     ("人物", "教育"),
    "スポーツ":     ("スポーツ・娯楽", "人物"),
    "テクノロジー": ("テクノロジー", "科学"),
    "交通手段":     ("交通", "産業・工業"),
    "旅行":         ("自然", "建物・都市"),
}

# ============================================================
# 設定ファイル読み込み
# ============================================================

CONFIG_FILE = Path(__file__).parent / "config.json"

def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {"input_folder": "", "variation_folder": "", "backup_folder": ""}
    # APIキーは.envからのみ読み込む（config.jsonには保存しない）
    config["api_key"] = os.environ.get("GEMINI_API_KEY", "")
    return config


def _get_config_value(key: str, default: str = "") -> str:
    return load_config().get(key, default)

# ============================================================
# プロンプト定義（外部ファイルから読み込み）
# ============================================================

PROMPTS_DIR = Path(__file__).parent / "prompts"

def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")

IMAGE_PROMPT = _load_prompt("image_prompt.txt")
VIDEO_PROMPT = _load_prompt("video_prompt.txt")

# ============================================================
# Gemini API（直接REST呼び出し）
# ============================================================

GEMINI_MODEL = "gemini-3-flash-preview"
GEMINI_MODEL_LITE = "gemini-2.5-flash-lite"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

def call_gemini_api(api_key: str, payload: dict, model: str = None) -> str:
    """Gemini APIを直接呼び出してテキストを返す（429/5xx時は指数バックオフでリトライ）"""
    import urllib.request
    import urllib.error

    use_model = model or GEMINI_MODEL
    url = f"{GEMINI_API_BASE}/{use_model}:generateContent?key={api_key}"
    data = json.dumps(payload).encode("utf-8")

    for attempt in range(7):
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req) as res:
                result = json.loads(res.read().decode("utf-8"))
            time.sleep(6)  # レート制限対策: 無料枠10RPMに収まるよう6秒間隔
            return result["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 6:
                wait = min(2 ** attempt * 2, 60)
                time.sleep(wait)
                continue
            raise


def upload_file_to_gemini(file_path: Path, api_key: str, progress_callback=None, wait_active=True) -> str:
    """File APIでファイルをアップロードしてfileUriを返す（画像・動画両対応）"""
    import urllib.request
    import urllib.error

    if progress_callback:
        progress_callback(f"  アップロード中: {file_path.name}")

    mime_type, _ = mimetypes.guess_type(str(file_path))
    if not mime_type:
        mime_type = "video/mp4"

    file_size = file_path.stat().st_size

    start_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"
    start_headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json"
    }
    start_body = json.dumps({"file": {"display_name": file_path.name}}).encode("utf-8")

    req = urllib.request.Request(start_url, data=start_body, headers=start_headers, method="POST")
    with urllib.request.urlopen(req) as res:
        upload_url = res.headers.get("X-Goog-Upload-URL")

    with open(file_path, "rb") as f:
        file_data = f.read()

    upload_req = urllib.request.Request(
        upload_url,
        data=file_data,
        headers={
            "Content-Length": str(file_size),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize"
        },
        method="POST"
    )
    with urllib.request.urlopen(upload_req) as res:
        file_info = json.loads(res.read().decode("utf-8"))

    file_uri = file_info["file"]["uri"]
    file_name = file_info["file"]["name"]

    if wait_active:
        # 動画はACTIVEになるまで待機が必要
        max_wait = 120
        waited = 0
        while True:
            time.sleep(3)
            waited += 3
            if progress_callback:
                progress_callback(f"  動画処理中... ({waited}秒)")

            status_url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}"
            status_req = urllib.request.Request(status_url)
            with urllib.request.urlopen(status_req) as res:
                status_info = json.loads(res.read().decode("utf-8"))

            state = status_info.get("state", "")
            if state == "ACTIVE":
                break
            elif state == "FAILED":
                raise ValueError("ファイルのアップロードに失敗しました")
            if waited >= max_wait:
                raise TimeoutError("ファイル処理がタイムアウトしました")

    return file_uri, file_name, mime_type


def delete_gemini_file(file_name: str, api_key: str):
    """アップロードしたファイルを削除"""
    import urllib.request
    url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        urllib.request.urlopen(req)
    except:
        pass


def detect_png_background(file_path: Path) -> dict:
    """numpyでPNGの背景情報を高速検出"""
    from PIL import Image
    import numpy as np
    result = {"has_transparent": False, "has_black": False}
    try:
        img = Image.open(file_path)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            arr = np.array(img.convert("RGBA"))
            result["has_transparent"] = bool((arr[:, :, 3] == 0).any())
        arr_rgb = np.array(img.convert("RGB"))
        black_mask = (arr_rgb[:, :, 0] < 30) & (arr_rgb[:, :, 1] < 30) & (arr_rgb[:, :, 2] < 30)
        total = arr_rgb.shape[0] * arr_rgb.shape[1]
        if total > 0 and black_mask.sum() / total > 0.1:
            result["has_black"] = True
    except Exception:
        pass
    return result


def build_background_prompt(bg_info: dict) -> str:
    """背景情報をプロンプト追記用テキストに変換（透過がある場合のみ追記）"""
    if not bg_info["has_transparent"]:
        return ""
    lines = [
        "\n##【この画像の背景情報（必ず反映すること）】",
        "- この画像は透明背景（アルファチャンネルあり）の素材です",
        "- adobe_title_en に 'transparent background' を含めること",
        "- pixta_title_ja に「透明背景」を含めること",
        "- adobe_keywords_en に 'transparent background' を追加",
        "- pixta_keywords_ja に「透明背景」「切り抜き」を追加",
    ]
    return "\n".join(lines)


def analyze_image(file_path: Path, api_key: str) -> dict:
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if not mime_type:
        mime_type = "image/jpeg"

    with open(file_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    prompt = IMAGE_PROMPT
    if file_path.suffix.lower() == ".png":
        bg_info = detect_png_background(file_path)
        bg_prompt = build_background_prompt(bg_info)
        if bg_prompt:
            prompt = IMAGE_PROMPT + bg_prompt

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": image_data}}
            ]
        }],
        "generationConfig": {"responseMimeType": "application/json"}
    }

    text = call_gemini_api(api_key, payload)
    return parse_json_response(text)


BATCH_MAX = 10  # バッチ処理の最大枚数


def estimate_api_requests(input_folder: str, variation_folder: str = "") -> dict:
    """素材フォルダ＋バリエーションフォルダ内のファイル数からAPIリクエスト数を概算する"""
    import math
    input_path = Path(input_folder)
    if not input_path.exists():
        return {"images": 0, "videos": 0, "vectors": 0,
                "image_requests": 0, "video_requests": 0, "vector_requests": 0,
                "total_requests": 0}

    # 通常 + 写真 + AI画像/動画を集計
    image_count = 0
    video_count = 0
    scan_dirs = [input_path]
    ai_folder = input_path / "AI"
    photo_folder = input_path / "Photo"
    if ai_folder.exists():
        scan_dirs.append(ai_folder)
    if photo_folder.exists():
        scan_dirs.append(photo_folder)

    # バリエーションフォルダも集計対象に含める
    if variation_folder:
        var_path = Path(variation_folder)
        if var_path.exists():
            for d in var_path.iterdir():
                if d.is_dir() and d.name != "movie":
                    scan_dirs.append(d)
            movie_dir = var_path / "movie"
            if movie_dir.exists():
                for d in movie_dir.iterdir():
                    if d.is_dir():
                        scan_dirs.append(d)

    for d in scan_dirs:
        for f in d.iterdir():
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext in VIDEO_EXTENSIONS:
                video_count += 1
            elif ext in IMAGE_EXTENSIONS:
                image_count += 1

    # ベクター（Vectorサブフォルダ数 = PNG数）
    vector_count = 0
    vector_path = input_path / "Vector"
    if vector_path.exists():
        vector_count = sum(1 for d in vector_path.iterdir()
                          if d.is_dir() and list(d.glob("*.png")))

    image_requests = math.ceil(image_count / BATCH_MAX) if image_count else 0
    vector_requests = math.ceil(vector_count / BATCH_MAX) if vector_count else 0
    video_requests = video_count  # 動画は工程1で解析、Pixta工程4は結果を使い回し

    return {
        "images": image_count,
        "videos": video_count,
        "vectors": vector_count,
        "image_requests": image_requests,
        "video_requests": video_requests,
        "vector_requests": vector_requests,
        "total_requests": image_requests + video_requests + vector_requests,
    }


# ファイルサイズ上限（MB）
ADOBE_MAX_IMAGE_MB = 45
PIXTA_MAX_PNG_MB = 30
PIXTA_MAX_UPLOAD_COUNT = 50
SHUTTERSTOCK_MAX_IMAGE_MB = 50


def validate_upload_files(input_folder: str) -> list:
    """素材フォルダ内のファイルがアップロード制限に違反していないかチェックする。
    戻り値: 警告メッセージのリスト（空なら問題なし）"""
    input_path = Path(input_folder)
    if not input_path.exists():
        return []

    warnings = []

    # スキャン対象ディレクトリ
    scan_dirs = [input_path]
    ai_folder = input_path / "AI"
    photo_folder = input_path / "Photo"
    if ai_folder.exists():
        scan_dirs.append(ai_folder)
    if photo_folder.exists():
        scan_dirs.append(photo_folder)

    image_count = 0
    for d in scan_dirs:
        for f in d.iterdir():
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext not in IMAGE_EXTENSIONS:
                continue
            image_count += 1
            size_mb = f.stat().st_size / (1024 * 1024)

            if size_mb > ADOBE_MAX_IMAGE_MB:
                warnings.append(f"[Adobe] {f.name} ({size_mb:.1f}MB) → 上限{ADOBE_MAX_IMAGE_MB}MBを超過")
            if ext == ".png" and size_mb > PIXTA_MAX_PNG_MB:
                warnings.append(f"[Pixta] {f.name} ({size_mb:.1f}MB) → PNG上限{PIXTA_MAX_PNG_MB}MBを超過")
            if size_mb > SHUTTERSTOCK_MAX_IMAGE_MB:
                warnings.append(f"[Shutterstock] {f.name} ({size_mb:.1f}MB) → 上限{SHUTTERSTOCK_MAX_IMAGE_MB}MBを超過")

    if image_count > PIXTA_MAX_UPLOAD_COUNT:
        warnings.append(f"[Pixta] 画像{image_count}枚 → 一度にアップロードできるのは{PIXTA_MAX_UPLOAD_COUNT}枚まで")

    return warnings


def _build_batch_prompt(base_prompt: str, filenames: list, bg_prompt: str = "",
                        per_file_bg_prompts: list = None) -> str:
    """バッチ用プロンプトを構築（ファイル名順でJSON配列を返す指示を追加）

    per_file_bg_prompts: 画像ごとの背景情報リスト。指定時はbg_promptより優先。
    """
    if per_file_bg_prompts:
        # 画像ごとに背景情報を付与
        file_lines = []
        for i, name in enumerate(filenames):
            bg = per_file_bg_prompts[i] if i < len(per_file_bg_prompts) else ""
            if bg:
                file_lines.append(f"  {i+1}. {name} — 背景情報: {bg}")
            else:
                file_lines.append(f"  {i+1}. {name} — 背景情報: なし（通常背景）")
        file_list = "\n".join(file_lines)
    else:
        file_list = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(filenames))
    batch_instruction = (
        f"\n\n##【バッチ処理指示】\n"
        f"以下の{len(filenames)}枚の画像が順番に添付されています。\n"
        f"{file_list}\n\n"
        f"各画像に対して個別にメタデータを生成し、上記の順番通りにJSONオブジェクトの配列で返してください。\n"
        f"必ず {len(filenames)} 個のJSONオブジェクトを含む配列を返すこと。"
    )
    if per_file_bg_prompts:
        batch_instruction += (
            "\n\n各画像の背景情報に従って、タイトルとキーワードに背景に関する情報を反映してください。"
            "\n- 透明背景の画像: adobe_title_enに'transparent background'を含め、pixta_title_jaに「透明背景」を含めること。adobe_keywords_enに'transparent background'、pixta_keywords_jaに「透明背景」「切り抜き」を追加。"
            "\n- 通常背景の画像: 背景に関する特別な記述は不要。"
        )
        return base_prompt + batch_instruction
    return base_prompt + bg_prompt + batch_instruction


def analyze_images_batch(file_paths: list, api_key: str, bg_prompt: str = "",
                         progress_callback=None, per_file_bg_prompts: list = None) -> list:
    """複数画像をFile APIでアップロードし、1リクエストでバッチ解析する"""
    uploaded_files = []
    filenames = [f.name for f in file_paths]

    try:
        # 1. 全画像をFile APIでアップロード
        for file_path in file_paths:
            if progress_callback:
                progress_callback(f"  アップロード中: {file_path.name}")
            file_uri, file_name, mime_type = upload_file_to_gemini(
                file_path, api_key, wait_active=False
            )
            uploaded_files.append((file_uri, file_name, mime_type))

        # 2. バッチ用プロンプト構築
        prompt = _build_batch_prompt(IMAGE_PROMPT, filenames, bg_prompt,
                                     per_file_bg_prompts=per_file_bg_prompts)

        # 3. 全file_uriをまとめて1リクエスト
        parts = [{"text": prompt}]
        for file_uri, _, mime_type in uploaded_files:
            parts.append({"file_data": {"mime_type": mime_type, "file_uri": file_uri}})

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json"}
        }

        if progress_callback:
            progress_callback(f"  Gemini解析中（{len(file_paths)}枚バッチ）...")

        text = call_gemini_api(api_key, payload)

        # 4. レスポンスをパース（配列を期待）
        parsed = parse_json_response(text)
        if isinstance(parsed, dict):
            # 1枚だけの場合は辞書で返ることがある
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise ValueError(f"バッチレスポンスが配列ではありません: {type(parsed)}")

        return parsed

    finally:
        # 5. アップロードファイルを一括削除
        for _, file_name, _ in uploaded_files:
            delete_gemini_file(file_name, api_key)


def analyze_video(file_path: Path, api_key: str, progress_callback=None, filename_hint: str = "") -> dict:
    file_uri, file_name, mime_type = upload_file_to_gemini(file_path, api_key, progress_callback)

    prompt = VIDEO_PROMPT
    if filename_hint:
        prompt += f"\n\nファイル名（参考情報）: {filename_hint}\nこのファイル名を手がかりに、動画の内容に正確に合ったタイトル・タグを生成してください。"

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"file_data": {"mime_type": mime_type, "file_uri": file_uri}}
            ]
        }],
        "generationConfig": {"responseMimeType": "application/json"}
    }

    try:
        text = call_gemini_api(api_key, payload)
    finally:
        delete_gemini_file(file_name, api_key)

    return parse_json_response(text)


def parse_json_response(text: str) -> dict:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    return json.loads(text)

# ============================================================
# CSV出力
# ============================================================

def write_adobe_stock_csv(results: list, output_path: Path, append: bool = False):
    adobe_cat_map = {
        "動物": "1", "建物・建築": "2", "ビジネス": "3", "飲み物": "4",
        "環境": "5", "感情と情緒": "6", "食べ物": "7", "グラフィック素材": "8",
        "趣味とレジャー": "9", "産業": "10", "風景": "11", "ライフスタイル": "12",
        "人物": "13", "植物・花": "14", "宗教・文化": "15", "科学": "16",
        "社会問題": "17", "スポーツ": "18", "テクノロジー": "19",
        "交通手段": "20", "旅行": "21",
    }
    mode = "a" if append else "w"
    with open(output_path, mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not append:
            writer.writerow(["Filename", "Title", "Keywords", "Category", "Releases"])
        for r in results:
            cat_ja = r.get("adobe_category", "")
            cat_code = adobe_cat_map.get(cat_ja, "8")
            keywords_raw = r.get("adobe_keywords_en", "")
            keywords_list = [k.strip() for k in keywords_raw.split(",") if k.strip()]
            keywords_49 = ", ".join(keywords_list[:49])
            writer.writerow([
                r["filename"],
                r.get("adobe_title_en", "")[:200],
                keywords_49,
                cat_code,
                ""
            ])


def write_shutterstock_csv(results: list, output_path: Path, append: bool = False):
    ss_cat_map = {
        "動物・野生生物": "Animals/Wildlife", "アート": "Art",
        "背景・テクスチャ": "Backgrounds/Textures", "建物・都市": "Buildings/Landmarks",
        "ビジネス・金融": "Business/Finance", "教育": "Education",
        "食べ物・飲み物": "Food and Drink", "ヘルスケア・医療": "Healthcare/Medical",
        "年中行事・ホリデー": "Holidays", "産業・工業": "Industrial",
        "自然": "Nature", "物": "Objects", "人物": "People", "宗教": "Religion",
        "科学": "Science", "アイコン・記号・標識": "Signs/Symbols",
        "スポーツ・娯楽": "Sports/Recreation", "テクノロジー": "Technology",
        "交通": "Transportation", "インテリア": "Interiors",
        "その他": "Miscellaneous", "風景・アウトドア": "Parks/Outdoor",
        "レトロ・ビンテージ": "Vintage", "抽象": "Abstract",
        "美容・ファッション": "Beauty/Fashion", "有名人": "Celebrities",
    }
    mode = "a" if append else "w"
    with open(output_path, mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not append:
            writer.writerow(["Filename", "Description", "Keywords", "Categories", "Editorial", "Mature content", "illustration"])
        for r in results:
            if Path(r.get("filename", "")).suffix.lower() == ".png":
                continue
            file_type = r.get("file_type", "image")
            is_illustration = "no" if file_type == "video" else "yes"
            cat1_ja = r.get("shutterstock_category1", "")
            cat2_ja = r.get("shutterstock_category2", "")
            if not cat1_ja:
                fallback = ADOBE_TO_SHUTTERSTOCK.get(r.get("adobe_category", ""), ("自然", "アート"))
                cat1_ja, cat2_ja = fallback
            cat1_en = ss_cat_map.get(cat1_ja, "miscellaneous")
            cat2_en = ss_cat_map.get(cat2_ja, "")
            categories = f"{cat1_en},{cat2_en}" if cat2_en else cat1_en
            keywords_raw = r.get("shutterstock_keywords_en", "")
            keywords_list = [k.strip() for k in keywords_raw.split(",") if k.strip()]
            keywords_filtered = filter_keywords(keywords_list, 50)
            keywords_50 = ",".join(keywords_filtered)
            writer.writerow([
                r["filename"],
                r.get("shutterstock_title_en", "")[:200],
                keywords_50,
                categories,
                "no", "no",
                is_illustration
            ])


def filter_keywords(keywords: list, max_count: int = 50) -> list:
    if len(keywords) <= max_count:
        return keywords
    LOW_PRIORITY = {
        "beautiful", "amazing", "stunning", "gorgeous", "wonderful", "lovely",
        "great", "nice", "good", "best", "perfect", "elegant", "creative",
        "modern", "simple", "various", "many", "different", "unique", "special",
        "concept", "design", "style", "theme", "idea", "background", "texture",
        "image", "photo", "picture", "illustration", "graphic", "visual",
        "colorful", "vibrant", "bright", "dark", "light", "white", "black",
        "fresh", "clean", "smooth", "soft", "sharp", "clear", "abstract",
        "digital", "art", "artistic", "decorative", "ornamental",
    }
    def score(kw: str) -> int:
        kw_lower = kw.lower().strip()
        if " " in kw_lower: return 3
        if kw_lower in LOW_PRIORITY: return 0
        if len(kw_lower) <= 2: return 0
        return 2
    sorted_keywords = sorted(keywords, key=score, reverse=True)
    return sorted_keywords[:max_count]

# ============================================================
# 共通 XMP 文字列生成
# ============================================================

def _make_xmp_string(title: str, keywords: list) -> str:
    keywords_xml = "\n          ".join(f"<rdf:li>{kw}</rdf:li>" for kw in keywords)
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '    <rdf:Description rdf:about=""\n'
        '      xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        '      <dc:title>\n'
        '        <rdf:Alt><rdf:li xml:lang="x-default">' + title + '</rdf:li></rdf:Alt>\n'
        '      </dc:title>\n'
        '      <dc:subject>\n'
        '        <rdf:Bag>\n'
        '          ' + keywords_xml + '\n'
        '        </rdf:Bag>\n'
        '      </dc:subject>\n'
        '    </rdf:Description>\n'
        '  </rdf:RDF>\n'
        '</x:xmpmeta>\n'
        '<?xpacket end="w"?>'
    )


# ============================================================
# PNG XMP メタデータ埋め込み
# ============================================================

def embed_png_xmp(file_path: Path, title: str, keywords: list):
    import zlib

    def read_chunks(png_data: bytes):
        chunks = []
        i = 8
        while i < len(png_data):
            length = struct.unpack(">I", png_data[i:i+4])[0]
            chunk_type = png_data[i+4:i+8]
            chunk_data = png_data[i+8:i+8+length]
            chunks.append((chunk_type, chunk_data))
            i += 12 + length
        return chunks

    def make_chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

    xmp_str = _make_xmp_string(title, keywords)

    with open(file_path, "rb") as f:
        png_data = f.read()

    chunks = read_chunks(png_data)
    chunks = [(t, d) for t, d in chunks if not (t == b"iTXt" and b"XML:com.adobe.xmp" in d)]

    keyword_bytes = b"XML:com.adobe.xmp"
    xmp_bytes = xmp_str.encode("utf-8")
    itxt_data = keyword_bytes + b"\x00\x00\x00\x00\x00" + xmp_bytes

    new_chunks = []
    inserted = False
    for chunk_type, chunk_data in chunks:
        new_chunks.append((chunk_type, chunk_data))
        if chunk_type == b"IHDR" and not inserted:
            new_chunks.append((b"iTXt", itxt_data))
            inserted = True

    output = png_data[:8]
    for chunk_type, chunk_data in new_chunks:
        output += make_chunk(chunk_type, chunk_data)

    with open(file_path, "wb") as f:
        f.write(output)


# ============================================================
# JPEG IPTC メタデータ埋め込み
# ============================================================

def embed_jpg_iptc(file_path: Path, title: str, keywords: list):
    from PIL import Image
    import io

    def make_iptc_dataset(record: int, dataset: int, value: bytes) -> bytes:
        return b"\x1c" + bytes([record, dataset]) + struct.pack(">H", len(value)) + value

    iptc_data = bytearray()
    # UTF-8文字コード宣言（IPTC 1:90 Coded Character Set = ESC %G）
    iptc_data += make_iptc_dataset(1, 90, b"\x1b\x25\x47")
    iptc_data += make_iptc_dataset(2, 5, title.encode("utf-8"))
    for kw in keywords:
        iptc_data += make_iptc_dataset(2, 25, kw.encode("utf-8"))

    photoshop_header = b"Photoshop 3.0\x00"
    bim_type = b"8BIM"
    resource_id = struct.pack(">H", 0x0404)
    pascal_string = b"\x00\x00"
    iptc_block = bim_type + resource_id + pascal_string + struct.pack(">I", len(iptc_data)) + bytes(iptc_data)
    if len(iptc_block) % 2 != 0:
        iptc_block += b"\x00"

    app13_payload = photoshop_header + iptc_block

    with open(file_path, "rb") as f:
        jpeg_data = f.read()

    segments = []
    i = 2
    while i < len(jpeg_data):
        if jpeg_data[i] != 0xFF:
            break
        marker = jpeg_data[i:i+2]
        if marker == b"\xFF\xDA":
            segments.append(jpeg_data[i:])
            break
        seg_len = struct.unpack(">H", jpeg_data[i+2:i+4])[0]
        seg_data = jpeg_data[i+4:i+2+seg_len]
        if marker != b"\xFF\xED":
            segments.append(marker + struct.pack(">H", seg_len) + seg_data)
        i += 2 + seg_len

    app13_marker = b"\xFF\xED"
    app13_segment = app13_marker + struct.pack(">H", len(app13_payload) + 2) + app13_payload

    output = b"\xFF\xD8" + app13_segment
    for seg in segments:
        output += seg

    with open(file_path, "wb") as f:
        f.write(output)


# ============================================================
# Pixta メタデータ埋め込み（JPG=IPTC, PNG=XMP）
# ============================================================

def embed_pixta_metadata(results: list, progress_callback=None) -> int:
    embedded = 0
    for r in results:
        if r.get("file_type") == "video":
            continue

        file_path = Path(r["original_path"])
        if not file_path.exists():
            continue

        ext = file_path.suffix.lower()
        title = r.get("pixta_title_ja", "")[:50]
        keywords_str = r.get("pixta_keywords_ja", "")
        keywords_list = [k.strip() for k in keywords_str.split(",") if k.strip()][:50]

        if ext == ".jpg" or ext == ".jpeg":
            embed_fn = embed_jpg_iptc
        elif ext == ".png":
            embed_fn = embed_png_xmp
        elif ext == ".eps":
            embed_fn = embed_eps_xmp
        else:
            continue

        try:
            embed_fn(file_path, title, keywords_list)
            embedded += 1
            if progress_callback:
                progress_callback(f"  埋め込み完了: {file_path.name}")
        except Exception as e:
            if progress_callback:
                progress_callback(f"  メタデータ埋め込み失敗: {file_path.name} - {e}")

    return embedded


# ============================================================
# EPS XMP メタデータ埋め込み
# ============================================================

def embed_eps_xmp(eps_path: Path, title: str, keywords: list):
    """既存Illustrator XMP構造を維持し dc:title と dc:subject だけ置換/挿入。バイナリEPSヘッダー対応"""
    import re as _re

    with open(eps_path, "rb") as f:
        raw = f.read()

    # バイナリEPSヘッダー対応
    binary_header = None
    if raw[:4] == b"\xc5\xd0\xd3\xc6":
        ps_offset = struct.unpack("<I", raw[4:8])[0]
        ps_length = struct.unpack("<I", raw[8:12])[0]
        binary_header = raw[:ps_offset]
        ps_data = raw[ps_offset:ps_offset + ps_length]
    else:
        ps_data = raw

    text = ps_data.decode("latin-1")

    # バックアップ
    bak = eps_path.with_suffix(eps_path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(str(eps_path), str(bak))

    # dc:title 置換
    title_pattern = r'(<dc:title>\s*<rdf:Alt>\s*<rdf:li[^>]*>)(.*?)(</rdf:li>\s*</rdf:Alt>\s*</dc:title>)'
    if _re.search(title_pattern, text, _re.DOTALL):
        text = _re.sub(title_pattern, lambda m: m.group(1) + title + m.group(3), text, flags=_re.DOTALL)
    else:
        insert_point = text.find("</rdf:Description>")
        if insert_point != -1:
            snippet = (
                '      <dc:title>\n'
                '        <rdf:Alt><rdf:li xml:lang="x-default">' + title + '</rdf:li></rdf:Alt>\n'
                '      </dc:title>\n'
            )
            text = text[:insert_point] + snippet + text[insert_point:]

    # dc:subject 置換
    subject_pattern = r'<dc:subject>\s*<rdf:Bag>.*?</rdf:Bag>\s*</dc:subject>'
    keywords_xml = "\n            ".join(f"<rdf:li>{kw}</rdf:li>" for kw in keywords)
    new_subject = (
        '<dc:subject>\n'
        '          <rdf:Bag>\n'
        '            ' + keywords_xml + '\n'
        '          </rdf:Bag>\n'
        '        </dc:subject>'
    )
    if _re.search(subject_pattern, text, _re.DOTALL):
        text = _re.sub(subject_pattern, new_subject, text, flags=_re.DOTALL)
    else:
        insert_point = text.find("</rdf:Description>")
        if insert_point != -1:
            text = text[:insert_point] + '      ' + new_subject + '\n' + text[insert_point:]

    # XMP部分はUTF-8を含むのでlatin-1では不可。
    # XMPパケット内だけUTF-8エンコードし、それ以外はlatin-1のまま保持する。
    xmp_start_marker = '<?xpacket begin='
    xmp_end_marker = '<?xpacket end='
    xmp_start = text.find(xmp_start_marker)
    xmp_end = text.find(xmp_end_marker)
    if xmp_start != -1 and xmp_end != -1:
        xmp_end = text.index('?>', xmp_end) + 2  # <?xpacket end="w"?> の末尾
        before = text[:xmp_start].encode("latin-1")
        xmp_part = text[xmp_start:xmp_end].encode("utf-8")
        after = text[xmp_end:].encode("latin-1")
        ps_data_new = before + xmp_part + after
    else:
        ps_data_new = text.encode("latin-1", errors="xmlcharrefreplace")

    if binary_header:
        new_ps_length = len(ps_data_new)
        header = bytearray(binary_header)
        ps_start = struct.unpack("<I", header[4:8])[0]
        header[8:12] = struct.pack("<I", new_ps_length)

        # WMF/TIFFプレビューのオフセットをPS長変更分だけ調整して保持
        old_ps_length = struct.unpack("<I", binary_header[8:12])[0]
        size_diff = new_ps_length - old_ps_length

        wmf_offset = struct.unpack("<I", header[12:16])[0]
        wmf_length = struct.unpack("<I", header[16:20])[0]
        tiff_offset = struct.unpack("<I", header[20:24])[0]
        tiff_length = struct.unpack("<I", header[24:28])[0]

        # プレビューデータを元ファイルから読み出す
        wmf_data = raw[wmf_offset:wmf_offset + wmf_length] if wmf_offset and wmf_length else b""
        tiff_data = raw[tiff_offset:tiff_offset + tiff_length] if tiff_offset and tiff_length else b""

        if wmf_offset and wmf_length:
            header[12:16] = struct.pack("<I", wmf_offset + size_diff)
        if tiff_offset and tiff_length:
            header[20:24] = struct.pack("<I", tiff_offset + size_diff)

        with open(eps_path, "wb") as f:
            f.write(bytes(header) + ps_data_new + wmf_data + tiff_data)
    else:
        with open(eps_path, "wb") as f:
            f.write(ps_data_new)


# ============================================================
# フォルダ移動
# ============================================================

def get_destination_folder(base_dir: Path) -> Path:
    now = datetime.now()
    folder_name = now.strftime("%y%m")
    dest = base_dir / folder_name
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def get_backup_folder() -> Path:
    """バックアップ先フォルダを返す（config.json の backup_folder を使用、YYMM形式）"""
    backup_base = _get_config_value("backup_folder", "")
    if not backup_base:
        return None
    backup_path = Path(backup_base)
    now = datetime.now()
    folder_name = now.strftime("%y%m")
    dest = backup_path / folder_name
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def move_to_destination(file_path: Path, dest_folder: Path) -> bool:
    try:
        dst = dest_folder / file_path.name
        if dst.exists():
            stem = dst.stem
            suffix = dst.suffix
            counter = 1
            while dst.exists():
                dst = dest_folder / f"{stem}_{counter}{suffix}"
                counter += 1
        shutil.move(str(file_path), str(dst))
        return True
    except Exception:
        return False


# ============================================================
# ファイル振り分け
# ============================================================

UPLOAD_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi"}


def get_upload_targets(folder: Path, site: str) -> list:
    """
    サイト別にアップロード対象ファイルを返す。
    Shutterstock: JPG・動画のみ（PNGはプラットフォーム非対応）
    Adobe/Pixta: PNG・JPG・動画すべて
    site: 'adobe' | 'shutterstock' | 'pixta'
    """
    files = [f for f in folder.iterdir() if f.is_file()]
    result = []

    for f in sorted(files):
        ext = f.suffix.lower()
        if site == "shutterstock":
            if ext in {".jpg", ".jpeg"} | UPLOAD_VIDEO_EXTENSIONS:
                result.append(f)
        elif site in ("adobe", "pixta"):
            if ext in UPLOAD_VIDEO_EXTENSIONS:
                result.append(f)
            elif ext in {".png", ".jpg", ".jpeg"}:
                result.append(f)

    return result


# ============================================================
# Shutterstock FTPS アップロード
# ============================================================

SHUTTERSTOCK_FTPS_HOST = "ftps.shutterstock.com"

def upload_to_shutterstock(input_folder: str, ftp_user: str, ftp_pass: str,
                            progress_callback=None) -> dict:
    """Shutterstock FTPSへ画像・動画をアップロードする"""
    import ftplib

    input_path = Path(input_folder)
    targets = get_upload_targets(input_path, "shutterstock")

    if not targets:
        if progress_callback:
            progress_callback("Shutterstockにアップロード対象のファイルがありません（JPG・動画のみ対応）")
        return {"uploaded": 0, "errors": [], "skipped": 0}

    uploaded = 0
    skipped = 0
    errors = []

    if progress_callback:
        progress_callback(f"Shutterstock FTPSに接続中... ({SHUTTERSTOCK_FTPS_HOST})")

    try:
        ftp = ftplib.FTP_TLS()
        ftp.connect(SHUTTERSTOCK_FTPS_HOST, 21, timeout=30)
        ftp.auth()
        ftp.login(ftp_user, ftp_pass)
        ftp.prot_p()

        if progress_callback:
            progress_callback(f"接続完了。{len(targets)}ファイルをアップロードします")

        for i, file_path in enumerate(targets, 1):
            if progress_callback:
                progress_callback(f"[{i}/{len(targets)}] アップロード中: {file_path.name}")
            try:
                with open(file_path, "rb") as f:
                    ftp.storbinary(f"STOR {file_path.name}", f)
                uploaded += 1
                if progress_callback:
                    progress_callback(f"  [OK] {file_path.name}")
            except Exception as e:
                errors.append({"filename": file_path.name, "error": str(e)})
                if progress_callback:
                    progress_callback(f"  [NG] {file_path.name}: {e}")

        ftp.quit()

    except Exception as e:
        raise ConnectionError(f"FTPS接続エラー: {e}")

    if progress_callback:
        progress_callback(f"\nShutterstock アップロード完了: {uploaded}件 / エラー: {len(errors)}件")

    return {"uploaded": uploaded, "errors": errors, "skipped": skipped}


# ============================================================
# リネーム処理
# ============================================================

RENAME_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
RENAME_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".wmv", ".webm"}


def _rename_get_keyword_and_colors(api_key: str, images_data: list) -> dict:
    """全画像を1回のAPIコールでキーワードと各画像の色を取得する。
    Returns: {"keyword": str, "colors": [str, ...]}
    """
    parts = []
    for mime, data in images_data:
        parts.append({"inline_data": {"mime_type": mime, "data": data}})
    parts.append({"text": (
        f"These {len(images_data)} images are all color variations of the same stock illustration with a black background.\n"
        "1. Provide a single English keyword (lowercase, no spaces) describing the visual style "
        "(e.g. 'lightstreak', 'lightswoosh', 'starburst').\n"
        "2. For each image (in order), identify the main color of the subject or effect in the foreground. "
        "Ignore the black background. Use a single lowercase English color name "
        "(e.g. red, blue, cyan, pink, green, yellow, white, purple, orange).\n\n"
        "Reply in this exact format (no extra text):\n"
        "keyword: <keyword>\n"
        "colors: <color1>, <color2>, <color3>, ..."
    )})
    response = call_gemini_api(api_key, {"contents": [{"parts": parts}]}, model=GEMINI_MODEL_LITE).strip()
    # パース
    keyword = ""
    colors = []
    for line in response.split("\n"):
        line = line.strip()
        if line.lower().startswith("keyword:"):
            keyword = line.split(":", 1)[1].strip().lower()
        elif line.lower().startswith("colors:"):
            colors = [c.strip().lower() for c in line.split(":", 1)[1].split(",")]
    return {"keyword": keyword, "colors": colors}


def _rename_video_keyword(api_key: str, file_uri: str, mime_type: str) -> str:
    parts = [
        {"file_data": {"mime_type": mime_type, "file_uri": file_uri}},
        {"text": (
            "This is a stock motion graphics video. Provide a single English keyword (lowercase, no spaces) "
            "describing the visual style (e.g. 'lightstreak', 'starburst', 'glowingstar'). "
            "Reply with ONLY the keyword."
        )}
    ]
    return call_gemini_api(api_key, {"contents": [{"parts": parts}]}, model=GEMINI_MODEL_LITE).strip().lower()


def _rename_video_color(api_key: str, file_uri: str, mime_type: str) -> str:
    parts = [
        {"file_data": {"mime_type": mime_type, "file_uri": file_uri}},
        {"text": (
            "This video has a black background. Ignore the black background and identify "
            "the main color of the subject or effect in the foreground. "
            "Reply with a single lowercase English color name only "
            "(e.g. red, blue, cyan, pink, green, yellow, white, purple, orange). "
            "Reply with ONLY the color name."
        )}
    ]
    return call_gemini_api(api_key, {"contents": [{"parts": parts}]}, model=GEMINI_MODEL_LITE).strip().lower()


def rename_variation_folders(api_key: str, output_folder: str, variation_base: str = None,
                              progress_callback=None) -> int:
    """バリエーションフォルダの素材をリネームして出力フォルダに移動する"""
    from send2trash import send2trash as _send2trash

    if not variation_base:
        variation_base = _get_config_value("variation_folder", "")
    base = Path(variation_base)
    output = Path(output_folder)
    today = datetime.now().strftime("%y%m%d")
    total = 0

    # ---- 画像フォルダ (01〜10) ----
    for n in range(1, 11):
        folder_path = base / f"{n:02d}"
        if not folder_path.exists():
            continue

        images = sorted([f for f in folder_path.iterdir()
                         if f.suffix.lower() in RENAME_IMAGE_EXTENSIONS])
        if not images:
            continue

        if progress_callback:
            progress_callback(f"[リネーム] 画像フォルダ {n:02d} ({len(images)}ファイル)")

        images_data = []
        for img in images:
            mime, _ = mimetypes.guess_type(str(img))
            mime = mime or "image/jpeg"
            with open(img, "rb") as f:
                data = base64.b64encode(f.read()).decode("utf-8")
            images_data.append((mime, data, img))

        result = _rename_get_keyword_and_colors(api_key, [(m, d) for m, d, _ in images_data])
        keyword = result["keyword"]
        colors = result["colors"]
        if progress_callback:
            progress_callback(f"  キーワード: {keyword} / 色: {', '.join(colors)}")

        used_names = {}
        for i, (mime, data, img) in enumerate(images_data):
            color = colors[i] if i < len(colors) else "unknown"
            base_name = f"{today}_{keyword}_{color}"
            if base_name in used_names:
                used_names[base_name] += 1
                final_name = f"{base_name}{used_names[base_name]:02d}{img.suffix}"
            else:
                used_names[base_name] = 0
                final_name = f"{base_name}{img.suffix}"

            dest = output / final_name
            shutil.copy2(str(img), str(dest))
            _send2trash(str(img))
            total += 1
            if progress_callback:
                progress_callback(f"  {img.name} → {final_name}")

    # ---- 動画フォルダ (movie/01〜10) ----
    movie_base = base / "movie"
    for n in range(1, 11):
        folder_path = movie_base / f"{n:02d}"
        if not folder_path.exists():
            continue

        videos = sorted([f for f in folder_path.iterdir()
                         if f.suffix.lower() in RENAME_VIDEO_EXTENSIONS])
        if not videos:
            continue

        if progress_callback:
            progress_callback(f"[リネーム] 動画フォルダ movie/{n:02d} ({len(videos)}ファイル)")

        used_names = {}
        for video in videos:
            file_uri, file_name, mime_type = upload_file_to_gemini(video, api_key, progress_callback)
            try:
                keyword = _rename_video_keyword(api_key, file_uri, mime_type)
                color = _rename_video_color(api_key, file_uri, mime_type)
            finally:
                delete_gemini_file(file_name, api_key)

            base_name = f"{today}_{keyword}_{color}"
            if base_name in used_names:
                used_names[base_name] += 1
                final_name = f"{base_name}{used_names[base_name]:02d}{video.suffix}"
            else:
                used_names[base_name] = 0
                final_name = f"{base_name}{video.suffix}"

            dest = output / final_name
            shutil.copy2(str(video), str(dest))
            _send2trash(str(video))
            total += 1
            if progress_callback:
                progress_callback(f"  {video.name} → {final_name}")

    if progress_callback:
        progress_callback(f"\nリネーム完了: {total}件")

    return total


# ============================================================
# メイン処理
# ============================================================

AI_FOLDER_NAME = "AI"
PHOTO_FOLDER_NAME = "Photo"


def get_ai_folder(input_folder: Path) -> Path:
    """AIフォルダのパスを返す"""
    return Path(input_folder) / AI_FOLDER_NAME


def get_ai_files(input_folder: Path) -> list:
    """AIフォルダ内の対象ファイルを返す（空ならリスト空）"""
    ai_folder = get_ai_folder(input_folder)
    if not ai_folder.exists():
        return []
    return [f for f in ai_folder.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS]


def get_photo_folder(input_folder: Path) -> Path:
    """写真フォルダのパスを返す"""
    return Path(input_folder) / PHOTO_FOLDER_NAME


def get_photo_files(input_folder: Path) -> list:
    """写真フォルダ内の対象ファイルを返す（空ならリスト空）"""
    photo_folder = get_photo_folder(input_folder)
    if not photo_folder.exists():
        return []
    return [f for f in photo_folder.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS]


def process_folder(input_folder: str, api_key: str, progress_callback=None, status_callback=None) -> dict:
    start_time = time.time()

    input_path = Path(input_folder)
    if not input_path.exists():
        raise FileNotFoundError(f"フォルダが見つかりません: {input_folder}")

    # 通常素材
    files = [f for f in input_path.iterdir()
             if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS]
    # 写真素材
    photo_files = get_photo_files(input_path)
    # AI素材
    ai_files = get_ai_files(input_path)

    all_files = files + photo_files + ai_files
    vector_subfolders = get_vector_subfolders(input_path)
    if not all_files and not vector_subfolders:
        raise ValueError("対応する素材ファイルが見つかりませんでした")

    total = len(all_files)
    results = []
    photo_results = []
    ai_results = []
    errors = []

    csv_folder = input_path / "csv_output"
    csv_folder.mkdir(exist_ok=True)
    from send2trash import send2trash as _send2trash
    for old_csv in csv_folder.glob("*.csv"):
        _send2trash(str(old_csv))
    old_video_meta = csv_folder / "video_metadata.json"
    if old_video_meta.exists():
        _send2trash(str(old_video_meta))

    base_dir = input_path.parent
    dest_folder = get_destination_folder(base_dir)

    ai_folder_path = get_ai_folder(input_path)
    photo_folder_path = get_photo_folder(input_path)

    # ── 画像・動画を分類し、画像ごとの背景情報を記録 ──
    video_files = []  # (file_path, is_ai, is_photo)
    image_files = []  # (file_path, is_ai, is_photo)
    image_bg_labels = []  # 画像ごとの背景ラベル（プロンプト用）

    for file_path in all_files:
        ext = file_path.suffix.lower()
        is_ai = file_path.parent == ai_folder_path
        is_photo = file_path.parent == photo_folder_path
        file_info = (file_path, is_ai, is_photo)

        if ext in VIDEO_EXTENSIONS:
            video_files.append(file_info)
        else:
            image_files.append(file_info)
            if ext == ".png":
                bg_info = detect_png_background(file_path)
                if bg_info["has_transparent"]:
                    image_bg_labels.append("透明背景（アルファチャンネルあり）")
                else:
                    image_bg_labels.append("")
            else:
                image_bg_labels.append("")

    processed_count = 0

    # ── 画像バッチ処理（背景グループ分けなし、画像ごとに背景情報を付与） ──
    for batch_start in range(0, len(image_files), BATCH_MAX):
        batch = image_files[batch_start:batch_start + BATCH_MAX]
        batch_paths = [info[0] for info in batch]
        batch_bg_labels = image_bg_labels[batch_start:batch_start + len(batch)]

        # per_file_bg_promptsが全て空なら不要
        per_file_bg = batch_bg_labels if any(batch_bg_labels) else None

        if progress_callback:
            names = ", ".join(p.name for p in batch_paths)
            progress_callback(f"[{processed_count + 1}〜{processed_count + len(batch)}/{total}] バッチ解析中: {names}")
        if status_callback:
            status_callback(processed_count + 1, total, f"バッチ {len(batch)}枚")

        try:
            batch_results = analyze_images_batch(
                batch_paths, api_key, "", progress_callback,
                per_file_bg_prompts=per_file_bg
            )

                # レスポンス数が足りない場合はエラー扱い
                if len(batch_results) < len(batch):
                    for j in range(len(batch_results), len(batch)):
                        fp, is_ai, is_photo = batch[j]
                        errors.append({"filename": fp.name, "error": "バッチレスポンスに結果なし"})

                for j, metadata in enumerate(batch_results):
                    if j >= len(batch):
                        break
                    fp, is_ai, is_photo = batch[j]
                    metadata["filename"] = fp.name
                    metadata["file_type"] = "image"
                    metadata["original_path"] = str(fp)
                    metadata["is_ai"] = is_ai
                    metadata["is_photo"] = is_photo

                    if is_ai:
                        ai_results.append(metadata)
                    elif is_photo:
                        photo_results.append(metadata)
                    else:
                        results.append(metadata)

                    if progress_callback:
                        progress_callback(f"  完了: {fp.name} → {metadata.get('adobe_title_en', '')[:50]}")

            except Exception as e:
                # バッチ全体が失敗した場合、全ファイルをエラーに
                for fp, _, _ in batch:
                    errors.append({"filename": fp.name, "error": str(e)})
                if progress_callback:
                    progress_callback(f"  バッチエラー: {str(e)}")

            processed_count += len(batch)

    # ── 動画は個別処理 ──
    for file_path, is_ai, is_photo in video_files:
        processed_count += 1
        if status_callback:
            status_callback(processed_count, total, file_path.name)
        if progress_callback:
            prefix = "[AI] " if is_ai else "[写真] " if is_photo else ""
            progress_callback(f"[{processed_count}/{total}] {prefix}解析中: {file_path.name}")

        try:
            metadata = analyze_video(file_path, api_key, progress_callback)
            metadata["filename"] = file_path.name
            metadata["file_type"] = "video"
            metadata["original_path"] = str(file_path)
            metadata["is_ai"] = is_ai
            metadata["is_photo"] = is_photo

            if is_ai:
                ai_results.append(metadata)
            elif is_photo:
                photo_results.append(metadata)
            else:
                results.append(metadata)

            if progress_callback:
                progress_callback(f"  完了: {metadata.get('adobe_title_en', '')[:50]}")

        except Exception as e:
            errors.append({"filename": file_path.name, "error": str(e)})
            if progress_callback:
                progress_callback(f"  エラー: {file_path.name} - {str(e)}")

    if not results and not photo_results and not ai_results and not vector_subfolders:
        raise ValueError("処理できたファイルがありません")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Adobe用CSV（通常 + 写真 + AI 全素材を1つに統合）
    all_adobe_results = results + photo_results + ai_results
    if all_adobe_results:
        adobe_path = csv_folder / f"adobe_stock_{timestamp}.csv"
        write_adobe_stock_csv(all_adobe_results, adobe_path)

    # Shutterstock用CSV（通常 + 写真を1つに統合、AI除外）
    all_ss_results = results + photo_results
    if all_ss_results:
        shutter_path = csv_folder / f"shutterstock_{timestamp}.csv"
        write_shutterstock_csv(all_ss_results, shutter_path)

    if progress_callback:
        progress_callback(f"\nCSV出力完了 → {csv_folder}")
        progress_callback(f"  Adobe統合CSV: {len(all_adobe_results)}件（通常{len(results)} / 写真{len(photo_results)} / AI{len(ai_results)}）")
        progress_callback(f"  SS統合CSV: {len(all_ss_results)}件（通常{len(results)} / 写真{len(photo_results)}）")

    # Pixta用メタデータ埋め込み（通常 + 写真 + AI全部）
    all_results = results + photo_results + ai_results
    if progress_callback:
        progress_callback("Pixta用メタデータを画像に埋め込み中...")
    embedded = embed_pixta_metadata(all_results, progress_callback)
    if progress_callback:
        progress_callback(f"  メタデータ埋め込み完了: {embedded}件")

    # 動画メタデータをJSONに保存（工程4で使い回すため）
    video_meta_list = [r for r in all_results if r.get("file_type") == "video"]
    if video_meta_list:
        video_meta_path = csv_folder / "video_metadata.json"
        with open(video_meta_path, "w", encoding="utf-8") as f:
            json.dump(video_meta_list, f, ensure_ascii=False, indent=2)
        if progress_callback:
            progress_callback(f"  動画メタデータ保存: {video_meta_path.name}（{len(video_meta_list)}件）")

    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    elapsed_str = f"{minutes}分{seconds}秒" if minutes > 0 else f"{seconds}秒"

    return {
        "success": len(all_results),
        "error": len(errors),
        "errors": errors,
        "csv_folder": str(csv_folder),
        "results": results,
        "photo_results": photo_results,
        "ai_results": ai_results,
        "video_results": [r for r in all_results if r.get("file_type") == "video"],
        "elapsed": elapsed_str
    }


def move_processed_files(results: list, input_folder: str, progress_callback=None) -> dict:
    input_path = Path(input_folder)
    base_dir = input_path.parent
    dest_folder = get_destination_folder(base_dir)

    moved = 0
    errors = []
    for r in results:
        src = Path(r["original_path"])
        if src.exists():
            if move_to_destination(src, dest_folder):
                moved += 1
                if progress_callback:
                    progress_callback(f"  移動: {src.name}")
            else:
                errors.append(src.name)

    # バックアップコピー
    backup_folder = get_backup_folder()
    if backup_folder:
        try:
            backup_count = 0
            for f in dest_folder.iterdir():
                if f.is_file():
                    dst = backup_folder / f.name
                    if not dst.exists():
                        shutil.copy2(str(f), str(dst))
                        backup_count += 1
            if progress_callback and backup_count > 0:
                progress_callback(f"  バックアップ: {backup_folder} ({backup_count}件)")
        except Exception as e:
            if progress_callback:
                progress_callback(f"  [!] バックアップ失敗（処理は続行）: {e}")

    if progress_callback:
        progress_callback(f"移動完了 → {dest_folder} ({moved}件)")

    return {
        "moved": moved,
        "dest_folder": str(dest_folder),
        "errors": errors
    }


# ============================================================
# Vector ファイル処理
# ============================================================

VECTOR_FOLDER_NAME = "Vector"


def get_vector_subfolders(input_folder: Path) -> list:
    vector_path = Path(input_folder) / VECTOR_FOLDER_NAME
    if not vector_path.exists():
        return []
    return [d for d in sorted(vector_path.iterdir()) if d.is_dir()]


def get_vector_eps_files(input_folder: Path) -> list:
    result = []
    for sub in get_vector_subfolders(input_folder):
        result.extend(sorted(sub.glob("*.eps")))
    return result


def get_vector_zip_files(input_folder: Path) -> list:
    result = []
    for sub in get_vector_subfolders(input_folder):
        result.extend(sorted(sub.glob("*.zip")))
    return result


def create_vector_zip(subfolder: Path, eps_path: Path, png_path: Path) -> Path:
    import zipfile
    zip_path = subfolder / f"{eps_path.stem}.zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(str(eps_path), eps_path.name)
        zf.write(str(png_path), png_path.name)
    return zip_path



def process_vector_files(input_folder: str, api_key: str, progress_callback=None) -> dict:
    input_path = Path(input_folder)
    subfolders = get_vector_subfolders(input_path)

    if not subfolders:
        if progress_callback:
            progress_callback("[Vector] Vectorフォルダまたはサブフォルダが見つかりません。スキップします。")
        return {"success": 0, "errors": [], "results": []}

    if progress_callback:
        progress_callback(f"\n[Vector] {len(subfolders)}件のベクターフォルダを処理開始...")

    results = []
    errors = []
    csv_folder = input_path / "csv_output"
    csv_folder.mkdir(exist_ok=True)

    # PNG/EPSペアを収集
    valid_pairs = []  # (subfolder, png_path, eps_path)
    for subfolder in subfolders:
        png_files = sorted(subfolder.glob("*.png"))
        eps_files = sorted(subfolder.glob("*.eps"))
        if not png_files:
            errors.append({"filename": subfolder.name, "error": "PNG not found"})
            continue
        if not eps_files:
            errors.append({"filename": subfolder.name, "error": "EPS not found"})
            continue
        valid_pairs.append((subfolder, png_files[0], eps_files[0]))

    # BATCH_MAX枚ずつバッチ処理
    for batch_start in range(0, len(valid_pairs), BATCH_MAX):
        batch = valid_pairs[batch_start:batch_start + BATCH_MAX]
        batch_paths = [png_path for _, png_path, _ in batch]

        if progress_callback:
            names = ", ".join(p.name for _, p, _ in batch)
            progress_callback(f"[Vector] バッチ解析中（{len(batch)}枚）: {names}")

        try:
            batch_results = analyze_images_batch(
                batch_paths, api_key, bg_prompt="", progress_callback=progress_callback
            )

            if len(batch_results) < len(batch):
                for j in range(len(batch_results), len(batch)):
                    subfolder, _, _ = batch[j]
                    errors.append({"filename": subfolder.name, "error": "バッチレスポンスに結果なし"})

            for j, metadata in enumerate(batch_results):
                if j >= len(batch):
                    break
                subfolder, png_path, eps_path = batch[j]

                # ベクタータグを追加
                adobe_kws = [k.strip() for k in metadata.get("adobe_keywords_en", "").split(",") if k.strip()]
                if "vector" not in [k.lower() for k in adobe_kws]:
                    adobe_kws.insert(0, "vector")
                metadata["adobe_keywords_en"] = ", ".join(adobe_kws[:49])

                ss_kws = [k.strip() for k in metadata.get("shutterstock_keywords_en", "").split(",") if k.strip()]
                if "vector" not in [k.lower() for k in ss_kws]:
                    ss_kws.insert(0, "vector")
                metadata["shutterstock_keywords_en"] = ", ".join(ss_kws[:50])

                pixta_kws = [k.strip() for k in metadata.get("pixta_keywords_ja", "").split(",") if k.strip()]
                if "ベクター" not in pixta_kws:
                    pixta_kws.insert(0, "ベクター")
                metadata["pixta_keywords_ja"] = ", ".join(pixta_kws[:50])

                metadata["filename"] = eps_path.name
                metadata["file_type"] = "vector"
                metadata["original_path"] = str(eps_path)
                metadata["subfolder"] = str(subfolder)
                metadata["eps_path"] = str(eps_path)
                metadata["png_path"] = str(png_path)
                results.append(metadata)

                if progress_callback:
                    progress_callback(f"  完了: {eps_path.name} → {metadata.get('adobe_title_en', '')[:50]}")

        except Exception as e:
            for subfolder, _, _ in batch:
                errors.append({"filename": subfolder.name, "error": str(e)})
            if progress_callback:
                progress_callback(f"  [NG] バッチエラー: {e}")

    return {"success": len(results), "errors": errors, "results": results}


def prepare_vector_zips_with_xmp(input_folder: str, vector_results: list, progress_callback=None) -> list:
    zips = []
    if not vector_results:
        return zips

    if progress_callback:
        progress_callback(f"\n[Vector/Pixta] {len(vector_results)}件のZIP作成...")

    for meta in vector_results:
        eps_path = Path(meta["eps_path"])
        png_path = Path(meta["png_path"])
        subfolder = Path(meta["subfolder"])

        if not eps_path.exists() or not png_path.exists():
            continue

        try:
            zip_path = create_vector_zip(subfolder, eps_path, png_path)
            if progress_callback:
                progress_callback(f"  ZIP作成完了: {zip_path.name}")
            zips.append(zip_path)
        except Exception as e:
            if progress_callback:
                progress_callback(f"  [NG] エラー ({subfolder.name}): {e}")

    return zips


def move_vector_subfolders(input_folder: str, progress_callback=None) -> dict:
    """Vector/ 以下のサブフォルダを 01_done に移動"""
    input_path = Path(input_folder)
    dest_folder = get_destination_folder(input_path.parent)
    subfolders = get_vector_subfolders(input_path)
    moved = 0
    errors = []
    for subfolder in subfolders:
        # 空フォルダはスキップ
        if not any(subfolder.iterdir()):
            if progress_callback:
                progress_callback(f"  スキップ（空フォルダ）: {subfolder.name}")
            continue
        try:
            # 1. 先にバックアップコピー（ソースから直接コピー）
            try:
                backup_folder = get_backup_folder()
                if backup_folder:
                    backup_dst = backup_folder / subfolder.name
                    if not backup_dst.exists():
                        shutil.copytree(str(subfolder), str(backup_dst))
                        for bak in backup_dst.glob("*.eps.bak"):
                            bak.unlink()
                        if progress_callback:
                            progress_callback(f"  バックアップ: {backup_dst}")
            except Exception as be:
                if progress_callback:
                    progress_callback(f"  [!] Vectorバックアップ失敗（処理は続行）: {be}")

            # 2. ローカル移動
            dst = dest_folder / subfolder.name
            counter = 1
            while dst.exists():
                dst = dest_folder / f"{subfolder.name}_{counter}"
                counter += 1
            shutil.move(str(subfolder), str(dst))
            moved += 1
            if progress_callback:
                progress_callback(f"  移動: {subfolder.name} → {dst}")
            for bak in dst.glob("*.eps.bak"):
                bak.unlink()
                if progress_callback:
                    progress_callback(f"  削除: {bak.name} (不要なバックアップ)")
        except Exception as e:
            errors.append(subfolder.name)
            if progress_callback:
                progress_callback(f"  [NG] 移動失敗: {subfolder.name} - {e}")
    return {"moved": moved, "errors": errors, "dest_folder": str(dest_folder)}


if __name__ == "__main__":
    print("このファイルは直接実行しないでください。app.py を実行してください。")
