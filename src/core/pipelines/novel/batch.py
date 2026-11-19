"""Build a chapter index and run resumable extraction against an OpenAI-compatible server."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    from .model_client import MODEL, PROVIDER, openai_completion
    from .usage import output_limit, record_usage, request_started
except ImportError:
    # The standalone Qwen batch worker is also deployed as a single script.
    MODEL = "qwen3.8-27b-bf16"
    PROVIDER = "qwen"

    def request_started():
        pass

    def record_usage(model, usage):
        pass

    def output_limit(requested):
        return requested

HEADING = re.compile(r"(?:☆、\s*)?第[0-9０-９零〇一二三四五六七八九十百千万两]+章\s*.*")
VOLUME = re.compile(r"第[0-9一二三四五六七八九十百千万]+卷\s+.*")
PROMPT = """分析这一章小说正文。只根据本章，区分现实发生、回忆、传闻和猜测。只返回 JSON。
summary：中文约150字，聚焦核心人物及其动机、行动、结果。key_events：2至4条简明的核心事件。
characters：提取本章全部有名字或可独立识别的角色，包括次要人物、回忆和只被提及的人物。无名且可识别的角色以原文身份称呼表示。不要把群体、地点、物件当人物。每条包含 name（原文名字）、aliases（本章有依据的其他称呼，数组）、role（本章行为或被提及的原因，标出仅提及或回忆）。章内合并明确是同一人的别称，不猜测身份。"""

STRING_LIST = {"type": "array", "items": {"type": "string"}}
PERSON_FIELDS = {"name": {"type": "string"}, "aliases": STRING_LIST, "role": {"type": "string"}}
CHAPTER_FIELDS = {
    "summary": {"type": "string"},
    "key_events": STRING_LIST,
    "characters": {
        "type": "array",
        "items": {"type": "object", "properties": PERSON_FIELDS,
                  "required": list(PERSON_FIELDS), "additionalProperties": False},
    },
}
CHAPTER_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "chapter_analysis", "strict": True,
        "schema": {"type": "object", "properties": CHAPTER_FIELDS,
                   "required": list(CHAPTER_FIELDS), "additionalProperties": False},
    },
}


def read_source(path):
    rows = []
    label, body, volume = None, [], ""
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        heading = line.strip()
        marked = heading.startswith("☆、")
        numbered = (marked or line == line.lstrip()) and len(heading) <= 80 and HEADING.fullmatch(heading)
        if marked and heading == "☆、结束语":
            break
        if numbered or VOLUME.fullmatch(heading):
            if label is not None:
                rows.append((label, volume, "\n".join(body).strip()))
                label, body = None, []
            if numbered:
                label = heading.removeprefix("☆、").strip()
            else:
                volume = heading
        elif label is not None:
            body.append(line)
    if label is not None:
        rows.append((label, volume, "\n".join(body).strip()))
    return [row for row in rows if row[2]]

def build(source, database):
    from src.core.base import db_init

    if database.exists():
        raise FileExistsError(database)
    rows = read_source(source)
    if not rows or "大结局" not in rows[-1][0]:
        raise ValueError("Chapter index did not reach the source ending")
    db_init.db_path = str(database)
    db_init.init_db()
    with sqlite3.connect(database) as conn:
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
            "INSERT INTO chapters(chapter_index,title,volume,content) VALUES (?,?,?,?)",
            ((idx, *row) for idx, row in enumerate(rows, 1)),
        )
    print(f"{len(rows)} chapters, {rows[0][0]} … {rows[-1][0]}", flush=True)

class CompletionLengthError(ValueError):
    """The server stopped generation at the configured output limit."""


def completion(base, messages, tokens, structured=False, response_format=None):
    if PROVIDER == "openai":
        return openai_completion(
            messages, tokens, CHAPTER_FORMAT if structured else response_format, CompletionLengthError
        )
    data = {
        "model": MODEL, "messages": messages, "max_tokens": output_limit(tokens),
        "temperature": 0, "chat_template_kwargs": {"enable_thinking": False},
    }
    if structured:
        data["response_format"] = CHAPTER_FORMAT
    if response_format is not None:
        data["response_format"] = response_format
    request = Request(
        base.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(data, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            request_started()
            with urlopen(request, timeout=240) as response:
                result = json.load(response)
            record_usage(result.get("model", MODEL), result.get("usage"))
            choice = result["choices"][0]
            break
        except HTTPError as error:
            if attempt == 2 or error.code not in {429, 500, 502, 503, 504}:
                raise
            time.sleep(2 ** (attempt + 1))
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))
    if choice["finish_reason"] == "length":
        raise CompletionLengthError("Incomplete response: length")
    if choice["finish_reason"] != "stop":
        raise ValueError(f"Incomplete response: {choice['finish_reason']}")
    return choice["message"]["content"]

def extract(base, row):
    index, title, content = row
    if os.getloadavg()[0] > 64:
        raise RuntimeError("Host load above 64")
    payload = json.loads(completion(base, [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": title + "\n\n" + content},
    ], 3500, structured=True))
    summary = payload["summary"].strip()
    events = payload["key_events"]
    people = payload["characters"]
    if not 65 <= len(summary) <= 270 or not events or not all(isinstance(e, str) and e.strip() for e in events):
        raise ValueError("Invalid summary or key events")
    if not people:
        raise ValueError("Missing chapter characters")
    for person in people:
        if not person["name"].strip() or not isinstance(person["role"], str):
            raise ValueError("Invalid character")
        if not isinstance(person["aliases"], list) or not all(isinstance(alias, str) for alias in person["aliases"]):
            raise ValueError("Invalid aliases")
    return {"index": index, "summary": summary, "events": events, "people": people}

def run(database, output, base, workers):
    with sqlite3.connect(database) as conn:
        rows = conn.execute("SELECT chapter_index,title,content FROM chapters ORDER BY chapter_index").fetchall()
    done = set()
    if output.exists():
        with output.open() as result_file:
            done.update(json.loads(line)["index"] for line in result_file)
    with output.open("a", buffering=1) as result_file, ThreadPoolExecutor(max_workers=workers) as pool:
        while len(done) < len(rows):
            pending = iter(row for row in rows if row[0] not in done)
            from concurrent.futures import FIRST_COMPLETED, wait

            active = {}
            while True:
                while len(active) < workers and os.getloadavg()[0] <= 64:
                    row = next(pending, None)
                    if row is None:
                        break
                    active[pool.submit(extract, base, row)] = row[0]
                if not active:
                    if os.getloadavg()[0] > 64:
                        time.sleep(60)
                        continue
                    break
                finished, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in finished:
                    idx = active.pop(future)
                    try:
                        result = future.result()
                    except Exception as error:
                        print(f"Chapter {idx}: {type(error).__name__}: {error}", flush=True)
                        continue
                    result_file.write(json.dumps(result, ensure_ascii=False) + "\n")
                    done.add(idx)
                    if len(done) <= 5 or len(done) % 25 == 0:
                        print(f"{len(done)}/{len(rows)} complete", flush=True)
            if len(done) < len(rows):
                time.sleep(60)

def ingest(database, output):
    with output.open("rb") as result_file, sqlite3.connect(database, timeout=15) as conn:
        for raw in result_file:
            if not raw.endswith(b"\n"):
                break
            row = json.loads(raw)
            idx = row["index"]
            if conn.execute("SELECT status FROM chapters WHERE chapter_index=?", (idx,)).fetchone() == ("completed",):
                continue
            conn.execute(
                "UPDATE chapters SET summary=?,key_events=?,status='completed' WHERE chapter_index=?",
                (row["summary"], json.dumps(row["events"], ensure_ascii=False), idx),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO chapter_people VALUES (?,?,?,?)",
                ((idx, p["name"], json.dumps(p["aliases"], ensure_ascii=False), p["role"]) for p in row["people"]),
            )

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "run", "ingest"])
    parser.add_argument("database", type=Path)
    parser.add_argument("file", type=Path)
    parser.add_argument("--base", default="http://127.0.0.1:18271")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    if args.action == "build":
        build(args.file, args.database)
    elif args.action == "run":
        run(args.database, args.file, args.base, args.workers)
    else:
        ingest(args.database, args.file)

if __name__ == "__main__":
    main()
