"""把离线 HTML 导出的悉尼标注合并回平台数据库。"""
import argparse
import json
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("json_file", type=Path)
parser.add_argument("--db", type=Path, default=Path(__file__).parent / "data" / "图片整理.sqlite3")
args = parser.parse_args()
payload = json.loads(args.json_file.read_text(encoding="utf-8"))
if payload.get("version") != 1:
    raise SystemExit("标注文件版本不支持")
db = sqlite3.connect(args.db)
db.execute("PRAGMA foreign_keys=ON")
image_ids = {r[0] for r in db.execute("SELECT image_id FROM images WHERE source=?", ("悉尼",))}
chapter_ids = {r[0] for r in db.execute("SELECT DISTINCT ci.chapter_id FROM chapter_images ci JOIN images i ON i.image_id=ci.image_id WHERE i.source=?", ("悉尼",))}
if {x["image_id"] for x in payload["images"]} != image_ids:
    raise SystemExit("图片集合与数据库不一致，已停止导入")
if {x["chapter_id"] for x in payload["chapters"]} != chapter_ids:
    raise SystemExit("Chapter 集合与数据库不一致，已停止导入")
try:
    with db:
        for x in payload["days"]:
            db.execute("UPDATE days SET title=?,intro=? WHERE day_id=?", (x["title"], x.get("intro", ""), x["day_id"]))
        for x in payload["chapters"]:
            db.execute("UPDATE chapters SET title=?,intro=? WHERE chapter_id=?", (x["title"], x.get("intro", ""), x["chapter_id"]))
        for x in payload["images"]:
            db.execute("UPDATE images SET status=?,tags_json=?,note=? WHERE image_id=?", (x["status"], x.get("tags_json", "[]"), x.get("note", ""), x["image_id"]))
        db.executemany("DELETE FROM chapter_images WHERE chapter_id=?", [(i,) for i in chapter_ids])
        for x in payload["chapter_images"]:
            if x["chapter_id"] not in chapter_ids or x["image_id"] not in image_ids:
                raise ValueError("Chapter 图片归属越界")
            db.execute("INSERT INTO chapter_images(chapter_id,image_id,position,added_at) VALUES(?,?,?,datetime('now'))", (x["chapter_id"], x["image_id"], x.get("position", 0)))
        db.executemany("DELETE FROM scene_groups WHERE chapter_id=?", [(i,) for i in chapter_ids])
        group_map = {}
        for x in payload.get("groups", []):
            if x["chapter_id"] not in chapter_ids:
                raise ValueError("风貌组归属越界")
            cur = db.execute("INSERT INTO scene_groups(chapter_id,title,description,position,updated_at) VALUES(?,?,?,?,datetime('now'))", (x["chapter_id"], x["title"], x.get("description", ""), x.get("position", 0)))
            group_map[x["group_id"]] = (cur.lastrowid, x["chapter_id"])
        for x in payload.get("group_images", []):
            group_id, chapter_id = group_map[x["group_id"]]
            db.execute("INSERT INTO scene_group_images(chapter_id,group_id,image_id,position,added_at) VALUES(?,?,?,?,datetime('now'))", (chapter_id, group_id, x["image_id"], x.get("position", 0)))
        db.executemany("DELETE FROM chapter_covers WHERE chapter_id=?", [(i,) for i in chapter_ids])
        for x in payload.get("covers", []):
            if x["chapter_id"] not in chapter_ids or x["image_id"] not in image_ids:
                raise ValueError("封面归属越界")
            db.execute("INSERT INTO chapter_covers(chapter_id,phase,image_id,updated_at) VALUES(?,?,?,datetime('now'))", (x["chapter_id"], x["phase"], x["image_id"]))
except Exception:
    db.close()
    raise
db.close()
print("悉尼离线标注已导入")