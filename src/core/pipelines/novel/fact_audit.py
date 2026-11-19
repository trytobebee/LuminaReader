"""Build rank and casualty answers from cited original paragraphs."""
from collections import defaultdict
import json
import hashlib
from pathlib import Path
import re

from .batch import completion
from .model_client import model_identity

PATTERN = re.compile(
    r"六品|七品|八品|九品|境界|元神|死亡|死了|身死|惨死|杀死|击杀|陨落|殒命|断臂|断.{0,6}手臂"
)
REFERENCE = {"type": "array", "items": {"type": "string"}}
RANK = {
    "name": {"type": "string"}, "level": {"type": "string"},
    "tier": {"type": "integer"}, "stage": {"type": "integer"},
    "temporal": {"type": "string", "enum": ["current", "historical", "uncertain"]},
    "evidence": REFERENCE,
}
DEATH = {
    "name": {"type": "string"},
    "state": {"type": "string", "enum": ["confirmed_dead", "injured", "uncertain"]},
    "master": {"type": "boolean"},
    "temporal": {"type": "string", "enum": ["current", "historical", "uncertain"]},
    "evidence": REFERENCE,
}


def records_schema(fields):
    return {"type": "array", "items": {
        "type": "object", "properties": fields, "required": list(fields), "additionalProperties": False,
    }}


FORMAT = {"type": "json_schema", "json_schema": {
    "name": "book_fact_ledger", "strict": True,
    "schema": {"type": "object", "properties": {
        "ranks": records_schema(RANK), "casualties": records_schema(DEATH),
    }, "required": ["ranks", "casualties"], "additionalProperties": False},
}}
PROMPT = """你是小说原文事实核对员。建立可核对的境界、伤亡台账，只依据给定原文。
范围是用户正在讨论的地点剧情，包括相关主线大战及离开转场。前后文仅用于辨识，历史回忆另列historical。
禁止以其他小说的常识推断，也禁止把获胜、战技强大或临时战力推为境界更高。
ranks只列明确给出修为的人物。tier为品级1-9，无法确定为0。
stage：未知0、初期1、中期2、后期3、大圆满4、巅峰5。仅原文明说才编码。
尝试突破、境界名称出现在章名、谈论某境界都不能证明人物已经达到。别人猜测标uncertain。
casualties覆盖所有与宗师伤亡有关的人，逐人一条。无名者以具体身份或事件编号稳定命名。
重述、旁人回顾或元神后来被吞噬都是同一个人的连续过程，不能重复建立新的死者。
叙述中的总人数是校验线索，不能再作为新死者重复计入。
master仅当原文明确为宗师/传奇宗师或六品及以上才为true。
confirmed_dead只用于明确死亡或元神消亡。断臂、穿胸、落败、重伤、生死不知、动了杀心均不能算已死。
肉身被毁但元神继续战斗也不算死亡。若同一人物稍后确实死亡，则合并为一条最终状态并保留证据。
本段实际死亡用current，回忆中发生的死亡用historical，无法确认用uncertain。
每条evidence必须列出支持身份、境界或结局的原文段落ID；可以引用多段以完整证明。
不要遗漏守护者等非敌方人物的死亡。只输出规定的JSON，禁止推理草稿。"""


def source_passages(arc):
    passages = {}
    for row in arc[1]:
        lines = [line for line in row["content"].splitlines() if line.strip()]
        indices = {
            index + offset
            for index, line in enumerate(lines) if PATTERN.search(line)
            for offset in (-1, 0, 1) if 0 <= index + offset < len(lines)
        }
        for index in sorted(indices):
            ref = f"c{row['chapter_index']}p{index}"
            passages[ref] = {
                "source": f"原稿顺序 {row['chapter_index']} · {row['title']}",
                "text": lines[index],
            }
    return passages


def validate(ledger, passages):
    for kind in ("ranks", "casualties"):
        for row in ledger[kind]:
            if not row["name"].strip() or not row["evidence"] or any(ref not in passages for ref in row["evidence"]):
                raise ValueError("事实台账缺少可核对原文")
    for row in ledger["ranks"]:
        if not 0 <= row["tier"] <= 9 or not 0 <= row["stage"] <= 5:
            raise ValueError("修为品级无效")


def merge_records(rows, kind):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(re.sub(r"\s+", "", row["name"]), row["temporal"])].append(row)
    merged = []
    for records in grouped.values():
        if kind == "ranks":
            selected = max(records, key=lambda row: (row["tier"], row["stage"]))
        else:
            selected = max(records, key=lambda row: row["state"] == "confirmed_dead")
        item = dict(selected)
        item["evidence"] = list(dict.fromkeys(ref for row in records for ref in row["evidence"]))
        merged.append(item)
    return merged


