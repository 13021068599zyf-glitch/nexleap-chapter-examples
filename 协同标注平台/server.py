"""本地抽帧图片整理平台。"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import webbrowser
from contextlib import contextmanager
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
IMAGE_ROOT = ROOT.parent / "抽帧图"
DATA_ROOT = ROOT / "data"
DB_PATH = DATA_ROOT / "图片整理.sqlite3"
WEB_ROOT = ROOT / "web"
EXPORT_ROOT = ROOT / "导出结果"
HOST = "127.0.0.1"
PORT = 8766
PAGE_SIZE = 72
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_STATUSES = {"未处理", "终选", "候选", "未选", "排除"}
TIME_PATTERN = re.compile(r"__abs_(\d{2}-\d{2}-\d{2}(?:\.\d+)?)")
FRAME_PATTERN = re.compile(r"__frame_(\d+)")
PHASE_PATTERN = re.compile(r"__phase_(\d+)_offset_(\d+)s__")
DB_LOCK = threading.RLock()
IMPORT_LOCK = threading.RLock()
MAX_IMPORT_FILE_SIZE = 200 * 1024 * 1024


@contextmanager
def connection():
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def clean_text(value: object, limit: int, required: bool = False) -> str:
    result = str(value or "").strip()
    if len(result) > limit:
        raise ValueError(f"内容不能超过 {limit} 个字符")
    if required and not result:
        raise ValueError("请填写必填内容")
    return result


def clean_tags(value: object) -> str:
    if isinstance(value, str):
        tags = re.split(r"[,，、;；\n]+", value)
    elif isinstance(value, list):
        tags = value
    else:
        tags = []
    normalized: list[str] = []
    for tag in tags:
        item = clean_text(tag, 40)
        if item and item not in normalized:
            normalized.append(item)
    if len(normalized) > 30:
        raise ValueError("最多添加 30 个标签")
    return json.dumps(normalized, ensure_ascii=False)


def initialize() -> None:
    if not IMAGE_ROOT.is_dir():
        raise FileNotFoundError(f"找不到图片目录：{IMAGE_ROOT}")
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS images (
                image_id INTEGER PRIMARY KEY AUTOINCREMENT,
                rel_path TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                filename TEXT NOT NULL,
                time_label TEXT NOT NULL DEFAULT '',
                frame_index INTEGER,
                status TEXT NOT NULL DEFAULT '未处理',
                tags_json TEXT NOT NULL DEFAULT '[]',
                note TEXT NOT NULL DEFAULT '',
                available INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS days (
                day_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                intro TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL DEFAULT 0,
                import_key TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chapters (
                chapter_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                intro TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                cover_image_id INTEGER REFERENCES images(image_id) ON DELETE SET NULL,
                day_id INTEGER REFERENCES days(day_id) ON DELETE SET NULL,
                position INTEGER NOT NULL DEFAULT 0,
                import_key TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chapter_images (
                chapter_id INTEGER NOT NULL REFERENCES chapters(chapter_id) ON DELETE CASCADE,
                image_id INTEGER NOT NULL REFERENCES images(image_id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0,
                added_at TEXT NOT NULL,
                PRIMARY KEY (chapter_id, image_id)
            );
            CREATE TABLE IF NOT EXISTS chapter_covers (
                chapter_id INTEGER NOT NULL REFERENCES chapters(chapter_id) ON DELETE CASCADE,
                phase INTEGER NOT NULL,
                image_id INTEGER NOT NULL REFERENCES images(image_id) ON DELETE CASCADE,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (chapter_id, phase)
            );
            CREATE TABLE IF NOT EXISTS scene_groups (
                group_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id INTEGER NOT NULL REFERENCES chapters(chapter_id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL DEFAULT 0,
                import_key TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE (chapter_id, group_id)
            );
            CREATE TABLE IF NOT EXISTS scene_group_images (
                chapter_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                image_id INTEGER NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                added_at TEXT NOT NULL,
                PRIMARY KEY (chapter_id, image_id),
                FOREIGN KEY (chapter_id, group_id)
                    REFERENCES scene_groups(chapter_id, group_id) ON DELETE CASCADE,
                FOREIGN KEY (chapter_id, image_id)
                    REFERENCES chapter_images(chapter_id, image_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_images_source ON images(source, filename);
            CREATE INDEX IF NOT EXISTS idx_images_status ON images(status);
            CREATE INDEX IF NOT EXISTS idx_chapter_images_position
                ON chapter_images(chapter_id, position, image_id);
            CREATE INDEX IF NOT EXISTS idx_chapter_covers_image
                ON chapter_covers(image_id);
            CREATE INDEX IF NOT EXISTS idx_scene_groups_chapter_position
                ON scene_groups(chapter_id, position, group_id);
            CREATE INDEX IF NOT EXISTS idx_scene_group_images_group_position
                ON scene_group_images(group_id, position, image_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_scene_groups_import_key
                ON scene_groups(import_key) WHERE import_key IS NOT NULL;
            """
        )
        chapter_columns = {row["name"] for row in db.execute("PRAGMA table_info(chapters)")}
        if "day_id" not in chapter_columns:
            db.execute("ALTER TABLE chapters ADD COLUMN day_id INTEGER REFERENCES days(day_id) ON DELETE SET NULL")
        if "import_key" not in chapter_columns:
            db.execute("ALTER TABLE chapters ADD COLUMN import_key TEXT")
        if "position" not in chapter_columns:
            db.execute("ALTER TABLE chapters ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
        db.execute(
            """UPDATE chapters
               SET position = (
                   SELECT COUNT(*) FROM chapters AS prior
                   WHERE prior.day_id IS chapters.day_id
                     AND prior.chapter_id <= chapters.chapter_id
               )
               WHERE position = 0"""
        )
        day_columns = {row["name"] for row in db.execute("PRAGMA table_info(days)")}
        if "import_key" not in day_columns:
            db.execute("ALTER TABLE days ADD COLUMN import_key TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS idx_chapters_day ON chapters(day_id, chapter_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_chapters_day_position ON chapters(day_id, position, chapter_id)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_days_import_key ON days(import_key) WHERE import_key IS NOT NULL")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_chapters_import_key ON chapters(import_key) WHERE import_key IS NOT NULL")
    scan_images()
    with DB_LOCK, connection() as db:
        ensure_chapter_covers(db)


def scan_images() -> dict:
    paths = sorted(
        (path for path in IMAGE_ROOT.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda path: str(path).casefold(),
    )
    stamp = now()
    with DB_LOCK, connection() as db:
        db.execute("UPDATE images SET available = 0")
        for path in paths:
            rel_path = path.relative_to(IMAGE_ROOT).as_posix()
            filename = path.name
            source = path.relative_to(IMAGE_ROOT).parts[0] if len(path.relative_to(IMAGE_ROOT).parts) > 1 else "未分类"
            time_match = TIME_PATTERN.search(filename)
            frame_match = FRAME_PATTERN.search(filename)
            db.execute(
                """INSERT INTO images
                   (rel_path, source, filename, time_label, frame_index, available, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(rel_path) DO UPDATE SET
                     source=excluded.source, filename=excluded.filename,
                     time_label=excluded.time_label, frame_index=excluded.frame_index,
                     available=1""",
                (
                    rel_path, source, filename,
                    time_match.group(1).replace("-", ":") if time_match else "",
                    int(frame_match.group(1)) if frame_match else None,
                    stamp,
                ),
            )
        count = db.execute("SELECT COUNT(*) FROM images WHERE available = 1").fetchone()[0]
        sources = [row[0] for row in db.execute(
            "SELECT DISTINCT source FROM images WHERE available = 1 ORDER BY source"
        )]
    return {"image_count": count, "sources": sources}


def image_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["tags"] = json.loads(item.pop("tags_json", "[]"))
    item["available"] = bool(item.get("available", 1))
    phase_match = PHASE_PATTERN.search(item["filename"])
    item["phase"] = int(phase_match.group(1)) if phase_match else None
    item["offset_seconds"] = int(phase_match.group(2)) if phase_match else None
    return item


def ensure_chapter_covers(db: sqlite3.Connection, chapter_id: int | None = None) -> None:
    chapter_filter = " AND c.chapter_id = ?" if chapter_id is not None else ""
    params: list[object] = [chapter_id] if chapter_id is not None else []
    legacy_rows = db.execute(
        f"""SELECT c.chapter_id, c.cover_image_id, i.filename
            FROM chapters c JOIN images i ON i.image_id = c.cover_image_id
            WHERE c.cover_image_id IS NOT NULL{chapter_filter}""",
        params,
    ).fetchall()
    for row in legacy_rows:
        phase_match = PHASE_PATTERN.search(row["filename"])
        if phase_match:
            db.execute(
                "INSERT OR IGNORE INTO chapter_covers(chapter_id, phase, image_id, updated_at) VALUES (?, ?, ?, ?)",
                (row["chapter_id"], int(phase_match.group(1)), row["cover_image_id"], now()),
            )

    cover_filter = " AND cc.chapter_id = ?" if chapter_id is not None else ""
    db.execute(
        f"""DELETE FROM chapter_covers AS cc
            WHERE NOT EXISTS (
                SELECT 1 FROM chapter_images ci JOIN images i ON i.image_id = ci.image_id
                WHERE ci.chapter_id = cc.chapter_id AND ci.image_id = cc.image_id AND i.available = 1
            ){cover_filter}""",
        params,
    )
    existing = {
        (row["chapter_id"], row["phase"])
        for row in db.execute(
            "SELECT chapter_id, phase FROM chapter_covers" + (" WHERE chapter_id = ?" if chapter_id is not None else ""),
            params,
        )
    }
    image_filter = " AND ci.chapter_id = ?" if chapter_id is not None else ""
    rows = db.execute(
        f"""SELECT ci.chapter_id, i.image_id, i.filename
            FROM chapter_images ci JOIN images i ON i.image_id = ci.image_id
            WHERE i.available = 1{image_filter}
            ORDER BY ci.chapter_id,
                     CASE WHEN i.time_label = '' THEN 1 ELSE 0 END,
                     i.time_label, ci.position, i.image_id""",
        params,
    ).fetchall()
    for row in rows:
        phase_match = PHASE_PATTERN.search(row["filename"])
        if not phase_match:
            continue
        phase = int(phase_match.group(1))
        key = (row["chapter_id"], phase)
        if key in existing:
            continue
        db.execute(
            "INSERT INTO chapter_covers(chapter_id, phase, image_id, updated_at) VALUES (?, ?, ?, ?)",
            (row["chapter_id"], phase, row["image_id"], now()),
        )
        existing.add(key)


def chapter_row(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item = dict(row)
    item["tags"] = json.loads(item.pop("tags_json", "[]"))
    item["image_count"] = db.execute(
        "SELECT COUNT(*) FROM chapter_images WHERE chapter_id = ?", (item["chapter_id"],)
    ).fetchone()[0]
    covers = {}
    for cover in db.execute(
        """SELECT cc.phase, cc.image_id, i.rel_path, i.filename
           FROM chapter_covers cc JOIN images i ON i.image_id = cc.image_id
           WHERE cc.chapter_id = ? AND i.available = 1 ORDER BY cc.phase""",
        (item["chapter_id"],),
    ):
        phase_match = PHASE_PATTERN.search(cover["filename"])
        covers[str(cover["phase"])] = {
            "image_id": cover["image_id"],
            "rel_path": cover["rel_path"],
            "offset_seconds": int(phase_match.group(2)) if phase_match else None,
        }
    item["covers"] = covers
    default_cover = covers.get("0") or (next(iter(covers.values())) if covers else None)
    item["cover_image_id"] = default_cover["image_id"] if default_cover else None
    return item


def scene_group_row(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item = dict(row)
    item["image_ids"] = [result["image_id"] for result in db.execute(
        """SELECT sgi.image_id FROM scene_group_images sgi
           JOIN images i ON i.image_id=sgi.image_id
           WHERE sgi.group_id=? AND i.available=1
           ORDER BY CASE WHEN i.time_label='' THEN 1 ELSE 0 END,
                    i.time_label, sgi.position, i.image_id""",
        (item["group_id"],),
    )]
    item["image_count"] = len(item["image_ids"])
    return item


def day_row(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item = dict(row)
    item["chapter_count"] = db.execute(
        "SELECT COUNT(*) FROM chapters WHERE day_id = ?", (item["day_id"],)
    ).fetchone()[0]
    item["image_count"] = db.execute(
        """SELECT COUNT(DISTINCT ci.image_id)
           FROM chapters c JOIN chapter_images ci ON ci.chapter_id = c.chapter_id
           JOIN images i ON i.image_id = ci.image_id
           WHERE c.day_id = ? AND i.available = 1""",
        (item["day_id"],),
    ).fetchone()[0]
    return item


def clean_day_id(db: sqlite3.Connection, value: object) -> int | None:
    if value in ("", None):
        return None
    try:
        day_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Day 编号无效") from exc
    if not db.execute("SELECT 1 FROM days WHERE day_id = ?", (day_id,)).fetchone():
        raise ValueError("所选 Day 不存在")
    return day_id


def safe_image_path(image_id: int) -> Path | None:
    with connection() as db:
        row = db.execute(
            "SELECT rel_path FROM images WHERE image_id = ? AND available = 1", (image_id,)
        ).fetchone()
    if not row:
        return None
    candidate = (IMAGE_ROOT / Path(*Path(row["rel_path"]).parts)).resolve()
    try:
        candidate.relative_to(IMAGE_ROOT.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def safe_import_path(value: object) -> Path:
    """把浏览器提供的相对路径限制在图片根目录内。"""
    relative = str(value or "").strip().replace("\\", "/")
    if not relative or len(relative) > 1000 or "\x00" in relative:
        raise ValueError("导入文件路径无效")
    pure = PurePosixPath(relative)
    parts = pure.parts
    if pure.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("导入文件路径无效")
    if any(re.search(r'[<>:"|?*]', part) for part in parts):
        raise ValueError("导入文件路径包含不可用字符")
    if Path(parts[-1]).suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError("只支持 JPG、PNG 和 WebP 图片")
    target = (IMAGE_ROOT.joinpath(*parts)).resolve()
    try:
        target.relative_to(IMAGE_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("导入文件路径超出图片库") from exc
    return target


def import_structured_result(db: sqlite3.Connection, payload: dict) -> dict:
    """从平台导出的整理结果包恢复 Day、Chapter、图片文字和分组封面。"""
    package_key = clean_text(payload.get("package_key"), 300, required=True)
    days = payload.get("days")
    chapters = payload.get("chapters")
    if not isinstance(days, list) or not days or len(days) > 100:
        raise ValueError("整理结果中的 Day 数据无效")
    if not isinstance(chapters, list) or len(chapters) > 2000:
        raise ValueError("整理结果中的 Chapter 数据无效")

    image_lookup: dict[str, sqlite3.Row] = {}
    missing: list[str] = []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise ValueError("Chapter 数据格式错误")
        images = chapter.get("images", [])
        if not isinstance(images, list) or len(images) > 10000:
            raise ValueError("Chapter 图片数据无效")
        for image in images:
            if not isinstance(image, dict):
                raise ValueError("图片数据格式错误")
            target = safe_import_path(image.get("import_path"))
            rel_path = target.relative_to(IMAGE_ROOT).as_posix()
            row = db.execute(
                "SELECT image_id, filename FROM images WHERE rel_path=? AND available=1", (rel_path,)
            ).fetchone()
            if row:
                image_lookup[rel_path] = row
            elif len(missing) < 5:
                missing.append(rel_path)
    if missing:
        raise ValueError("整理结果中的图片尚未完成入库：" + "、".join(missing))

    day_ids: dict[str, int] = {}
    for day in days:
        if not isinstance(day, dict):
            raise ValueError("Day 数据格式错误")
        external_id = clean_text(day.get("external_id"), 80, required=True)
        import_key = f"{package_key}:day:{external_id}"
        title = clean_text(day.get("title"), 120, required=True)
        intro = clean_text(day.get("intro"), 2000)
        existing = db.execute("SELECT day_id FROM days WHERE import_key=?", (import_key,)).fetchone()
        if existing:
            day_id = int(existing["day_id"])
            db.execute(
                "UPDATE days SET title=?, intro=?, updated_at=? WHERE day_id=?",
                (title, intro, now(), day_id),
            )
        else:
            position = db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM days").fetchone()[0]
            cursor = db.execute(
                "INSERT INTO days(title, intro, position, import_key, updated_at) VALUES (?, ?, ?, ?, ?)",
                (title, intro, position, import_key, now()),
            )
            day_id = int(cursor.lastrowid)
        day_ids[external_id] = day_id

    chapter_ids: list[int] = []
    assigned_images = 0
    chapter_positions: dict[int | None, int] = {}
    for chapter in chapters:
        external_id = clean_text(chapter.get("external_id"), 80, required=True)
        external_day_id = clean_text(chapter.get("external_day_id"), 80)
        if external_day_id and external_day_id not in day_ids:
            raise ValueError("Chapter 所属 Day 不存在")
        day_id = day_ids.get(external_day_id)
        chapter_positions[day_id] = chapter_positions.get(day_id, 0) + 1
        try:
            position = int(chapter.get("position") or chapter_positions[day_id])
        except (TypeError, ValueError) as exc:
            raise ValueError("Chapter 顺序无效") from exc
        if position < 1 or position > 10000:
            raise ValueError("Chapter 顺序无效")
        import_key = f"{package_key}:chapter:{external_id}"
        title = clean_text(chapter.get("title"), 120, required=True)
        intro = clean_text(chapter.get("intro"), 2000)
        tags_json = clean_tags(chapter.get("tags"))
        existing = db.execute("SELECT chapter_id FROM chapters WHERE import_key=?", (import_key,)).fetchone()
        if existing:
            chapter_id = int(existing["chapter_id"])
            db.execute(
                "UPDATE chapters SET title=?, intro=?, tags_json=?, day_id=?, position=?, updated_at=? WHERE chapter_id=?",
                (title, intro, tags_json, day_id, position, now(), chapter_id),
            )
        else:
            cursor = db.execute(
                "INSERT INTO chapters(title, intro, tags_json, day_id, position, import_key, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, intro, tags_json, day_id, position, import_key, now()),
            )
            chapter_id = int(cursor.lastrowid)
        chapter_ids.append(chapter_id)

        for position, image in enumerate(chapter.get("images", []), start=1):
            target = safe_import_path(image.get("import_path"))
            rel_path = target.relative_to(IMAGE_ROOT).as_posix()
            image_id = int(image_lookup[rel_path]["image_id"])
            status = clean_text(image.get("status"), 20) or "未处理"
            if status not in IMAGE_STATUSES:
                status = "未处理"
            db.execute(
                "UPDATE images SET status=?, tags_json=?, note=?, updated_at=? WHERE image_id=?",
                (status, clean_tags(image.get("tags")), clean_text(image.get("note"), 1000), now(), image_id),
            )
            db.execute(
                """INSERT INTO chapter_images(chapter_id, image_id, position, added_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chapter_id, image_id) DO UPDATE SET position=excluded.position""",
                (chapter_id, image_id, position, now()),
            )
            assigned_images += 1

        scene_groups = chapter.get("scene_groups", [])
        if not isinstance(scene_groups, list) or len(scene_groups) > 2000:
            raise ValueError("Chapter 风貌组数据无效")
        for group_index, scene_group in enumerate(scene_groups, start=1):
            if not isinstance(scene_group, dict):
                raise ValueError("风貌组数据格式错误")
            external_group_id = clean_text(scene_group.get("external_id"), 80) or str(group_index)
            group_import_key = f"{package_key}:group:{external_id}:{external_group_id}"
            group_title = clean_text(scene_group.get("title"), 120, required=True)
            group_description = clean_text(scene_group.get("description"), 2000)
            try:
                group_position = int(scene_group.get("position") or group_index)
            except (TypeError, ValueError) as exc:
                raise ValueError("风貌组顺序无效") from exc
            if group_position < 1 or group_position > 10000:
                raise ValueError("风貌组顺序无效")
            existing_scene_group = db.execute(
                "SELECT group_id FROM scene_groups WHERE import_key=?", (group_import_key,)
            ).fetchone()
            if existing_scene_group:
                group_id = int(existing_scene_group["group_id"])
                db.execute(
                    "UPDATE scene_groups SET title=?,description=?,position=?,updated_at=? WHERE group_id=?",
                    (group_title, group_description, group_position, now(), group_id),
                )
            else:
                group_id = int(db.execute(
                    """INSERT INTO scene_groups(chapter_id,title,description,position,import_key,updated_at)
                       VALUES(?,?,?,?,?,?)""",
                    (chapter_id, group_title, group_description, group_position, group_import_key, now()),
                ).lastrowid)
            group_images = scene_group.get("images", [])
            if not isinstance(group_images, list) or len(group_images) > 10000:
                raise ValueError("风貌组图片数据无效")
            for group_image_position, image in enumerate(group_images, start=1):
                if not isinstance(image, dict):
                    raise ValueError("风貌组图片数据格式错误")
                target = safe_import_path(image.get("import_path"))
                rel_path = target.relative_to(IMAGE_ROOT).as_posix()
                image_row_value = image_lookup.get(rel_path)
                if not image_row_value:
                    raise ValueError("风貌组中的图片未包含在 Chapter 图片列表中")
                db.execute(
                    """INSERT INTO scene_group_images(chapter_id,group_id,image_id,position,added_at)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(chapter_id,image_id) DO UPDATE SET
                         group_id=excluded.group_id,position=excluded.position,added_at=excluded.added_at""",
                    (chapter_id, group_id, int(image_row_value["image_id"]), group_image_position, now()),
                )

        cover_path = chapter.get("cover_import_path")
        phase = chapter.get("cover_phase")
        if cover_path not in ("", None) and phase not in ("", None):
            target = safe_import_path(cover_path)
            rel_path = target.relative_to(IMAGE_ROOT).as_posix()
            cover = image_lookup.get(rel_path)
            if cover:
                phase_number = int(phase)
                if phase_number < 0 or phase_number > 999:
                    raise ValueError("封面偏移组编号无效")
                cover_id = int(cover["image_id"])
                db.execute(
                    """INSERT INTO chapter_covers(chapter_id, phase, image_id, updated_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(chapter_id, phase) DO UPDATE SET
                         image_id=excluded.image_id, updated_at=excluded.updated_at""",
                    (chapter_id, phase_number, cover_id, now()),
                )
                db.execute(
                    "UPDATE chapters SET cover_image_id=?, updated_at=? WHERE chapter_id=?",
                    (cover_id, now(), chapter_id),
                )

    return {
        "day_count": len(day_ids),
        "chapter_count": len(chapter_ids),
        "assigned_image_count": assigned_images,
        "day_ids": list(day_ids.values()),
        "chapter_ids": chapter_ids,
    }


def safe_export_name(value: object, fallback: str) -> str:
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "").strip())
    result = result.strip(" .")[:80].strip(" .")
    if result.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        result = f"_{result}"
    return result or fallback


def build_export_data(db: sqlite3.Connection, day_value: object = "", phase_value: object = "") -> dict:
    day_text = "" if day_value in ("", None) else str(day_value).strip()
    phase_text = "" if phase_value in ("", None) else str(phase_value).strip()
    selected_day = None
    selected_phase = None
    if day_text:
        try:
            day_id = int(day_text)
        except ValueError as exc:
            raise ValueError("Day 编号无效") from exc
        selected_day = db.execute("SELECT * FROM days WHERE day_id=?", (day_id,)).fetchone()
        if not selected_day:
            raise ValueError("所选 Day 不存在")
    if phase_text:
        try:
            selected_phase = int(phase_text)
        except ValueError as exc:
            raise ValueError("抽帧组编号无效") from exc
        if selected_phase < 0 or selected_phase > 999:
            raise ValueError("抽帧组编号无效")

    days = []
    day_rows = (
        [selected_day]
        if selected_day
        else db.execute("SELECT * FROM days ORDER BY position, day_id").fetchall()
    )
    for row in day_rows:
        day = day_row(db, row)
        day["chapter_ids"] = [item[0] for item in db.execute(
            "SELECT chapter_id FROM chapters WHERE day_id = ? ORDER BY position, chapter_id", (row["day_id"],)
        )]
        days.append(day)

    chapters = []
    chapter_rows = (
        db.execute("SELECT * FROM chapters WHERE day_id=? ORDER BY position, chapter_id", (selected_day["day_id"],)).fetchall()
        if selected_day
        else db.execute("SELECT * FROM chapters ORDER BY CASE WHEN day_id IS NULL THEN 1 ELSE 0 END, day_id, position, chapter_id").fetchall()
    )
    exported_image_count = 0
    for row in chapter_rows:
        chapter = chapter_row(db, row)
        image_conditions = ["ci.chapter_id=?", "i.available=1"]
        image_params: list[object] = [row["chapter_id"]]
        if selected_phase is not None:
            image_conditions.append("instr(i.filename, ?) > 0")
            image_params.append(f"__phase_{selected_phase:02d}_offset_")
        chapter["images"] = [
            {**image_row(item), "position": item["position"]}
            for item in db.execute(
                f"""SELECT i.*, ci.position FROM chapter_images ci
                   JOIN images i ON i.image_id=ci.image_id
                   WHERE {" AND ".join(image_conditions)}
                   ORDER BY CASE WHEN i.time_label = '' THEN 1 ELSE 0 END,
                            i.time_label, i.source, i.filename COLLATE NOCASE""",
                image_params,
            )
        ]
        chapter["exported_image_count"] = len(chapter["images"])
        exported_image_count += len(chapter["images"])
        selected_cover = (
            chapter["covers"].get(str(selected_phase))
            if selected_phase is not None
            else chapter["covers"].get("0") or (next(iter(chapter["covers"].values())) if chapter["covers"] else None)
        )
        chapter["cover_image_id"] = selected_cover["image_id"] if selected_cover else None
        chapter["cover_image_path"] = selected_cover["rel_path"] if selected_cover else None
        chapter_image_lookup = {image["image_id"]: image for image in chapter["images"]}
        grouped_ids: set[int] = set()
        scene_groups = []
        for group_row in db.execute(
            "SELECT * FROM scene_groups WHERE chapter_id=? ORDER BY position, group_id",
            (row["chapter_id"],),
        ):
            scene_group = scene_group_row(db, group_row)
            group_images = [
                chapter_image_lookup[image_id]
                for image_id in scene_group["image_ids"]
                if image_id in chapter_image_lookup
            ]
            grouped_ids.update(image["image_id"] for image in group_images)
            scene_groups.append({
                "group_id": scene_group["group_id"],
                "external_id": str(scene_group["group_id"]),
                "title": scene_group["title"],
                "description": scene_group["description"],
                "position": scene_group["position"],
                "image_count": len(group_images),
                "images": group_images,
            })
        chapter["scene_groups"] = scene_groups
        chapter["ungrouped_images"] = [
            image for image in chapter["images"] if image["image_id"] not in grouped_ids
        ]
        chapters.append(chapter)

    offset_seconds = None
    if selected_phase is not None:
        offset_row = db.execute(
            "SELECT filename FROM images WHERE instr(filename, ?) > 0 LIMIT 1",
            (f"__phase_{selected_phase:02d}_offset_",),
        ).fetchone()
        if offset_row:
            phase_match = PHASE_PATTERN.search(offset_row["filename"])
            offset_seconds = int(phase_match.group(2)) if phase_match else None
    return {
        "exported_at": now(),
        "image_root": str(IMAGE_ROOT),
        "filters": {
            "day_id": selected_day["day_id"] if selected_day else None,
            "day_title": selected_day["title"] if selected_day else None,
            "phase": selected_phase,
            "offset_seconds": offset_seconds,
        },
        "exported_image_count": exported_image_count,
        "days": days,
        "chapters": chapters,
    }


def write_export_folder(export: dict) -> Path:
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    filters = export["filters"]
    folder_parts = []
    if filters["day_id"] is not None:
        folder_parts.append(safe_export_name(filters["day_title"], "未命名Day"))
    else:
        folder_parts.append("全部Day")
    if filters["phase"] is not None:
        folder_parts.append(f"偏移为{filters['offset_seconds']}组")
    folder_parts.append(datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    target = EXPORT_ROOT / "_".join(folder_parts)
    target.mkdir()

    try:
        (target / "整理结果.json").write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
        filter_text = "全部偏移组"
        if filters["phase"] is not None:
            filter_text = f"偏移为{filters['offset_seconds']}组"
        readme = [
            "# 图片整理导出结果",
            "",
            f"- 导出时间：{export['exported_at']}",
            f"- Day 范围：{filters['day_title'] or '全部 Day'}",
            f"- 偏移组：{filter_text}",
            f"- Chapter 数量：{len(export['chapters'])}",
            f"- 图片数量：{export['exported_image_count']}",
            "",
            "每张图片与同名 Markdown 文件一一对应；完整结构化数据见 `整理结果.json`。",
        ]
        (target / "导出说明.md").write_text("\n".join(readme), encoding="utf-8")

        day_lookup = {day["day_id"]: day for day in export["days"]}
        day_folders: dict[int | None, Path] = {}
        day_order: dict[int | None, int] = {}
        for index, day in enumerate(export["days"], start=1):
            day_id = day["day_id"]
            day_order[day_id] = index
            day_folder = target / f"{index:02d}_Day_{safe_export_name(day['title'], str(day_id))}"
            day_folder.mkdir()
            day_folders[day_id] = day_folder
            day_text = [
                f"# {day['title']}",
                "",
                day.get("intro") or "暂无简介。",
                "",
                f"- Chapter 数量：{day.get('chapter_count', 0)}",
                f"- 原始图片数量：{day.get('image_count', 0)}",
            ]
            (day_folder / "Day说明.md").write_text("\n".join(day_text), encoding="utf-8")

        chapter_numbers: dict[int | None, int] = {}
        for chapter in export["chapters"]:
            day_id = chapter.get("day_id")
            if day_id not in day_folders:
                day_order[day_id] = len(day_folders) + 1
                day_folder = target / f"{day_order[day_id]:02d}_未归入Day"
                day_folder.mkdir(exist_ok=True)
                day_folders[day_id] = day_folder
            chapter_numbers[day_id] = chapter_numbers.get(day_id, 0) + 1
            chapter_number = chapter_numbers[day_id]
            chapter_folder = day_folders[day_id] / f"{chapter_number:02d}_{safe_export_name(chapter['title'], str(chapter['chapter_id']))}"
            scene_groups = chapter.get("scene_groups") or []
            image_folder = chapter_folder / "图片"
            chapter_folder.mkdir(parents=True)
            group_image_folders: dict[int, Path] = {}
            group_by_image: dict[int, dict] = {}
            if scene_groups:
                for group_index, scene_group in enumerate(scene_groups, start=1):
                    group_title = scene_group.get("title") or f"风貌组{group_index}"
                    group_folder = chapter_folder / f"{group_index:02d}_风貌组_{safe_export_name(group_title, str(group_index))}"
                    group_image_folder = group_folder / "图片"
                    group_image_folder.mkdir(parents=True)
                    group_image_folders[group_index] = group_image_folder
                    for image in scene_group.get("images") or []:
                        group_by_image[image["image_id"]] = {
                            "title": group_title,
                            "folder": group_image_folder,
                        }
                    group_text = [
                        f"# {group_title}",
                        "",
                        scene_group.get("description") or "暂无风貌组描述。",
                        "",
                        f"- 所属 Chapter：{chapter['title']}",
                        f"- 图片数量：{len(scene_group.get('images') or [])}",
                    ]
                    (group_folder / "风貌组说明.md").write_text("\n".join(group_text), encoding="utf-8")
                if chapter.get("ungrouped_images"):
                    image_folder = chapter_folder / "未分组" / "图片"
                    image_folder.mkdir(parents=True)
            else:
                image_folder.mkdir(parents=True)
            day = day_lookup.get(day_id)
            tags = "、".join(chapter.get("tags") or []) or "无"
            chapter_text = [
                f"# {chapter['title']}",
                "",
                chapter.get("intro") or "暂无简介。",
                "",
                f"- 所属 Day：{day['title'] if day else '未归入 Day'}",
                f"- 标签：{tags}",
                f"- 导出图片数：{chapter['exported_image_count']}",
            ]
            cover_items = chapter.get("covers") or {}
            if filters["phase"] is not None:
                cover = cover_items.get(str(filters["phase"]))
                chapter_text.append(f"- {filter_text}封面：{cover['rel_path'] if cover else '未设置'}")
            else:
                chapter_text.append("- 分组封面：")
                chapter_text.extend(
                    f"  - 偏移为{cover['offset_seconds']}组：{cover['rel_path']}"
                    for cover in cover_items.values()
                )
            (chapter_folder / "Chapter说明.md").write_text("\n".join(chapter_text), encoding="utf-8")

            selected_image_ids = {image["image_id"] for image in chapter["images"]}
            export_covers = (
                [cover_items[str(filters["phase"])]]
                if filters["phase"] is not None and str(filters["phase"]) in cover_items
                else list(cover_items.values()) if filters["phase"] is None else []
            )
            for cover in export_covers:
                if cover["image_id"] not in selected_image_ids:
                    continue
                cover_path = safe_image_path(cover["image_id"])
                if not cover_path:
                    raise ValueError(f"找不到 Chapter“{chapter['title']}”的封面原图")
                shutil.copy2(
                    cover_path,
                    chapter_folder / f"Chapter封面_偏移为{cover['offset_seconds']}组{cover_path.suffix.lower()}",
                )

            for image_index, image in enumerate(chapter["images"], start=1):
                source_path = safe_image_path(image["image_id"])
                if not source_path:
                    raise ValueError(f"找不到原图：{image['rel_path']}")
                time_name = safe_export_name((image.get("time_label") or f"图片{image['image_id']}").replace(":", "-"), f"图片{image['image_id']}")
                stem = f"{image_index:03d}_{time_name}"
                group_entry = group_by_image.get(image["image_id"])
                target_folder = group_entry["folder"] if group_entry else image_folder
                shutil.copy2(source_path, target_folder / f"{stem}{source_path.suffix.lower()}")
                image_tags = "、".join(image.get("tags") or []) or "无"
                image_text = [
                    f"# 图片 {image_index:03d}",
                    "",
                    f"- 所属 Day：{day['title'] if day else '未归入 Day'}",
                    f"- 所属 Chapter：{chapter['title']}",
                    f"- 所属风貌组：{group_entry['title']}" if group_entry else "- 所属风貌组：未分组",
                    f"- 视频时间：{image.get('time_label') or '未知'}",
                    f"- 抽帧组：偏移为{image['offset_seconds']}组" if image.get("phase") is not None else "- 抽帧组：未知",
                    f"- 状态：{image.get('status') or '未处理'}",
                    f"- 标签：{image_tags}",
                    f"- 原始相对路径：{image['rel_path']}",
                    "",
                    "## 备注",
                    "",
                    image.get("note") or "暂无备注。",
                ]
                (target_folder / f"{stem}.md").write_text("\n".join(image_text), encoding="utf-8")
    except Exception:
        resolved_target = target.resolve()
        resolved_target.relative_to(EXPORT_ROOT.resolve())
        shutil.rmtree(resolved_target)
        raise
    return target


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalImageOrganizer/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def respond(self, status: int, payload: object) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def body(self, max_size: int = 4 * 1024 * 1024) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > max_size:
            raise ValueError("请求内容为空或过大")
        data = json.loads(self.rfile.read(size).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("请求内容格式错误")
        return data

    def mutation_allowed(self) -> bool:
        origin = self.headers.get("Origin", "")
        return not origin or origin in {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

    def receive_import_file(self, parsed) -> None:
        query = parse_qs(parsed.query)
        target = safe_import_path(query.get("path", [""])[0])
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("导入文件大小无效") from exc
        if size < 1:
            raise ValueError("导入文件为空")
        if size > MAX_IMPORT_FILE_SIZE:
            raise ValueError("单张图片不能超过 200 MB")

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", delete=False, dir=target.parent, prefix=".导入-", suffix=".tmp"
            ) as temporary:
                temporary_path = Path(temporary.name)
                remaining = size
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("图片上传未完成")
                    temporary.write(chunk)
                    remaining -= len(chunk)
            with IMPORT_LOCK:
                if target.exists():
                    temporary_path.unlink(missing_ok=True)
                    self.respond(200, {"status": "skipped", "path": str(target.relative_to(IMAGE_ROOT))})
                    return
                os.replace(temporary_path, target)
                temporary_path = None
            self.respond(201, {"status": "imported", "path": str(target.relative_to(IMAGE_ROOT))})
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/"):
                with connection() as db:
                    if path == "/api/meta":
                        sources = [dict(row) for row in db.execute(
                            "SELECT source, COUNT(*) AS count FROM images WHERE available = 1 GROUP BY source ORDER BY source"
                        )]
                        statuses = {row["status"]: row["count"] for row in db.execute(
                            "SELECT status, COUNT(*) AS count FROM images WHERE available = 1 GROUP BY status"
                        )}
                        unassigned = db.execute(
                            "SELECT COUNT(*) FROM images i WHERE i.available=1 AND NOT EXISTS (SELECT 1 FROM chapter_images ci WHERE ci.image_id=i.image_id)"
                        ).fetchone()[0]
                        phase_counts: dict[tuple[int, int], int] = {}
                        for image in db.execute("SELECT filename FROM images WHERE available = 1"):
                            phase_match = PHASE_PATTERN.search(image["filename"])
                            if phase_match:
                                key = (int(phase_match.group(1)), int(phase_match.group(2)))
                                phase_counts[key] = phase_counts.get(key, 0) + 1
                        phases = [
                            {"phase": phase, "offset_seconds": offset, "count": count}
                            for (phase, offset), count in sorted(phase_counts.items())
                        ]
                        self.respond(200, {"image_count": sum(s["count"] for s in sources), "sources": sources, "statuses": statuses, "unassigned": unassigned, "phases": phases})
                        return
                    if path == "/api/chapters":
                        rows = db.execute(
                            "SELECT * FROM chapters ORDER BY CASE WHEN day_id IS NULL THEN 1 ELSE 0 END, day_id, position, chapter_id"
                        ).fetchall()
                        self.respond(200, [chapter_row(db, row) for row in rows])
                        return
                    chapter_groups_match = re.fullmatch(r"/api/chapters/(\d+)/groups", path)
                    if chapter_groups_match:
                        chapter_id = int(chapter_groups_match.group(1))
                        if not db.execute("SELECT 1 FROM chapters WHERE chapter_id=?", (chapter_id,)).fetchone():
                            self.respond(404, {"error": "Chapter 不存在"})
                            return
                        rows = db.execute(
                            "SELECT * FROM scene_groups WHERE chapter_id=? ORDER BY position, group_id",
                            (chapter_id,),
                        ).fetchall()
                        self.respond(200, [scene_group_row(db, row) for row in rows])
                        return
                    if path == "/api/days":
                        rows = db.execute("SELECT * FROM days ORDER BY position, day_id").fetchall()
                        self.respond(200, [day_row(db, row) for row in rows])
                        return
                    if path == "/api/images":
                        query = parse_qs(parsed.query)
                        page = max(1, min(100000, int(query.get("page", ["1"])[0])))
                        limit = max(1, min(5000, int(query.get("limit", [str(PAGE_SIZE)])[0])))
                        search = query.get("q", [""])[0].strip()
                        source = query.get("source", [""])[0].strip()
                        status = query.get("status", [""])[0].strip()
                        phase = query.get("phase", [""])[0].strip()
                        chapter = query.get("chapter", [""])[0].strip()
                        day = query.get("day", [""])[0].strip()
                        conditions = ["i.available = 1"]
                        params: list[object] = []
                        if search:
                            conditions.append("(i.filename LIKE ? OR i.source LIKE ? OR i.time_label LIKE ? OR i.note LIKE ? OR i.tags_json LIKE ?)")
                            params.extend([f"%{search}%"] * 5)
                        if source:
                            conditions.append("i.source = ?")
                            params.append(source)
                        if status:
                            conditions.append("i.status = ?")
                            params.append(status)
                        if phase:
                            try:
                                phase_number = int(phase)
                            except ValueError as exc:
                                raise ValueError("抽帧组编号无效") from exc
                            if phase_number < 0 or phase_number > 999:
                                raise ValueError("抽帧组编号无效")
                            conditions.append("instr(i.filename, ?) > 0")
                            params.append(f"__phase_{phase_number:02d}_offset_")
                        if chapter:
                            if chapter == "unassigned":
                                conditions.append("NOT EXISTS (SELECT 1 FROM chapter_images ci WHERE ci.image_id = i.image_id)")
                            else:
                                try:
                                    chapter_id = int(chapter)
                                except ValueError as exc:
                                    raise ValueError("Chapter 编号无效") from exc
                                conditions.append("EXISTS (SELECT 1 FROM chapter_images ci WHERE ci.image_id = i.image_id AND ci.chapter_id = ?)")
                                params.append(chapter_id)
                        if day:
                            if day == "unassigned":
                                conditions.append("EXISTS (SELECT 1 FROM chapter_images day_ci JOIN chapters day_c ON day_c.chapter_id = day_ci.chapter_id WHERE day_ci.image_id = i.image_id AND day_c.day_id IS NULL)")
                            else:
                                try:
                                    day_id = int(day)
                                except ValueError as exc:
                                    raise ValueError("Day 编号无效") from exc
                                conditions.append("EXISTS (SELECT 1 FROM chapter_images day_ci JOIN chapters day_c ON day_c.chapter_id = day_ci.chapter_id WHERE day_ci.image_id = i.image_id AND day_c.day_id = ?)")
                                params.append(day_id)
                        where = " AND ".join(conditions)
                        total = db.execute(f"SELECT COUNT(*) FROM images i WHERE {where}", params).fetchone()[0]
                        order_by = (
                            "CASE WHEN i.time_label = '' THEN 1 ELSE 0 END, i.time_label, i.source, i.filename COLLATE NOCASE"
                            if chapter and chapter != "unassigned"
                            else "i.source, CASE WHEN i.time_label = '' THEN 1 ELSE 0 END, i.time_label, i.filename COLLATE NOCASE"
                        )
                        rows = db.execute(
                            f"""SELECT i.*,
                                EXISTS(SELECT 1 FROM chapter_images ci WHERE ci.image_id=i.image_id) AS assigned,
                                (SELECT COUNT(*) FROM chapter_images ci WHERE ci.image_id=i.image_id) AS chapter_count
                                FROM images i LEFT JOIN chapter_images ci ON ci.image_id=i.image_id AND ci.chapter_id=?
                                WHERE {where}
                                ORDER BY {order_by}
                                LIMIT ? OFFSET ?""",
                            [int(chapter) if chapter and chapter != "unassigned" else -1, *params, limit, (page - 1) * limit],
                        ).fetchall()
                        self.respond(200, {"items": [image_row(row) for row in rows], "total": total, "page": page, "limit": limit, "has_more": page * limit < total})
                        return
                    if path == "/api/export.json":
                        query = parse_qs(parsed.query)
                        export = build_export_data(
                            db,
                            query.get("day", [""])[0],
                            query.get("phase", [""])[0],
                        )
                        raw = json.dumps(export, ensure_ascii=False, indent=2).encode("utf-8")
                        filename_parts = ["day"]
                        if export["filters"]["day_id"] is not None:
                            filename_parts.append(str(export["filters"]["day_id"]))
                        else:
                            filename_parts.append("all")
                        if export["filters"]["phase"] is not None:
                            filename_parts.append(f"phase-{export['filters']['phase']:02d}")
                        filename_parts.append("export.json")
                        filename = "-".join(filename_parts)
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Disposition", f"attachment; filename={filename}")
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                self.respond(404, {"error": "接口不存在"})
                return
            media_match = re.fullmatch(r"/media/(\d+)", path)
            if media_match:
                target = safe_image_path(int(media_match.group(1)))
                if not target:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                size = target.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with target.open("rb") as file:
                    while chunk := file.read(1024 * 1024):
                        self.wfile.write(chunk)
                return
            static = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/viewer": ("viewer.html", "text/html; charset=utf-8"),
                "/viewer/": ("viewer.html", "text/html; charset=utf-8"),
                "/viewer.html": ("viewer.html", "text/html; charset=utf-8"),
                "/chapters": ("viewer.html", "text/html; charset=utf-8"),
                "/chapters/": ("viewer.html", "text/html; charset=utf-8"),
                "/standalone": ("../Chapter范例查看器.html", "text/html; charset=utf-8"),
                "/Chapter范例查看器.html": ("../Chapter范例查看器.html", "text/html; charset=utf-8"),
                "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/viewer.css": ("viewer.css", "text/css; charset=utf-8"),
                "/viewer.js": ("viewer.js", "text/javascript; charset=utf-8"),
            }
            if path not in static:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            filename, content_type = static[path]
            raw = (WEB_ROOT / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
            self.respond(400, {"error": str(exc)})

    def do_POST(self) -> None:
        self.mutate("POST")

    def do_PATCH(self) -> None:
        self.mutate("PATCH")

    def do_DELETE(self) -> None:
        self.mutate("DELETE")

    def mutate(self, method: str) -> None:
        if not self.mutation_allowed():
            self.respond(403, {"error": "仅允许从本地页面修改数据"})
            return
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/import-file" and method == "POST":
                self.receive_import_file(parsed)
                return
            data = self.body(32 * 1024 * 1024) if path == "/api/import-structure" else (
                self.body() if method != "DELETE" or path.endswith("/images") else {}
            )
            with DB_LOCK, connection() as db:
                if path == "/api/scan" and method == "POST":
                    self.respond(200, scan_images())
                    return
                if path == "/api/import-complete" and method == "POST":
                    self.respond(200, scan_images())
                    return
                if path == "/api/import-structure" and method == "POST":
                    self.respond(201, import_structured_result(db, data))
                    return
                if path == "/api/export-folder" and method == "POST":
                    export = build_export_data(db, data.get("day"), data.get("phase"))
                    folder = write_export_folder(export)
                    self.respond(201, {
                        "path": str(folder),
                        "chapter_count": len(export["chapters"]),
                        "image_count": export["exported_image_count"],
                    })
                    return
                chapter_groups_match = re.fullmatch(r"/api/chapters/(\d+)/groups", path)
                if chapter_groups_match and method == "POST":
                    chapter_id = int(chapter_groups_match.group(1))
                    if not db.execute("SELECT 1 FROM chapters WHERE chapter_id=?", (chapter_id,)).fetchone():
                        self.respond(404, {"error": "Chapter 不存在"})
                        return
                    position = db.execute(
                        "SELECT COALESCE(MAX(position),0)+1 FROM scene_groups WHERE chapter_id=?", (chapter_id,)
                    ).fetchone()[0]
                    cursor = db.execute(
                        "INSERT INTO scene_groups(chapter_id,title,description,position,updated_at) VALUES(?,?,?,?,?)",
                        (chapter_id, clean_text(data.get("title"), 120, required=True), clean_text(data.get("description"), 2000), position, now()),
                    )
                    self.respond(201, {"group_id": cursor.lastrowid, "chapter_id": chapter_id})
                    return
                scene_group_match = re.fullmatch(r"/api/scene-groups/(\d+)", path)
                if scene_group_match:
                    group_id = int(scene_group_match.group(1))
                    existing_group = db.execute(
                        "SELECT * FROM scene_groups WHERE group_id=?", (group_id,)
                    ).fetchone()
                    if not existing_group:
                        self.respond(404, {"error": "风貌组不存在"})
                        return
                    if method == "PATCH":
                        title = clean_text(data.get("title", existing_group["title"]), 120, required=True)
                        description = clean_text(data.get("description", existing_group["description"]), 2000)
                        try:
                            position = int(data.get("position", existing_group["position"]))
                        except (TypeError, ValueError) as exc:
                            raise ValueError("风貌组顺序无效") from exc
                        if position < 1 or position > 10000:
                            raise ValueError("风貌组顺序无效")
                        db.execute(
                            "UPDATE scene_groups SET title=?,description=?,position=?,updated_at=? WHERE group_id=?",
                            (title, description, position, now(), group_id),
                        )
                        self.respond(200, {"group_id": group_id, "position": position})
                        return
                    if method == "DELETE":
                        db.execute("DELETE FROM scene_groups WHERE group_id=?", (group_id,))
                        self.respond(200, {"deleted": group_id})
                        return
                group_images_match = re.fullmatch(r"/api/scene-groups/(\d+)/images", path)
                if group_images_match:
                    group_id = int(group_images_match.group(1))
                    group = db.execute("SELECT chapter_id FROM scene_groups WHERE group_id=?", (group_id,)).fetchone()
                    if not group:
                        self.respond(404, {"error": "风貌组不存在"})
                        return
                    image_ids = data.get("image_ids", [])
                    if not isinstance(image_ids, list) or len(image_ids) > 4000:
                        raise ValueError("图片列表格式错误")
                    ids = list(dict.fromkeys(int(value) for value in image_ids))
                    if method == "POST":
                        if not ids:
                            raise ValueError("请选择 Chapter 图片后再加入风貌组")
                        placeholders = ",".join("?" for _ in ids)
                        members = {
                            row["image_id"] for row in db.execute(
                                f"SELECT image_id FROM chapter_images WHERE chapter_id=? AND image_id IN ({placeholders})",
                                [group["chapter_id"], *ids],
                            )
                        }
                        if members != set(ids):
                            raise ValueError("只能将当前 Chapter 已收录的图片加入风貌组")
                        position = db.execute(
                            "SELECT COALESCE(MAX(position),0) FROM scene_group_images WHERE group_id=?", (group_id,)
                        ).fetchone()[0]
                        stamp = now()
                        for image_id in ids:
                            position += 1
                            db.execute(
                                """INSERT INTO scene_group_images(chapter_id,group_id,image_id,position,added_at)
                                   VALUES(?,?,?,?,?)
                                   ON CONFLICT(chapter_id,image_id) DO UPDATE SET
                                     group_id=excluded.group_id,position=excluded.position,added_at=excluded.added_at""",
                                (group["chapter_id"], group_id, image_id, position, stamp),
                            )
                        self.respond(200, {"group_id": group_id, "assigned": len(ids)})
                        return
                    if method == "DELETE":
                        if ids:
                            placeholders = ",".join("?" for _ in ids)
                            cursor = db.execute(
                                f"DELETE FROM scene_group_images WHERE group_id=? AND image_id IN ({placeholders})",
                                [group_id, *ids],
                            )
                            removed = cursor.rowcount
                        else:
                            removed = 0
                        self.respond(200, {"group_id": group_id, "removed": removed})
                        return
                if path == "/api/days" and method == "POST":
                    position = db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM days").fetchone()[0]
                    cursor = db.execute(
                        "INSERT INTO days(title, intro, position, updated_at) VALUES (?, ?, ?, ?)",
                        (clean_text(data.get("title"), 120, required=True), clean_text(data.get("intro"), 2000), position, now()),
                    )
                    self.respond(201, {"day_id": cursor.lastrowid})
                    return
                day_match = re.fullmatch(r"/api/days/(\d+)", path)
                if day_match:
                    day_id = int(day_match.group(1))
                    if not db.execute("SELECT 1 FROM days WHERE day_id = ?", (day_id,)).fetchone():
                        self.respond(404, {"error": "Day 不存在"})
                        return
                    if method == "PATCH":
                        db.execute(
                            "UPDATE days SET title = ?, intro = ?, updated_at = ? WHERE day_id = ?",
                            (clean_text(data.get("title"), 120, required=True), clean_text(data.get("intro"), 2000), now(), day_id),
                        )
                        self.respond(200, {"day_id": day_id})
                        return
                    if method == "DELETE":
                        db.execute("DELETE FROM days WHERE day_id = ?", (day_id,))
                        self.respond(200, {"deleted": day_id})
                        return
                if path == "/api/chapters" and method == "POST":
                    title = clean_text(data.get("title"), 120, required=True)
                    stamp = now()
                    day_id = clean_day_id(db, data.get("day_id"))
                    if day_id is None:
                        position = db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM chapters WHERE day_id IS NULL").fetchone()[0]
                    else:
                        position = db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM chapters WHERE day_id=?", (day_id,)).fetchone()[0]
                    cursor = db.execute(
                        "INSERT INTO chapters(title, intro, tags_json, day_id, position, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (title, clean_text(data.get("intro"), 2000), clean_tags(data.get("tags")), day_id, position, stamp),
                    )
                    self.respond(201, {"chapter_id": cursor.lastrowid})
                    return
                chapter_match = re.fullmatch(r"/api/chapters/(\d+)", path)
                if chapter_match:
                    chapter_id = int(chapter_match.group(1))
                    if method == "PATCH":
                        existing_chapter = db.execute("SELECT day_id, position FROM chapters WHERE chapter_id=?", (chapter_id,)).fetchone()
                        if not existing_chapter:
                            self.respond(404, {"error": "Chapter 不存在"})
                            return
                        title = clean_text(data.get("title"), 120, required=True)
                        day_id = clean_day_id(db, data["day_id"]) if "day_id" in data else existing_chapter["day_id"]
                        try:
                            position = int(data.get("position", existing_chapter["position"]))
                        except (TypeError, ValueError) as exc:
                            raise ValueError("Chapter 顺序无效") from exc
                        if position < 1 or position > 10000:
                            raise ValueError("Chapter 顺序无效")
                        db.execute(
                            "UPDATE chapters SET title=?, intro=?, tags_json=?, day_id=?, position=?, updated_at=? WHERE chapter_id=?",
                            (title, clean_text(data.get("intro"), 2000), clean_tags(data.get("tags")), day_id, position, now(), chapter_id),
                        )
                        cover_phase = None
                        if "cover_image_id" in data and data.get("cover_image_id") not in ("", None):
                            cover_id = int(data["cover_image_id"])
                            cover = db.execute(
                                """SELECT i.filename FROM chapter_images ci
                                   JOIN images i ON i.image_id = ci.image_id
                                   WHERE ci.chapter_id=? AND ci.image_id=? AND i.available=1""",
                                (chapter_id, cover_id),
                            ).fetchone()
                            if not cover:
                                raise ValueError("只能选择当前 Chapter 中的图片作为封面")
                            phase_match = PHASE_PATTERN.search(cover["filename"])
                            if not phase_match:
                                raise ValueError("该图片没有可识别的偏移组，不能设为分组封面")
                            cover_phase = int(phase_match.group(1))
                            requested_phase = data.get("cover_phase")
                            if requested_phase not in ("", None) and int(requested_phase) != cover_phase:
                                raise ValueError("封面图片必须来自当前偏移组")
                            db.execute(
                                """INSERT INTO chapter_covers(chapter_id, phase, image_id, updated_at)
                                   VALUES (?, ?, ?, ?)
                                   ON CONFLICT(chapter_id, phase) DO UPDATE SET
                                     image_id=excluded.image_id, updated_at=excluded.updated_at""",
                                (chapter_id, cover_phase, cover_id, now()),
                            )
                            db.execute(
                                "UPDATE chapters SET cover_image_id=?, updated_at=? WHERE chapter_id=?",
                                (cover_id, now(), chapter_id),
                            )
                        self.respond(200, {"chapter_id": chapter_id, "cover_phase": cover_phase})
                        return
                    if method == "DELETE":
                        db.execute("DELETE FROM chapters WHERE chapter_id=?", (chapter_id,))
                        self.respond(200, {"deleted": chapter_id})
                        return
                chapter_images_match = re.fullmatch(r"/api/chapters/(\d+)/images", path)
                if chapter_images_match:
                    chapter_id = int(chapter_images_match.group(1))
                    if not db.execute("SELECT 1 FROM chapters WHERE chapter_id=?", (chapter_id,)).fetchone():
                        self.respond(404, {"error": "Chapter 不存在"})
                        return
                    image_ids = data.get("image_ids", [])
                    if not isinstance(image_ids, list) or len(image_ids) > 4000:
                        raise ValueError("图片列表格式错误")
                    ids = list(dict.fromkeys(int(value) for value in image_ids))
                    if method == "POST":
                        position = db.execute("SELECT COALESCE(MAX(position), 0) FROM chapter_images WHERE chapter_id=?", (chapter_id,)).fetchone()[0]
                        stamp = now()
                        for image_id in ids:
                            position += 1
                            db.execute(
                                "INSERT OR IGNORE INTO chapter_images(chapter_id, image_id, position, added_at) VALUES (?, ?, ?, ?)",
                                (chapter_id, image_id, position, stamp),
                            )
                        ensure_chapter_covers(db, chapter_id)
                        self.respond(200, {"chapter_id": chapter_id, "added": len(ids)})
                        return
                    if method == "DELETE":
                        db.executemany("DELETE FROM chapter_images WHERE chapter_id=? AND image_id=?", [(chapter_id, image_id) for image_id in ids])
                        if ids:
                            placeholders = ",".join("?" for _ in ids)
                            db.execute(
                                f"DELETE FROM chapter_covers WHERE chapter_id=? AND image_id IN ({placeholders})",
                                [chapter_id, *ids],
                            )
                            db.execute(
                                f"UPDATE chapters SET cover_image_id=NULL, updated_at=? WHERE chapter_id=? AND cover_image_id IN ({placeholders})",
                                [now(), chapter_id, *ids],
                            )
                            ensure_chapter_covers(db, chapter_id)
                        self.respond(200, {"chapter_id": chapter_id, "removed": len(ids)})
                        return
                    if method == "PATCH":
                        order = data.get("image_ids", [])
                        db.execute("UPDATE chapter_images SET position=position+100000 WHERE chapter_id=?", (chapter_id,))
                        for position, image_id in enumerate(order, start=1):
                            db.execute("UPDATE chapter_images SET position=? WHERE chapter_id=? AND image_id=?", (position, chapter_id, int(image_id)))
                        self.respond(200, {"chapter_id": chapter_id, "ordered": len(order)})
                        return
                image_match = re.fullmatch(r"/api/images/(\d+)", path)
                if image_match and method == "PATCH":
                    image_id = int(image_match.group(1))
                    status = clean_text(data.get("status", "未处理"), 20)
                    if status not in IMAGE_STATUSES:
                        raise ValueError("图片状态无效")
                    if not db.execute("SELECT 1 FROM images WHERE image_id=?", (image_id,)).fetchone():
                        self.respond(404, {"error": "图片不存在"})
                        return
                    db.execute(
                        "UPDATE images SET status=?, tags_json=?, note=?, updated_at=? WHERE image_id=?",
                        (status, clean_tags(data.get("tags")), clean_text(data.get("note"), 1000), now(), image_id),
                    )
                    self.respond(200, {"image_id": image_id})
                    return
                if path == "/api/images/bulk" and method == "PATCH":
                    ids = data.get("image_ids", [])
                    if not isinstance(ids, list) or not ids or len(ids) > 4000:
                        raise ValueError("请选择图片后再批量标记")
                    status = clean_text(data.get("status"), 20)
                    if status not in IMAGE_STATUSES:
                        raise ValueError("图片状态无效")
                    stamp = now()
                    db.executemany("UPDATE images SET status=?, updated_at=? WHERE image_id=?", [(status, stamp, int(image_id)) for image_id in set(ids)])
                    self.respond(200, {"updated": len(set(ids))})
                    return
            self.respond(404, {"error": "接口不存在"})
        except (sqlite3.Error, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
            self.respond(400, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="本地抽帧图片整理平台")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()
    initialize()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"已索引 {IMAGE_ROOT} 中的图片。整理平台：http://{HOST}:{PORT}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("图片整理平台已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
