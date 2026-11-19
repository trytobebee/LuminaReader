"""Answer, assess missing evidence, retrieve original text, and refine."""
from collections import defaultdict
import json
from pathlib import Path
import re

from .batch import completion
from . import qa
from .usage import CURRENT

ASSESS_FIELDS = {
    "needs_original": {"type": "boolean"},
    "missing_points": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
    "search_terms": {"type": "array", "items": {"type": "string", "maxLength": 40}, "maxItems": 8},
    "chapter_indices": {"type": "array", "items": {"type": "integer"}, "maxItems": 24},
}
REFINE_FIELDS = {
    "answer": {"type": "string"},
    "cited_paragraphs": {"type": "array", "items": {"type": "string"}},
    "unresolved": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
    "needs_more": {"type": "boolean"},
    "search_terms": {"type": "array", "items": {"type": "string", "maxLength": 40}, "maxItems": 8},
}


def output_format(name, fields):
    return {"type": "json_schema", "json_schema": {
        "name": name, "strict": True, "schema": {
            "type": "object", "properties": fields,
            "required": list(fields), "additionalProperties": False,
        },
    }}


def assess(endpoint, question, history, draft, scoped):
    value = json.loads(completion(endpoint, [
        {"role": "system", "content": (
            "检查小说问答初稿的证据缺口。初稿是基于压缩资料的候选答案，不能作为事实证据。"
            "凡是人数统计、境界比较、身份争议、因果不明、材料冲突、至少N人但不完整、"
            "声称缺原文/资料不足等，必须needs_original=true。"
            "若已有充分原文依据且无争议，可为false。"
            "missing_points写具体需要核对的命题，search_terms给可在原文查找的角色名、地点或事件词，"
            "chapter_indices只能写确知的原稿顺序索引，不把原始章号当索引猜测。"
            "对话历史仅帮助确定指代，不能拿历史回答作原文证明。"
        )},
        {"role": "user", "content": json.dumps({
            "question": question, "history": qa.compact_history(history),
            "draft": draft[:10000], "has_story_scope": scoped,
        }, ensure_ascii=False)},
    ], 1600, response_format=output_format("evidence_gap", ASSESS_FIELDS)))
    if qa.needs_fact_audit(question) or re.search(r"无法确定|资料不足|尚不能|缺少.{0,12}原文|需要核对|至少[一二三四五六七八九十\d]", draft):
        value["needs_original"] = True
    return value


def search_original(conn, question, plan, selected, arc, excluded):
    rows = arc[1] if arc else conn.execute(
        "SELECT chapter_index,title,content FROM chapters ORDER BY chapter_index"
    ).fetchall()
    phrases = [term for value in plan["search_terms"]
               for term in re.split(r"[，,；;\s]+", value.strip()) if 2 <= len(term) <= 40]
    words = qa.terms(question + " " + " ".join(phrases))
    preferred = {chapter["chapter_index"] for chapter, _ in selected} | set(plan["chapter_indices"])
    ranked = []
    for row in rows:
        text = row["content"]
        score = sum(5 + len(term) for term in phrases if term in text)
        if row["chapter_index"] in preferred:
            score += 2
        if score:
            ranked.append((score, row))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["chapter_index"]))
    passages = {}
    size = 0
    for _, row in ranked[:24]:
        lines = [line for line in row["content"].splitlines() if line.strip()]
        scores = []
        for index, line in enumerate(lines):
            score = sum(5 + len(term) for term in phrases if term in line) + len(words & qa.terms(line))
            if score:
                scores.append((score, index))
        scores.sort(key=lambda pair: (-pair[0], pair[1]))
        indices = sorted({index + offset for _, index in scores[:6] for offset in (-1, 0, 1)
                          if 0 <= index + offset < len(lines)})
        for index in indices:
            ref = f"c{row['chapter_index']}p{index}"
            if ref in excluded:
                continue
            line = lines[index]
            if size + len(line) > 50000:
                continue
            passages[ref] = {
                "chapter_index": row["chapter_index"], "title": row["title"],
                "text": line,
            }
            size += len(line)
    return passages


