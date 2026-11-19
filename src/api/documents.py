"""Local document library with deterministic, CPU-only preprocessing."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock

from src.core.base.db_init import init_db
from src.core.pipelines.novel.batch import HEADING, VOLUME

IMPORT_LOCK = Lock()


def library_root(default_database):
    return Path(default_database).resolve().parent / "library"


def resolve_database(default_database, document_id):
    if not document_id or document_id == "default":
        return str(default_database)
    if not re.fullmatch(r"[a-f0-9]{24}", document_id):
        raise ValueError("文档不存在")
    path = library_root(default_database) / document_id / "book.db"
    if not path.exists() or not path.with_name("document.json").exists():
        raise ValueError("文档不存在")
    return str(path)


def describe(database, metadata):
    with sqlite3.connect(database) as conn:
        total, analyzed = conn.execute(
            "SELECT count(*),count(CASE WHEN status='completed' THEN 1 END) FROM chapters"
        ).fetchone()
    return {**metadata, "chapter_count": total, "analyzed_count": analyzed, "status": "ready"}


def list_documents(default_database):
    result = []
    if Path(default_database).exists():
        result.append(describe(default_database, {
            "id": "default", "title": "超品相师", "author": "九灯和善", "format": "TXT",
        }))
    root = library_root(default_database)
    if root.exists():
        for directory in sorted(root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            metadata = directory / "document.json"
            if metadata.exists() and (directory / "book.db").exists():
                result.append(describe(directory / "book.db", json.loads(metadata.read_text(encoding="utf-8"))))
    return result


def split_sections(text, filename):
    sections = []
    title, volume, body = "前言", "", []

    def flush():
        content = "\n".join(body).strip()
        if content:
            chunks = []
            while len(content) > 6000:
                cut = content.rfind("\n", 3000, 6000)
                cut = cut if cut > 0 else 6000
                chunks.append(content[:cut])
                content = content[cut:]
            if content.strip():
                chunks.append(content.strip())
            for index, chunk in enumerate(chunks, 1):
                label = title if len(chunks) == 1 else f"{title} · 第{index}部分"
                sections.append((label, volume, chunk))
        body.clear()

    for line in text.splitlines():
        clean = line.strip()
        if len(clean) <= 100 and (HEADING.fullmatch(clean) or re.match(r"^#{1,4}\s+\S", clean)):
            flush()
            title = re.sub(r"^#{1,4}\s+", "", clean).removeprefix("☆、").strip()
        elif VOLUME.fullmatch(clean):
            flush()
            volume = clean
        else:
            body.append(line)
    if title == "前言" and not sections:
        title = Path(filename).stem
    flush()
    return sections


def import_document(default_database, filename, raw):
    suffix = Path(filename).suffix.lower()
    if suffix not in {".txt", ".md"}:
        raise ValueError("目前支持 TXT 和 Markdown 文档")
    text = None
    encodings = ["utf-16"] if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ["utf-8-sig", "gb18030"]
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text or not text.strip() or "\x00" in text:
        raise ValueError("未读取到有效文本，请检查文件编码")
    document_id = hashlib.sha256(text.encode()).hexdigest()[:24]
    directory = library_root(default_database) / document_id
    with IMPORT_LOCK:
        database = directory / "book.db"
        if database.exists() and (directory / "document.json").exists():
            metadata = json.loads((directory / "document.json").read_text(encoding="utf-8"))
            return describe(database, metadata)
        sections = split_sections(text, filename)
        if not sections:
            raise ValueError("文档没有可索引的正文")
        directory.mkdir(parents=True, exist_ok=True)
        partial = directory / "book.partial.db"
        if partial.exists():
            partial.unlink()
        try:
            init_db(str(partial))
            with sqlite3.connect(partial) as conn:
                conn.executescript("""
                    ALTER TABLE chapters ADD COLUMN volume TEXT;
                    ALTER TABLE chapters ADD COLUMN content TEXT;
                    CREATE TABLE chapter_people (
                        chapter_index INTEGER NOT NULL, name TEXT NOT NULL,
                        aliases TEXT NOT NULL, role TEXT NOT NULL,
                        PRIMARY KEY (chapter_index, name)
                    );
                """)
                conn.executemany(
                    "INSERT INTO chapters(chapter_index,title,volume,content,status) VALUES (?,?,?,?, 'indexed')",
                    ((index, *section) for index, section in enumerate(sections, 1)),
                )
            partial.replace(database)
            (directory / "source.txt").write_text(text, encoding="utf-8")
            metadata = {"id": document_id, "title": Path(filename).stem, "author": "", "format": suffix[1:].upper()}
            metadata_path = directory / "document.json"
            metadata_partial = directory / "document.partial.json"
            metadata_partial.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
            metadata_partial.replace(metadata_path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return describe(database, metadata)
