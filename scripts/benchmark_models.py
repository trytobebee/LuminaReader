"""Freeze book evidence and compare providers without changing Reader settings."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import HTTPException
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import statistics
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core.pipelines.novel import fact_audit
from src.core.pipelines.novel.batch import CompletionLengthError
from src.core.pipelines.novel.model_client import MODEL, REASONING, openai_completion
from src.core.pipelines.novel.usage import CURRENT, UsageMeter

QWEN_MODEL = "qwen3.8-27b-bf16"
TOTAL_OUTPUT_CAP = 12000
ANSWER_FORMAT = {"type": "json_schema", "json_schema": {
    "name": "grounded_book_answer", "strict": True, "schema": {
        "type": "object", "properties": {
            "answer": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "unresolved": {"type": "array", "items": {"type": "string"}},
        }, "required": ["answer", "evidence_ids", "unresolved"], "additionalProperties": False,
    },
}}
SYSTEM = """你是小说原文核对员。只依据所给原文段落回答，不借用其他小说设定。
先给结论，再给关键依据，输出整理好的答案，不输出思考草稿或反复试算。回答控制在900字以内。
明确区分杀心、重伤、生死不知、肉身毁灭但元神仍存活，以及确认死亡。
统计时同一人只计一次，历史回忆与本段当前事件分别处理，人数应与名单相符。
境界、临时战力和胜负分别判断，不能用获胜推出境界更高，尝试突破也不算突破成功。
关系变化应按原稿顺序，区分当时认知与后来揭露的真相。
每个关键结论在answer中标出原稿章名，evidence_ids给出直接支持结论的段落ID。
材料未明确的地方在unresolved中列出，不补造事实。"""


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def full_passages(conn, indices, name_filter=None):
    passages = {}
    for row in conn.execute("SELECT chapter_index,title,content FROM chapters ORDER BY chapter_index"):
        if row["chapter_index"] not in indices:
            continue
        lines = [line for line in row["content"].splitlines() if line.strip()]
        selected = range(len(lines))
        if name_filter:
            selected = sorted({
                index + offset for index, line in enumerate(lines) if name_filter in line
                for offset in (-1, 0, 1) if 0 <= index + offset < len(lines)
            })
        for index in selected:
            passages[f"c{row['chapter_index']}p{index}"] = {
                "chapter_index": row["chapter_index"], "title": row["title"], "text": lines[index],
            }
    return passages


def freeze(directory, database):
    if (directory / "cases.json").exists():
        raise FileExistsError("Frozen inputs already exist; choose a new --output directory")
    directory.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    definitions = [
        ("injury", "神女击败的五位世家宗师都死了吗？请分别核对三名断臂者、被指力击穿胸口者、郁丁明的状态，并核对这四名同伴是否都属于郁家。",
         [1787], [
             "三名断臂者仍能退走和交谈，不能算本章死亡",
             "胸口被击穿者与郁丁明均生死不知，不能确认死亡",
             "另外四人是世家同行宗师，不能全部归为郁家",
             "引用本章原文，结论一致，无断臂即死或其他小说常识",
         ]),
        ("temple", "小石寨祠堂这场事件中，按包含自尽守护者的口径有多少位宗师死亡？请列人名或身份，并说明明前寺老和尚的结局。",
         [1775, 1776, 1777, 1778], [
             "钟声杀死李家老家主、不知来历宗师、金箭观老道长三人",
             "祁婆婆本身是宗师，燃烧生命自尽，合计四名",
             "明前寺老和尚被秦宇救下，幸存",
             "不把十三位冲门者等无明确宗师身份者计入，有可核对出处",
         ]),
        ("rank", "能否因为秦宇最终战胜聂明升，就认定他是这段情节境界最高者？请结合风尘子、青铜人、聂明升及秦宇的原文描述，区分实际境界、突破尝试和胜负；未明确的强者请保留不确定性。",
         [1752, 1753, 1821, 1822, 1823, 1831, 1835], [
             "胜负不能推出秦宇境界最高，原文明说其与聂明升有境界差距",
             "六位青铜人含一名七品后期；聂明升差不多七品后期",
             "风尘子历史上冲击八品失败，不能算已成八品",
             "明确区分已知境界和未明确者，不编造全体强者的绝对排名，引用正确",
         ]),
        ("relationship", "依据这些固定召回的原文，按顺序总结欧阳明和秦宇关系的变化。请重点说明早期宿命敌对、相互评价、求助合作以及登仙之梯的真相。给出原稿章名，说明资料覆盖边界。",
         None, [
             "早期师门宿敌认知与后来师傅共同布局的真相区分清楚",
             "涵盖竞争敌对、尊重对手、传信求助、合作或共同使命的变化",
             "顺序为秦宇击杀欧阳明、玉佩揭真相、携尸完成后续计划，不倒置知情时间",
             "按原稿顺序且有有效引用，最后回顾不等于复活，不声称材料外结局",
         ]),
    ]
    cases = []
    for name, question, indices, rubric in definitions:
        if indices is None:
            indices = {row[0] for row in conn.execute("SELECT chapter_index FROM chapters WHERE instr(content,'欧阳明')>0")}
            passages = full_passages(conn, indices, "欧阳明")
        else:
            passages = full_passages(conn, set(indices))
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps({"question": question, "original_passages": passages}, ensure_ascii=False)},
        ]
        case = {"id": name, "question": question, "passages": passages, "messages": messages,
                "format": ANSWER_FORMAT, "rubric": rubric}
        case["input_sha256"] = digest([messages, ANSWER_FORMAT])
        cases.append(case)
    arc_rows = conn.execute(
        "SELECT chapter_index,title,content,summary,key_events FROM chapters WHERE chapter_index BETWEEN 1738 AND 1841 ORDER BY chapter_index"
    ).fetchall()
    passages = fact_audit.source_passages(("小石寨村", arc_rows))
    messages = [
        {"role": "system", "content": fact_audit.PROMPT},
        {"role": "user", "content": json.dumps({
            "question": "核对本段原文明示的修炼境界，以及宗师和传奇宗师的伤亡台账。",
            "location": "小石寨村", "original_passages": passages,
        }, ensure_ascii=False)},
    ]
    case = {"id": "full_census", "question": "秦宇初到小石寨村至离开去京城，多少位宗师或传奇宗师实际死亡？",
            "messages": messages, "passages": passages, "format": fact_audit.FORMAT, "rubric": [
                "本段实际死亡、包含传奇、去重后的总数为9",
                "名单包括祁婆婆、聂明升、杨同峰，且杨同峰为传奇宗师",
                "三个世家死者分别对应朱雀、玄武、吊坠，不把断臂本身或回顾重复计入",
                "风尘子等历史死亡不计本段，伤者不算死者，引用有效且名单与数量一致",
            ]}
    case["input_sha256"] = digest([messages, fact_audit.FORMAT])
    cases.append(case)
    conn.close()
    write_json(directory / "cases.json", cases)
    references = {}
    for key in ("509491ee962ca94cbe1d14cd645cb72d", "6ecc1d98fda720a8f6f2728404d3f705", "b88fa8632ee0a500ce90971218566d54"):
        path = database.parent / "reader_qa" / (key + ".json")
        if path.exists():
            data = json.loads(path.read_text())
            references[key] = {"model": data.get("model"), "response": data.get("response"),
                               "timeline": data.get("timeline", [])}
    write_json(directory / "prior_gpt_references.json", references)
    write_json(directory / "manifest.json", {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "database": str(database), "cases_sha256": digest(cases),
        "models": ["gpt-6.1-sol / medium", QWEN_MODEL + " / thinking=false", QWEN_MODEL + " / thinking=true"],
        "qwen_thinking_cases": ["injury", "temple", "full_census"],
        "max_total_output_tokens": TOTAL_OUTPUT_CAP,
        "visible_gpt_request_tokens": TOTAL_OUTPUT_CAP - 6000,
        "sampling": "Qwen temperature=0; GPT medium reasoning has no temperature parameter",
        "harness": "fixed retrieved originals -> one structured generation -> identical validation and rendering",
        "reference_policy": "previous GPT answers are reference material; scoring is checked against original paragraphs",
        "latency_policy": "one measured response per case/config; includes transport retries, shared-service queue and prefill",
        "price_source": "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
    })
    for case in cases:
        print(case["id"],len(case["passages"]),len(json.dumps(case["messages"],ensure_ascii=False)),case["input_sha256"][:12],flush=True)


def qwen_metrics(base):
    try:
        with urlopen(base.rstrip("/") + "/metrics", timeout=5) as response:
            text = response.read().decode()
        result = {}
        for name in ("running", "waiting"):
            match = re.search(r"^vllm:num_requests_" + name + r".*?\s([\d.]+)$", text, re.M)
            result[name] = float(match[1]) if match else None
        return result
    except (OSError, ValueError):
        return {}


def qwen_call(base, case, thinking):
    payload = {
        "model": QWEN_MODEL, "messages": case["messages"], "temperature": 0,
        "max_tokens": TOTAL_OUTPUT_CAP, "response_format": case["format"],
        "chat_template_kwargs": {"enable_thinking": thinking},
        "stream": True, "stream_options": {"include_usage": True},
    }
    if case["format"] is None:
        del payload["response_format"]
    text, reasoning_chars, usage, finish = [], 0, None, None
    attempts = []
    for attempt in range(3):
        start = time.perf_counter()
        try:
            request = Request(base.rstrip("/") + "/v1/chat/completions",
                              data=json.dumps(payload, ensure_ascii=False).encode(),
                              headers={"Content-Type": "application/json"})
            parts, reasoning_chars = [], 0
            with urlopen(request, timeout=300) as response:
                for line in response:
                    if not line.startswith(b"data:"):
                        continue
                    value = line[5:].strip()
                    if value == b"[DONE]":
                        break
                    item = json.loads(value)
                    if item.get("usage"):
                        usage = item["usage"]
                    for choice in item.get("choices", []):
                        delta = choice.get("delta", {})
                        parts.append(delta.get("content") or "")
                        reasoning_chars += len(delta.get("reasoning_content") or "")
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
            text = "".join(parts)
            if finish is None:
                raise ConnectionError("stream ended without finish_reason")
            attempts.append({"elapsed_s": round(time.perf_counter() - start, 3), "finish_reason": finish, "usage": usage})
            return text, {"attempts": attempts, "raw_usage": usage, "finish_reason": finish,
                          "reasoning_characters": reasoning_chars}
        except (OSError, HTTPException, ValueError) as error:
            attempts.append({"elapsed_s": round(time.perf_counter() - start, 3), "error": type(error).__name__})
            if isinstance(error, HTTPError):
                code = error.code
                error.close()
                if code not in {429, 500, 502, 503, 504}:
                    return "", {"attempts": attempts, "finish_reason": "http_error", "http_status": code}
            if attempt == 2:
                return "", {"attempts": attempts, "finish_reason": "transport_error"}
            time.sleep(2 ** (attempt + 1))


def validate_answer(case, text):
    # Shared parser for free-generation controls; never repairs factual content.
    fenced = re.fullmatch(r"\s*```(?:json)?\s*([\s\S]*?)\s*```\s*", text)
    if fenced:
        text = fenced[1]
    value = json.loads(text)
    if case["id"] == "full_census":
        fact_audit.validate(value, case["passages"])
        ledger = {kind: fact_audit.merge_records(value[kind],kind) for kind in ("ranks","casualties")}
        rendered = fact_audit.format_ledger(ledger,case["passages"],case["question"])
        dead = [row for row in ledger["casualties"] if row["master"] and row["state"]=="confirmed_dead" and row["temporal"]=="current"]
        return value, rendered["response"], {"death_count":len(dead),"dead_names":[row["name"] for row in dead]}
    if not value["answer"].strip() or not value["evidence_ids"]:
        raise ValueError("empty answer or citations")
    if any(ref not in case["passages"] for ref in value["evidence_ids"]):
        raise ValueError("unknown citation")
    return value, value["answer"], {"citation_count":len(value["evidence_ids"])}


def run_one(directory, case, arm, qwen_base):
    path = directory / f"{case['id']}__{arm}.json"
    if path.exists():
        return
    if arm == "qwen_thinking":
        manifest = json.loads((directory / "manifest.json").read_text())
        subset = manifest.get("qwen_thinking_cases")
        if subset is not None and case["id"] not in subset:
            write_json(path, {"case": case["id"], "arm": arm, "input_sha256": case["input_sha256"],
                              "status": "not_run", "reason": "Outside auxiliary thinking subset", "elapsed_s": 0, "usage": {}})
            return
    start = time.perf_counter()
    result = {"case":case["id"],"arm":arm,"input_sha256":case["input_sha256"],"started_at":datetime.now(timezone.utc).isoformat()}
    before = qwen_metrics(qwen_base) if arm.startswith("qwen") else None
    try:
        if arm == "gpt":
            if (MODEL, REASONING) != ("gpt-6.1-sol", "medium"):
                raise ValueError("Benchmark requires gpt-6.1-sol with medium reasoning")
            meter = UsageMeter()
            token = CURRENT.set(meter)
            try:
                text = openai_completion(case["messages"], TOTAL_OUTPUT_CAP-6000, case["format"], CompletionLengthError)
            finally:
                CURRENT.reset(token)
                result["usage"] = meter.summary()
            result["finish_reason"] = "stop"
        else:
            text, meta = qwen_call(qwen_base,case,arm=="qwen_thinking")
            result.update(meta)
            raw = meta.get("raw_usage") or {}
            result["usage"] = {
                "api_requests":len(meta["attempts"]),
                "input_tokens":raw.get("prompt_tokens"),"output_tokens":raw.get("completion_tokens"),
                "total_tokens":raw.get("total_tokens"),"estimated_cost_usd":None,
                "reasoning_tokens":(raw.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
                "usage_complete":bool(raw) and all("usage" in attempt for attempt in meta["attempts"]),
            }
            result["service_load_before"] = before
            result["service_load_after"] = qwen_metrics(qwen_base)
        result["raw_answer"] = text
        if result["finish_reason"] != "stop":
            raise ValueError("generation did not stop normally: " + result["finish_reason"])
        parsed, rendered, checks = validate_answer(case,text)
        result.update(status="completed",parsed=parsed,answer=rendered,checks=checks)
    except Exception as error:
        result.update(status="failed",error=f"{type(error).__name__}: {error}")
    result["elapsed_s"] = round(time.perf_counter()-start,3)
    write_json(path,result)
    print(json.dumps({key:result.get(key) for key in ("case","arm","status","elapsed_s","error")},ensure_ascii=False),flush=True)


def run(directory, qwen_base):
    cases = json.loads((directory/"cases.json").read_text())
    # Independent services run concurrently; the self-hosted Qwen receives one request at a time.
    def gpt_worker():
        for case in cases:
            run_one(directory,case,"gpt",qwen_base)
    def qwen_worker():
        for case in cases:
            for arm in ("qwen_off","qwen_thinking"):
                run_one(directory,case,arm,qwen_base)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(gpt_worker),pool.submit(qwen_worker)]
        for future in futures:
            future.result()
    outputs = []
    for case in cases:
        for arm in ("gpt","qwen_off","qwen_thinking"):
            result=json.loads((directory/f"{case['id']}__{arm}.json").read_text())
            if result["status"] in ("not_run", "cancelled"):
                continue
            answer = result.get("answer", result.get("raw_answer", ""))
            if result["status"] == "failed":
                answer = result.get("error", "") + "\n原始输出节选（完整见结果JSON）：\n" + answer[:2000].rstrip()
            outputs.append(f"===== {case['id']} / {arm} =====\n{case['question']}\n"
                           f"状态：{result['status']}；耗时：{result['elapsed_s']}秒\n"
                           f"用量：{json.dumps(result.get('usage',{}),ensure_ascii=False)}\n\n"
                           f"{answer}\n")
    (directory/"answers.txt").write_text("\n".join(outputs),encoding="utf-8")


def report(directory):
    cases = json.loads((directory / "cases.json").read_text())
    scores = json.loads((directory / "reviewed_scores.json").read_text())
    comparison = {"cases": [], "totals": {}}
    for arm in ("gpt", "qwen_off", "qwen_thinking"):
        results = []
        for case in cases:
            result = json.loads((directory / f"{case['id']}__{arm}.json").read_text())
            assert result["input_sha256"] == case["input_sha256"]
            if result["status"] in ("not_run", "cancelled"):
                continue
            if arm.startswith("qwen"):
                result["usage"]["reasoning_tokens"] = (
                    (result.get("raw_usage") or {}).get("completion_tokens_details") or {}
                ).get("reasoning_tokens", 0)
            review = scores[case["id"]][arm]
            assert len(review["points"]) == len(case["rubric"])
            assert all(point in (0, 0.5, 1) for point in review["points"])
            row = {"case": case["id"], "arm": arm, "score": sum(review["points"]),
                   "score_max": len(case["rubric"]), "review": review, **{
                       key: result.get(key) for key in ("status", "elapsed_s", "usage", "checks", "reasoning_characters")
                   }}
            results.append(row)
            comparison["cases"].append(row)
        usages = [row["usage"] for row in results]
        comparison["totals"][arm] = {
            "completed": sum(row["status"] == "completed" for row in results),
            "cases": len(results), "score": sum(row["score"] for row in results),
            "score_max": sum(row["score_max"] for row in results),
            "elapsed_sum_s": round(sum(row["elapsed_s"] for row in results), 3),
            "elapsed_mean_s": round(statistics.mean(row["elapsed_s"] for row in results), 3),
            "elapsed_median_s": round(statistics.median(row["elapsed_s"] for row in results), 3),
            **{key: sum(usage.get(key) or 0 for usage in usages)
               for key in ("api_requests", "input_tokens", "output_tokens", "total_tokens", "reasoning_tokens", "cached_input_tokens", "cache_write_input_tokens")},
            "estimated_cost_usd": round(sum(usage["estimated_cost_usd"] for usage in usages), 6)
                if all(usage.get("estimated_cost_usd") is not None for usage in usages) else None,
        }
    write_json(directory / "comparison.json", comparison)
    lines = [
        "LuminaReader 同证据模型对比｜2026-11-19", "",
        "主对比：gpt-6.1-sol / medium 与 qwen3.8-27b-bf16 / thinking=false，均5题。",
        "补充：Qwen thinking=true，局部死亡判定2题；全段结构化补测在同类空白退化发现后中断，实际用量未知。",
        "每题固定相同原文、检索结果、角色消息和JSON schema；每次生成共用12000输出Token上限。",
        "旧GPT答案仅作参考，没有发给任一模型。这里测固定证据后的生成，不含模型自主追加检索。",
        "20个事先冻结的事实检查点，人工对照原文评分：正确1、部分正确0.5、错误或缺失0。",
        "得分是这组诊断题的检查点完成度，不是随机题集上的通用准确率。", "",
        "汇总（input/output为各服务实际计量，output包含推理Token）",
    ]
    for arm, total in comparison["totals"].items():
        cost = total["estimated_cost_usd"]
        cost_text = f"${cost:.6f}" if cost is not None else "自托管，未折算硬件费用"
        lines.append(f"{arm}: {total['completed']}/{total['cases']}完成；"
                     f"{total['score']}/{total['score_max']}分；平均{total['elapsed_mean_s']}秒；"
                     f"输入{total['input_tokens']}，输出{total['output_tokens']}，"
                     f"推理{total['reasoning_tokens']}，总Token {total['total_tokens']}；费用{cost_text}")
    lines += ["", "逐题结果与原文复核"]
    for row in comparison["cases"]:
        lines.append(f"{row['case']} / {row['arm']}: {row['score']}/{row['score_max']}分，"
                     f"{row['elapsed_s']}秒，{row['status']}；{row['review']['reason']}")
    lines += ["", "计量与边界", "每题每配置实测一次，没有重复运行取平均；表中平均值是不同问题的平均值。",
              "耗时包含排队、读取长输入、生成与传输；Qwen共享服务起始已有其他请求，测试中在途请求增加，不能当作独占GPU算力测速。",
              "两边tokenizer和推理实现不同，固定的是文本、输出上限和后处理，并非内部Token序列。",
              "GPT费用按API实际usage和官方单价估算，包含缓存写入；以账户账单为准。",
              "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
              "Qwen没有按Token支付的外部API账单；缺少GPU成本和共享利用率，未将自托管总成本写成0。",
              "完整答案：answers.txt；逐次usage和原始输出：各题__各配置.json；固定输入：cases.json。"]
    (directory / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if (directory / "unconstrained/reviewed_scores.json").exists():
        paired_report(directory)
    print(json.dumps(comparison["totals"], ensure_ascii=False, indent=2))


def paired_report(directory):
    cases = json.loads((directory / "cases.json").read_text())
    paired = {"policy": "First four paired structured generations; fifth paired free-JSON control. Both providers use identical evidence, prompt and parsing within each case.", "cases": [], "totals": {}}
    answers = []
    for case in cases:
        source = directory / "unconstrained" if case["id"] == "full_census" else directory
        frozen = next(row for row in json.loads((source / "cases.json").read_text()) if row["id"] == case["id"])
        reviews = json.loads((source / "reviewed_scores.json").read_text())[case["id"]]
        row = {"case": case["id"], "protocol": "free JSON" if source != directory else "structured JSON"}
        for arm in ("gpt", "qwen_off"):
            result = json.loads((source / f"{case['id']}__{arm}.json").read_text())
            assert result["input_sha256"] == frozen["input_sha256"] and result["status"] == "completed"
            row[arm] = {"score": sum(reviews[arm]["points"]), "review": reviews[arm],
                        "elapsed_s": result["elapsed_s"], "usage": result["usage"],
                        "source": str(source / f"{case['id']}__{arm}.json")}
            answers.append(f"===== {case['id']} / {arm} / {row['protocol']} =====\n{case['question']}\n"
                           f"耗时{result['elapsed_s']}秒；用量{json.dumps(result['usage'],ensure_ascii=False)}\n\n{result['answer']}\n")
        paired["cases"].append(row)
    for arm in ("gpt", "qwen_off"):
        rows = [case[arm] for case in paired["cases"]]
        costs = [row["usage"].get("estimated_cost_usd") for row in rows]
        paired["totals"][arm] = {
            "score": sum(row["score"] for row in rows), "score_max": 20,
            "elapsed_mean_s": round(statistics.mean(row["elapsed_s"] for row in rows),3),
            **{key: sum(row["usage"].get(key) or 0 for row in rows) for key in ("input_tokens","output_tokens","total_tokens")},
            "estimated_cost_usd": round(sum(costs),6) if all(cost is not None for cost in costs) else None,
        }
    measured = [json.loads(path.read_text()) for path in directory.rglob("*__*.json")]
    paid = [row for row in measured if row.get("arm") == "gpt"]
    paired["all_experiments"] = {
        "gpt_estimated_cost_usd": round(sum(row.get("usage",{}).get("estimated_cost_usd") or 0 for row in paid),6),
        "known_token_totals": {arm: sum(row.get("usage",{}).get("total_tokens") or 0 for row in measured if row.get("arm")==arm)
                               for arm in ("gpt","qwen_off","qwen_thinking")},
        "unmetered_cancelled_requests": sum(row.get("status")=="cancelled" for row in measured),
    }
    write_json(directory / "paired_comparison.json", paired)
    (directory / "answers_compared.txt").write_text("\n".join(answers),encoding="utf-8")
    lines = ["最终成对答案对比", "",
             "前4题使用原结构化协议；第5题原协议Qwen空白退化，双方共同改为自由JSON补测。",
             "补测把相同schema写入双方同一提示词，取消双方接口的解码约束；统一去除可选代码围栏后校验。",
             "下表统计可用成对的5题，原协议的失败与全部额外用量另列，未删除原始失败。", ""]
    for arm,total in paired["totals"].items():
        cost = total["estimated_cost_usd"]
        cost_text = f"${cost:.6f}" if cost is not None else "自托管，未折算硬件费用"
        lines.append(f"{arm}: {total['score']}/20分；平均{total['elapsed_mean_s']}秒；输入{total['input_tokens']}，"
                     f"输出{total['output_tokens']}，合计{total['total_tokens']} Token；预估费用{cost_text}。")
    lines += ["", "逐题（GPT分数/耗时；Qwen分数/耗时）"]
    for row in paired["cases"]:
        lines.append(f"{row['case']} [{row['protocol']}]: {row['gpt']['score']}/4, {row['gpt']['elapsed_s']}秒；"
                     f"{row['qwen_off']['score']}/4, {row['qwen_off']['elapsed_s']}秒。{row['qwen_off']['review']['reason']}")
    lines += ["", "结论：在这组固定原文诊断题中，Qwen能答对局部死亡判定和全段9人总数；"
              "GPT在境界比较的证据边界、关系变化完整性和主体辨识上更好；本次还观察到结构化输出失败。",
              "结构化测试中Qwen输出空白耗尽预算，自由JSON后成功。[猜测]输出约束路径可能参与了退化，具体底层原因尚未定位。",
              "已知事实检查点得分不代表全部陈述都逐字无误：例如Qwen补测台账未列六位青铜人的境界，不能直接用于最高境界排名。",
              f"全部实测和补测GPT预估总费用：${paired['all_experiments']['gpt_estimated_cost_usd']:.6f}；"
              "另有1次Qwen辅助思考请求被中断，未收到usage，不纳入已知Token合计。",
              "同一问题每个协议各测一次；共享Qwen负载影响耗时，不据此作通用模型能力或独占算力的量化结论。", "",
              "完整成对答案：answers_compared.txt；指标：paired_comparison.json。", "", "以下保留原协议和辅助测试记录", ""]
    report_path = directory / "report.txt"
    report_path.write_text("\n".join(lines) + report_path.read_text(),encoding="utf-8")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["freeze","run","report"])
    parser.add_argument("--output",type=Path,default=ROOT/"data/benchmarks/2026-11-19")
    parser.add_argument("--database",type=Path,default=ROOT/"data/reader_novel.db")
    parser.add_argument("--qwen-base",default="http://127.0.0.1:18271")
    args=parser.parse_args()
    if args.action=="freeze":
        freeze(args.output,args.database)
    elif args.action == "run":
        run(args.output,args.qwen_base)
    else:
        report(args.output)


if __name__=="__main__":
    main()
