"""Build proposed V2 contracts and an UNEXECUTED evaluation specification.

Design artifacts only. No book reads, network, model calls or runtime migration.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs"


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def string(description=""):
    return {"type": "string", "description": description}


def strings(description=""):
    return {"type": "array", "items": {"type": "string"}, "description": description}


def chapter_orders():
    return {"type": "array", "items": {"type": "integer", "minimum": 1}, "uniqueItems": True}


def enum(*values):
    return {"type": "string", "enum": list(values)}


def record(kind, fields, required=None, common=True):
    base = {"kind": {"const": kind}}
    if common:
        base.update({key: string() for key in ("id", "book_id", "source_revision", "index_revision")})
    return {"type": "object", "additionalProperties": False,
            "properties": {**base, **fields}, "required": [*base, *(required if required is not None else fields)]}


def contracts():
    validation = {"type": "object", "additionalProperties": False,
                  "properties": {"quote_integrity": {"type": "boolean"},
                                 "semantic_status": enum("unreviewed", "source_supported", "contested", "unsupported"),
                                 "review_method": enum("none", "rule", "model", "human")},
                  "required": ["quote_integrity", "semantic_status", "review_method"]}
    definitions = {
        "Entity": record("entity", {
            "entity_type": enum("person", "group", "place", "organization", "object", "other"),
            "canonical_name": string(), "aliases": strings(), "mention_span_ids": strings(),
            "identity_claim_ids": strings(), "same_as_candidates": strings(),
            "identity_status": enum("resolved", "provisional", "ambiguous"),
            "temporary_identity_basis": string("For unnamed people use an event/mention anchor, never a globally shared label such as unnamed master.")}),
        "SourceSpan": record("source_span", {
            "chapter_order": {"type": "integer", "minimum": 1}, "original_title": string(),
            "start_char": {"type": "integer", "minimum": 0}, "end_char": {"type": "integer", "minimum": 1},
            "text": string("Exact substring of immutable decoded chapter text; [start_char,end_char) uses Unicode code points."),
            "text_sha256": string(), "legacy_paragraph_id": string("Bridge to current cNpM or cNpMsK IDs.")}),
        "Claim": record("claim", {
            "text": string(), "subject_ids": strings(), "object_ids": strings(),
            "claim_type": enum("identity", "action", "state", "rank", "world_rule", "knowledge", "utterance", "other"),
            "epistemic": enum("narrated", "character_claim", "recollection", "inference"),
            "asserted_by": string(), "revealed_at": {"type": "integer", "minimum": 1},
            "reveal_span_id": string("Fine narrative position when multiple reveals occur in one chapter."),
            "story_time": string("Known interval or explicitly unknown; do not invent dates."),
            "evidence_span_ids": {"type": "array", "items": string(), "minItems": 1},
            "validation": validation, "supersedes_claim_ids": strings(),
            "status": enum("candidate", "supported", "disputed", "superseded", "rejected")}),
        "Event": record("event", {
            "participants": strings(), "action": string(), "goal": string(), "result": string(),
            "mention_span_ids": strings(), "claim_ids": strings(),
            "narrative_first": {"type": "integer", "minimum": 1}, "narrative_last": {"type": "integer", "minimum": 1},
            "story_time": string(), "status": enum("ongoing", "resolved", "interrupted", "unknown"),
            "identity_status": enum("candidate", "linked", "ambiguous"), "identity_notes": string()}),
        "EventLink": record("event_link", {
            "from_event_id": string(), "to_event_id": string(),
            "relation": enum("continues", "precedes", "explicit_cause", "hypothesized_cause", "enables", "motivates", "blocks", "resolves", "refers_back"),
            "epistemic": enum("source_explicit", "character_claim", "inferred"),
            "evidence_span_ids": {"type": "array", "items": string(), "minItems": 1}, "validation": validation}),
        "NarrativeUnit": record("narrative_unit", {
            "unit_type": enum("scene", "phase", "arc"), "label": string(), "parent_unit_ids": strings(),
            "event_ids": strings(), "phase_role": string("Optional annotation: setup, trigger, turn, climax, aftermath, transition, or unknown; never force a plot into a fixed pattern."),
            "segments": {"type": "array", "items": {"type": "object", "properties": {
                "start": {"type": "integer", "minimum": 1}, "end": {"type": "integer", "minimum": 1}}, "required": ["start", "end"], "additionalProperties": False}},
            "entry_claim_ids": strings(), "exit_claim_ids": strings(),
            "boundary_status": enum("proposed", "supported", "ambiguous"),
            "open_thread_ids": strings(), "excluded_mentions": strings("Later recollections are mentions, not automatic arc members.")}),
        "EntityState": record("entity_state", {
            "entity_id": string(), "property": string("Book-defined property, e.g. physical_state, spirit_state, rank, allegiance, knowledge."),
            "old_value": string(), "new_value": string(), "valid_from_event": string(), "valid_to_event": string(),
            "revealed_at": {"type": "integer", "minimum": 1}, "claim_ids": strings(), "uncertainty": string()}),
        "ClueThread": record("clue_thread", {
            "seed_claim_ids": strings(), "development_claim_ids": strings(), "payoff_claim_ids": strings(),
            "relation_basis": string(), "status": enum("candidate_setup", "developing", "resolved", "open", "disputed", "revised"),
            "validation": validation, "spoiler_revealed_at": {"type": ["integer", "null"], "minimum": 1}}),
        "Coverage": record("coverage", {
            "facet": enum("events", "boundaries", "causes", "clues", "entity_states", "world_rules", "source_reading"),
            "requested_chapter_ids": chapter_orders(), "processed_chapter_ids": chapter_orders(), "missing_chapter_ids": chapter_orders(),
            "reviewed_samples": {"type": "integer", "minimum": 0}, "unresolved_ids": strings(),
            "method": enum("full_source_pass", "keyword_scan", "compact_lookup", "human_review"),
            "status": enum("partial", "processed", "validated_sample", "adjudicated_range"),
            "notes": string("Processed coverage is not a proof of perfect semantic recall.")}),
        "Scope": record("scope", {
            "unit_ids": strings(), "event_ids": strings(), "chapter_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}},
            "context_chapter_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}},
            "excluded_event_ids": strings(), "boundary_claim_ids": strings(),
            "status": enum("candidate", "confirmed_for_question", "ambiguous"),
            "origin": enum("user_selected", "conversation", "index_proposed", "source_checked"),
            "reader_position": {"type": ["integer", "null"], "minimum": 1,
                                "description": "Last chapter confirmed finished by the reader, not merely opened. Null means no read-through boundary."}, "uncertainty": string()}),
        "EvidencePackage": record("evidence_package", {
            "scope_id": string(), "question_goal": string(), "claim_ids": strings(),
            "source_spans": {"type": "array", "items": {"$ref": "#/$defs/SourceSpan"}},
            "coverage_ids": strings(), "counterevidence_claim_ids": strings(),
            "gate_results": {"type": "object", "additionalProperties": enum("pass", "fail", "unknown")},
            "unresolved": strings(), "answer_mode": enum("factual", "interpretation", "counterfactual", "mixed"),
            "requires_source_expansion": {"type": "boolean"}}),
        "IndexManifest": record("index_manifest", {
            "schema_version": string(), "extractor_versions": {"type": "object"},
            "total_chapters": {"type": "integer", "minimum": 0}, "processed_chapters": {"type": "integer", "minimum": 0},
            "facet_coverage_ids": strings(), "failed_job_ids": strings(), "open_quality_issues": strings(),
            "publication": enum("staging", "published", "superseded"), "parent_index_revision": string(),
            "artifact_hashes": {"type": "object", "additionalProperties": string()}}),
    }
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:lumina:compact-reader:v2:proposal",
            "title": "LuminaReader compact-first contracts — DESIGN PROPOSAL, not active runtime",
            "description": "Source identity, proof-bearing compact records and coverage are versioned separately from dialogue and model choice.",
            "$defs": definitions, "oneOf": [{"$ref": "#/$defs/" + name} for name in definitions]}


FAMILIES = [
    ("locate", "局部事实与定位", ["这章秦宇采取了什么行动？请给原文出处。", "这句话是谁对谁说的？前后语境是什么？", "找出人物首次实际登场，而不是首次被提及的章节。", "某件物品在本章被如何使用？区分计划和实际行动。"], "事实和引用主体对应；首次出现与首次行动区分。"),
    ("scope", "剧情阶段与范围", ["回顾秦宇初到小石寨村至离开去京城的完整情节。", "中间几章不提小石寨村，但是否仍属于同一事件？", "最后一次出现地名之后，善后与离开在哪些章节完成？", "后文再次回忆小石寨村，是否应该扩展本段剧情范围？"], "按事件延续及起止证据定位；不按地名首末命中机械截断。"),
    ("relations", "多人物关系变化", ["欧阳明和秦宇的关系经历了哪些变化？请按章节梳理。", "比较三名人物在同一冲突中的目标与立场。", "一次合作是否说明两人已经和解？列出支持和反例。", "把同一人物的别名和无名身份对应起来，不确定的分开列。"], "人物归属、变化时点、相互评价和参与身份准确。"),
    ("causes", "因果与行动条件", ["这场冲突是怎样一步步升级的？区分导火索和背景条件。", "两个事件先后发生，原文是否明确说明因果关系？", "这次选择是角色自述的动机，还是叙述确认的原因？", "如果没有前一事件，后一事件是否仍可能发生？区分事实和反事实推演。"], "时间先后不当作因果；显式因果、角色说法和推断分层。"),
    ("clues", "伏笔与回收", ["登仙之梯相关线索怎样铺垫并揭露真相？", "列出某个悬念的出现、发展、回应章节；没回应的保持开放。", "哪些是后来印证的伏笔，哪些只是重复出现的意象？", "一个早期说法后来被推翻，如何保留它当时造成的影响？"], "种子与回收分别有证据；候选伏笔不当成作者意图事实。"),
    ("knowledge", "叙述顺序与人物认知", ["秦宇当时是否已经知道欧阳明的真实计划？", "读者知道的信息与人物知道的信息有哪些差异？", "这段死亡描述是当下发生，还是历史回忆？", "只讨论到指定章节，后面的真相不要剧透。"], "事件发生、叙述揭露、角色知情三个时间位置分开。"),
    ("census", "身份、境界与伤亡", ["神女击败的五位世家宗师都死了吗？", "祠堂事件包含自尽守护者时，共多少位宗师死亡？", "秦宇战胜聂明升，是否说明他的境界最高？", "原稿顺序1738至1841，包含祁婆婆和传奇宗师，同一人去重后有多少位宗师死亡？"], "断臂/生死不知/元神被收与消亡分开；身份及最终状态接齐；名单与计数一致。"),
    ("world", "设定与状态演进", ["某个境界的限制有哪些？哪些来自人物说法？", "同一个人物在不同阶段的境界变化是什么？", "临时战力与实际境界分别有什么证据？", "同一设定前后不一致，是新揭露、说法冲突还是无法判断？"], "书内规则及有效时间明确；不引入其他小说设定。"),
    ("discussion", "书友讨论与解释", ["你如何理解秦宇与欧阳明的关系变化？给出另一种读法。", "这段情节的叙事节奏有什么作用？引用具体段落。", "你是否赞同这个人物的选择？区分评价和书内事实。", "假设人物作出另一种选择，可能发生什么？清楚标注推演。"], "事实前提有出处；解释允许多解；反事实不写回事实索引。"),
    ("calculation", "确定性计算", ["在一个明确范围内，按阵营统计已确认死亡者，并列身份依据。", "根据明确给出的时间计算耗时，缺失时间不补造。", "计算有明确记录的阶段平均耗时，并说明分母和排除项。", "把明确境界按书内层级排序，不把未知阶段强行编码。"], "类型、单位、分母、去重键和未知值策略可复算。"),
    ("coverage", "资料不足与能力边界", ["现有索引没有这段情节，能否只读相关原文补齐？", "比较两本已加载的书，逐本标明来源。", "查作者访谈来证实创作意图；若未启用外部检索应明确说明。", "载入含图片的PDF，询问插图信息；未支持时不可假装读到。"], "缺口触发正确技能或说明未支持；不得假装已读、已检索。"),
    ("operations", "上下文、缓存与预算", ["相同问题再次提问，复用合格答案和证据，不重复花费。", "切换到另一个讨论后问其中有几人，不能沿用错误范围。", "修改某一章后，只失效依赖它的证据与答案。", "原文阅读即将超过预算时，给出已知结论、缺口和继续阅读的费用上限。"], "缓存、讨论隔离、增量失效和预算停止可检查。"),
]


def evaluation():
    rows = []
    for family, label, questions, rubric in FAMILIES:
        for index, question in enumerate(questions, 1):
            existing = family == "census" or (family == "relations" and index == 1)
            rows.append({"id": f"{family}-{index:02}", "family": family, "family_label": label,
                         "question": question, "criteria": rubric, "execution_status": "NOT_RUN",
                         "gold_status": "existing_candidate_requires_original_adjudication" if existing else "not_labeled",
                         "split": "regression_development" if existing else "candidate_holdout_after_labeling",
                         "reference_policy": "Reuse prior GPT/Qwen outputs as candidate references only; do not regenerate GPT automatically.",
                         "required_annotations": ["source_revision", "scope", "required_claims", "acceptable_interpretations", "source_spans", "coverage_requirements"]})
    return {"status": "DESIGN_ONLY_NOT_EXECUTED", "cases": rows,
            "metrics": ["claim_support_precision", "scope_precision_and_recall", "final_state_accuracy", "event_identity_accuracy",
                        "causal_edge_correctness", "foreshadowing_link_correctness", "spoiler_violations", "unresolved_disclosure",
                        "compact_focused_deep_distribution", "cost_and_tokens_per_answer", "cache_reuse_savings"],
            "initial_acceptance_targets_not_measurements": {"adjudicated_claim_support_precision": 0.98,
                "scope_recall": 0.95, "scope_precision": 0.85, "critical_factual_errors_on_regressions": 0,
                "compact_or_focused_share_on_labeled_question_mix": 0.80,
                "unapproved_budget_expansions": 0},
            "measurement_policy": "Report by family, not only overall. Initially label cases against source. Mocked tool tests validate mechanics, not model accuracy. Track budgets and partial answers; do not optimize cost by hiding failures."}


def policy():
    skills = [
        ("locate_scope", "定位剧情与章节", ["narrative_units", "events"], "核对进入、延续、结束与转场；返回多段范围与不确定边界"),
        ("trace_relations", "关系与状态演进", ["events", "entity_states"], "按有效时间展示变化；两人或多人只是查询参数"),
        ("trace_causes", "因果链", ["event_links", "claims"], "显式因果、角色说法、推断分层；先后不等于因果"),
        ("trace_clues", "伏笔与悬念", ["clue_threads", "claims"], "种子、发展、回收、未回收和修正分别输出"),
        ("track_knowledge", "人物认知与防剧透", ["entity_states", "claims"], "按揭露位置与读者进度过滤，不偷用未来信息"),
        ("census", "全量身份/状态统计", ["events", "entity_states", "coverage"], "名单、身份、最终状态和去重键均可追溯，覆盖不足不报严格总数"),
        ("world_rules", "设定核对", ["claims", "entity_states"], "维护书内规则适用阶段与反例，不套用外书设定"),
        ("discuss", "书友讨论", ["claims", "events"], "事实前提有出处，解读与评价明确标注，允许合理不同读法"),
        ("calculate", "数值和时间计算", ["typed_records"], "使用白名单算子；解释单位、分母、未知值，不执行生成代码"),
        ("deep_read", "证据缺口深读", ["source_spans", "coverage"], "先验证缺口，再按事件依赖扩展，受已授权费用与来源范围约束"),
        ("cross_book", "跨书比较", ["per_book_evidence_packages"], "显式多文档选择、逐本出处和独立预算；后续阶段启用"),
        ("external_sources", "外部资料核验", ["external_provenance"], "仅启用相应连接器后使用；书内推断不能冒充作者访谈事实"),
    ]
    return {"status": "PROPOSED_NOT_ACTIVE", "baseline_tag": "v1.0.0-reader-baseline", "baseline_commit": "4c3396de3dc3216c00ac17d4f57f41d8c8a550c7",
            "controller_loop": {
                "status": "PROPOSED_EXTENSION_OF_BASELINE_LOOP",
                "actions": ["execute", "answer", "clarify"],
                "calls_per_execute": {"minimum": 1, "maximum": 4, "execution": "sequential; prior result references allowed"},
                "persisted_state_groups": ["goal_and_discussion_dependencies", "scope_and_revisions", "evidence_and_facet_coverage",
                    "evidence_requirements_and_gaps", "pending_calls_and_received_decision", "actual_usage_and_reservations"],
                "transitions": [
                    {"when": "valid answer cache", "next": "return cached answer with zero new model usage"},
                    {"when": "pending calls exist", "next": "validate, execute cached tool, merge observations, checkpoint; drain before controller"},
                    {"when": "pending queue empty", "next": "assemble bounded observation; reuse pending_final or invoke controller"},
                    {"when": "invalid action or tool arguments", "next": "append error observation; invalidate pending calls; replan"},
                    {"when": "final answer has blocking evidence gaps", "next": "append specific gaps; retrieve compact or source as needed"},
                    {"when": "final requirements met", "next": "answered, partial with explicit limits, or clarification"},
                    {"when": "authorized limit exhausted or repeated no progress", "next": "persist checkpoint; render confirmed records and gaps without a new paid summary"}],
                "semantic_check_limit": "Exact quotes, hashes and coverage are deterministic checks; entailment requires source-grounded extraction or targeted semantic review and remains subject to evaluation.",
                "deep_jobs_share_parent_authorization": True,
                "unknown_request_completion": "Keep reservation, operation ID and unknown usage; do not promise exactly-once execution across APIs.",
                "baseline_default_limits_not_v2_measurements": {"max_calls": 8, "max_decisions": 8, "max_steps": 24,
                    "max_source_chars": 180000, "max_input_chars": 60000, "max_output_tokens": 5000,
                    "max_cost_usd": 0.50, "repeated_seen_results_stop": 3}},
            "mode_policy": [
                {"mode": "compact", "entry": "已发布索引的范围、身份、时间和证据包足够", "actions": ["search_knowledge", "get_context", "get_evidence", "answer"], "cost_rule": "Prefer local lookups; answer in the same planner turn when evidence is ready."},
                {"mode": "focused_source", "entry": "已定位事件/阶段，但关键事实缺引用、冲突或边界待核", "actions": ["read", "extract_if_needed", "compute_if_needed", "answer"], "cost_rule": "Read the smallest supporting spans plus relevant context, cache the evidence."},
                {"mode": "deep", "entry": "缺失事件链、完整统计覆盖不足、多轮证据冲突或原文定位连续失败", "actions": ["durable_read_job", "read_scan_extract", "propose_index_patch", "answer"], "cost_rule": "Never increase the authorized total budget automatically. Save progress on exhaustion."}],
            "evidence_gates": ["scope_supported", "identity_resolved", "temporal_and_reveal_positions_valid", "claim_source_support",
                               "required_range_coverage", "counterevidence_checked", "budget_available"],
            "cross_record_checks": ["source span end_char > start_char and exact substring/hash match",
                "references stay inside the authorized book/source/index revisions",
                "unit parentage is acyclic; event and character identity conflicts remain explicit",
                "causal cycles are flagged for source review, not rejected just because a narrative includes time travel or a loop",
                "counterfactual answers and reader opinions are not published as book facts"],
            "base_tools_preserved": ["catalog", "search", "read", "scan", "extract", "aggregate", "workflow"],
            "proposed_additions": ["search_knowledge", "get_context", "get_evidence", "typed_compute"],
            "skills": [{"id": id, "purpose": name, "requires": required, "evidence_rule": rule,
                        "selection": "planner chooses composable skill; not a hardcoded question router",
                        "availability": "later_opt_in" if id in {"cross_book","external_sources"} else "planned_core",
                        "completion_checks": ["required evidence conditions met or gaps disclosed", "authorized scope and budget respected"]}
                       for id,name,required,rule in skills],
            "skill_contract": {"input": ["question_goal","scope","reader_position","evidence_requirements","budget"],
                               "output": ["evidence_package","coverage","unresolved","actual_usage"],
                               "allowed_writes": "query patches go to staging; discussion opinions never become book facts"},
            "typed_compute_operators": ["filter","deduplicate","count","sum","mean","sort","interval_difference"],
            "preprocessing_resource_defaults": {"blas_threads":4,"dataloader_workers":2,"combined_cpu_thread_budget":16,
                "no_new_cpu_heavy_job_above_load1":32,"light_inspection_only_above_load1":64,
                "check_gpu_and_existing_jobs_before_launch":True},
            "cost_policy": {"reuse_existing_analyses_as_candidates": True,
                "never_promote_old_summaries_to_source_truth": True, "do_not_rerun_gpt_for_reference_answers": True,
                "preprocess_and_qa_separate_budgets": True, "count_retries_and_unknown_usage": True,
                "embedding_usage_in_preprocess_budget": True,
                "missing_historical_metrics_stay_unknown_without_auto_paid_rerun": True,
                "cache_writes_reads_outputs_reported_separately": True,
                "full_preprocessing_requires_token_estimate_and_explicit_total_budget": True}}


if __name__ == "__main__":
    write("compact-reader-v2-contracts.json", contracts())
    write("compact-reader-v2-evaluation.json", evaluation())
    write("compact-reader-v2-policy.json", policy())
    print("Wrote proposed contracts, policy and 48 UNEXECUTED evaluation cases; no API calls.")
