"""Search indexed chapter people and analysis text."""
import json
import unicodedata

from .qa import terms


def normalize(text):
    return unicodedata.normalize("NFKC", text).casefold().strip()


def search(conn, query, mode):
    query = normalize(query)
    if not query:
        return []
    chapters = {
        row["chapter_index"]: row
        for row in conn.execute("SELECT chapter_index,summary,key_events,status,content FROM chapters")
    }
    ranked = []
    if mode == "people":
        matches = {}
        has_people = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chapter_people'"
        ).fetchone()
        if has_people:
            for row in conn.execute("SELECT chapter_index,name,aliases,role FROM chapter_people"):
                names = [row["name"], *json.loads(row["aliases"])]
                score = max((2 if query == normalize(name) else 1 for name in names if query in normalize(name)), default=0)
                if score:
                    matches.setdefault(row["chapter_index"], []).append((score, row["name"], row["role"]))
        for index, chapter in chapters.items():
            if chapter["status"] == "indexed" and query in normalize(chapter["content"]):
                text = chapter["content"]
                position = max(0, normalize(text).find(query) - 24)
                matches.setdefault(index, []).append((1, query, "原文提及 · " + text[position:position + 100]))
        for index, people in matches.items():
            score, name, role = max(people, key=lambda item: item[0])
            ranked.append((score, index, f"{name} · {role}"))
    else:
        words = terms(query)
        for index, chapter in chapters.items():
            passages = [chapter["summary"] or ""]
            if chapter["status"] == "indexed":
                passages.extend(chapter["content"].splitlines())
            try:
                events = json.loads(chapter["key_events"] or "[]")
            except ValueError:
                events = []
            if isinstance(events, list):
                passages.extend(event for event in events if isinstance(event, str))
            best = (0, "")
            for passage in passages:
                target = normalize(passage)
                overlap = len(words & terms(target))
                if query in target:
                    score = 2 + overlap / max(len(words), 1)
                elif overlap >= min(2, len(words)) and overlap >= len(words) * 0.35:
                    score = overlap / max(len(words), 1)
                else:
                    continue
                if score > best[0]:
                    offset = max(0, target.find(query) - 24)
                    preview = passage[offset:offset + 150]
                    best = (score, ("…" if offset else "") + preview + ("…" if len(passage) > offset + 150 else ""))
            if best[0]:
                ranked.append((best[0], index, best[1]))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [{"chapter_index": index, "snippet": snippet} for _, index, snippet in ranked]