def format_ledger(ledger, passages, question=None):
    current = [row for row in ledger["ranks"] if row["temporal"] == "current" and row["tier"] > 0]
    deaths = [row for row in ledger["casualties"]
              if row["temporal"] == "current" and row["master"] and row["state"] == "confirmed_dead"]
    lines, timeline = [], []

    def add_evidence(row, event):
        source = passages[row["evidence"][0]]["source"]
        index = int(re.search(r"c(\d+)p", row["evidence"][0])[1])
        timeline.append({
            "chapter_index": index, "title": source.split(" · ", 1)[1] + " · " + row["name"],
            "event": event,
            "quotes": [passages[ref]["source"] + "\n" + passages[ref]["text"].strip()
                       for ref in row["evidence"]],
        })

    include_rank = question is None or any(word in question for word in ("等级", "境界", "修为", "修炼", "最高"))
    include_deaths = question is None or any(word in question for word in ("死亡", "死", "殒", "陨", "多少", "几位"))
    if current and include_rank:
        highest = max((row["tier"], row["stage"]) for row in current)
        leaders = [row for row in current if (row["tier"], row["stage"]) == highest]
        lines.append("按本段原文明示的修炼境界，最高者为" +
                     "、".join(f"{row['name']}（{row['level']}）" for row in leaders) +
                     "。境界排序与战斗胜负分别判断；未明确修为者无法据此排定名次。")
        for row in leaders:
            sources = list(dict.fromkeys(passages[ref]["source"] for ref in row["evidence"]))
            lines.append("境界依据：" + "；".join(sources))
            add_evidence(row, row["level"])
    elif include_rank:
        lines.append("现有原文证据不足以确定这段剧情的最高境界者。")
    if not include_deaths:
        return {"response": "\n".join(lines), "timeline": timeline}
    lines.append(f"\n本段可确认 {len(deaths)} 位宗师殒命。口径包含传奇宗师，同一人只计一次，历史回忆另列。")
    for number, row in enumerate(deaths, 1):
        sources = list(dict.fromkeys(passages[ref]["source"] for ref in row["evidence"]))
        lines.append(f"{number}. {row['name']}（{'；'.join(sources)}）")
        add_evidence(row, row["name"] + " · 原文确认死亡")
    excluded = [row for row in ledger["casualties"] if row not in deaths]
    if excluded:
        lines.append("\n其余记录")
        for row in excluded:
            reason = "历史回忆" if row["temporal"] == "historical" else (
                "原文未证实宗师身份" if not row["master"] else
                "重伤" if row["state"] == "injured" else "死亡或发生时段未确认"
            )
            lines.append(f"• {row['name']}：{reason}")
            add_evidence(row, reason)
    return {"response": "\n".join(lines), "timeline": timeline}


def answer_from_source(endpoint, question, arc, cache_directory=None):
    passages = source_passages(arc)
    cache_path = None
    if cache_directory:
        cache_directory = Path(cache_directory)
        cache_directory.mkdir(exist_ok=True)
        key = hashlib.sha256(json.dumps(
            [PROMPT, model_identity(), arc[0], passages], ensure_ascii=False
        ).encode()).hexdigest()
        cache_path = cache_directory / ("ledger-" + key + ".json")
        if cache_path.exists():
            ledger = json.loads(cache_path.read_text(encoding="utf-8"))
            validate(ledger, passages)
            result = format_ledger(ledger, passages, question)
            result["source_ledger_cache_hit"] = True
            return result
    groups, group, size = [], {}, 0
    for ref, passage in passages.items():
        length = len(json.dumps(passage, ensure_ascii=False))
        if group and size + length > 120000:
            groups.append(group)
            group, size = {}, 0
        group[ref] = passage
        size += length
    if group:
        groups.append(group)
    ledger = {"ranks": [], "casualties": []}
    for group in groups:
        result = json.loads(completion(endpoint, [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps({
                "question": "核对本段原文明示的修炼境界，以及宗师和传奇宗师的伤亡台账。",
                "location": arc[0], "original_passages": group,
            }, ensure_ascii=False)},
        ], 7000, response_format=FORMAT))
        validate(result, group)
        for kind in ledger:
            ledger[kind].extend(result[kind])
    for kind in ledger:
        ledger[kind] = merge_records(ledger[kind], kind)
    if cache_path:
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
        temporary.replace(cache_path)
    return format_ledger(ledger, passages, question)