def refine_answer(conn, endpoint, question, history, current_chapter, arc=None, on_progress=None, checkpoint=None):
    def phase(name):
        if on_progress:
            on_progress(name)

    arc = arc or (None if qa.chapter_numbers(question) else qa.resolve_arc(conn, question, history))
    if checkpoint and Path(checkpoint).exists():
        saved = json.loads(Path(checkpoint).read_text(encoding="utf-8"))
        draft, plan = saved["draft"], saved["plan"]
        meter = CURRENT.get()
        if meter and saved.get("usage"):
            meter.carry(saved["usage"])
    else:
        phase("answering")
        draft = qa.answer(conn, endpoint, question, history, current_chapter, arc_override=arc)
        phase("checking_evidence")
        plan = assess(endpoint, question, history, draft, bool(arc))
        if checkpoint:
            path = Path(checkpoint)
            path.parent.mkdir(exist_ok=True)
            temporary = path.with_suffix(".tmp")
            saved = {"draft": draft, "plan": plan}
            meter = CURRENT.get()
            if meter:
                saved["usage"] = meter.summary()
            temporary.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
    result = {"response": draft, "retrieval": {
        "performed": False, "missing_points": plan["missing_points"], "rounds": 0,
    }}
    if arc:
        result["scope"] = {
            "location": arc[0], "start": arc[1][0]["chapter_index"], "end": arc[1][-1]["chapter_index"],
        }
    if not plan["needs_original"]:
        return result
    phase("searching_original")
    if not arc and qa.needs_fact_audit(question):
        for term in plan["search_terms"]:
            arc = qa.location_arc(conn, "回顾" + term)
            if arc:
                break
    if not arc and qa.needs_fact_audit(question) and any(
        word in question for word in ("这个过程", "整个过程", "这其中", "这段", "其中", "上述", "期间")
    ) and not qa.chapter_numbers(question):
        result["response"] = "这次对话还没有确定这段剧情的范围。请补充地点或起止章节，我再检索原文核对人数。"
        result["retrieval"]["performed"] = False
        return result
    if arc and qa.needs_fact_audit(question):
        from .fact_audit import answer_from_source
        database = conn.execute("PRAGMA database_list").fetchone()[2]
        cache_directory = Path(database).resolve().parent / "reader_qa" if database else None
        audit = answer_from_source(endpoint, question, arc, cache_directory=cache_directory)
        result.update(audit)
        result["scope"] = {
            "location": arc[0], "start": arc[1][0]["chapter_index"], "end": arc[1][-1]["chapter_index"],
        }
        result["retrieval"].update(performed=True, rounds=1)
        result["coverage"] = f"在 {len(arc[1])} 章范围内检索原文，逐人核对身份、境界与死亡状态；同一人去重计数。"
        return result
    selected = qa.retrieve(conn, question, history, current_chapter)
    passages = {}
    final = None
    for round_number in range(1, 3):
        phase("searching_original")
        new = search_original(conn, question, plan, selected, arc, set(passages))
        if not new:
            break
        passages.update(new)
        phase("refining")
        final = json.loads(completion(endpoint, [
            {"role": "system", "content": (
                "依据检索到的小说原文重新回答问题。初稿和历史回答仅供定位错误，原文是唯一事实证据。"
                "先给结论，再给出处，可用Markdown表格。区分身份、境界、战力、受伤、死亡、"
                "当前事件与历史回忆，禁止借用其他小说设定。"
                "只输出整理后的答案，不输出自问自答或试算过程。"
                "每项事实应引用原始章名，cited_paragraphs仅列真正支撑答案的段落ID。"
                "尚有影响结论的证据缺口时needs_more=true，并提供针对性search_terms；"
                "无法确认的部分在答案中明确指出。"
            )},
            {"role": "user", "content": json.dumps({
                "question": question, "history": qa.compact_history(history),
                "draft_to_verify": draft[:10000], "missing_points": plan["missing_points"],
                "original_passages": passages,
            }, ensure_ascii=False)},
        ], 5000, response_format=output_format("grounded_answer", REFINE_FIELDS)))
        if not final["answer"].strip() or not final["cited_paragraphs"] or any(ref not in passages for ref in final["cited_paragraphs"]):
            raise ValueError("最终回答缺少有效的原文引用")
        result["retrieval"].update(performed=True, rounds=round_number)
        if not final["needs_more"]:
            break
        plan["search_terms"] = final["search_terms"]
    if final is None:
        result["response"] = "这次原文检索没有找到足以核实该问题的段落。请补充人物名或章节范围。"
        result["retrieval"]["performed"] = True
        return result
    grouped = defaultdict(list)
    for ref in dict.fromkeys(final["cited_paragraphs"]):
        paragraph = passages[ref]
        grouped[(paragraph["chapter_index"], paragraph["title"])].append(paragraph["text"])
    result.update(response=final["answer"], timeline=[
        {"chapter_index": index, "title": title, "event": "原文检索依据", "quotes": quotes}
        for (index, title), quotes in sorted(grouped.items())
    ])
    if final["unresolved"]:
        result["response"] += "\n\n尚未核实：" + "；".join(final["unresolved"])
    result["coverage"] = f"完成 {result['retrieval']['rounds']} 轮原文检索，引用 {len(grouped)} 章原文。"
    return result
