"""Isolated V2 pilot jobs; V1 runtime and caches remain separate."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
from threading import Lock
from uuid import uuid4
from dataclasses import replace

from .model_client import model_identity
from .reader_harness import Limits, write_json
from .compact_harness import CompactHarness as Harness, VERSION
from .compact_index import index_path
from .reader_workflows import WorkflowStore, digest
from .usage import cached_result
from .spending import Spending

POOL = ThreadPoolExecutor(max_workers=1)
LOCK = Lock()
JOBS = {}
REVISIONS = {}


def directory(database):
    path = Path(database).resolve()
    return path.parent / "compact_harness" / digest(str(path))[:16]


def revision(database):
    path = Path(database).resolve()
    files = [path,Path(str(path)+"-wal"),index_path(database)]
    stamp = tuple((str(file),file.stat().st_mtime_ns,file.stat().st_size) for file in files if file.exists())
    with LOCK:
        previous = REVISIONS.get(str(path))
    if previous and previous[0]==stamp:
        return previous[1]
    value = hashlib.sha256()
    if index_path(database).exists():
        with closing(sqlite3.connect(index_path(database))) as index:
            item=index.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()
            if item: value.update(item[0].encode())
            for record in index.execute('SELECT id,payload FROM records ORDER BY id'):
                value.update(json.dumps(record,ensure_ascii=False).encode())
    with closing(sqlite3.connect(path)) as conn:
        for row in conn.execute("SELECT chapter_index,title,content,summary,key_events FROM chapters ORDER BY chapter_index"):
            value.update(json.dumps(row,ensure_ascii=False).encode())
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='chapter_people'").fetchone():
            for row in conn.execute("SELECT chapter_index,name,aliases,role FROM chapter_people ORDER BY chapter_index,name"):
                value.update(json.dumps(row,ensure_ascii=False).encode())
    result = value.hexdigest()
    with LOCK:
        REVISIONS[str(path)] = stamp,result
    return result


def start(database,endpoint,question,history=None,chapter=None,scope=None,max_cost_usd=None):
    history = [{"role":row["role"],"content":row["content"][:1500]} for row in (history or [])[-4:]]
    book_revision = revision(database)
    key = digest([VERSION,model_identity(),str(Path(database).resolve()),book_revision,question,history,chapter,scope])[:32]
    target = directory(database)
    target.mkdir(parents=True,exist_ok=True)
    path = target/(key+".json")
    if path.exists():
        result = json.loads(path.read_text())
        meta=result.get("harness",{})
        recipe=meta.get("workflow_id") or (meta.get("learned_workflow") or {}).get("id")
        disabled={row["id"] for row in WorkflowStore(target,[VERSION,*model_identity()]).list() if row["status"]=="disabled"}
        if meta.get("outcome") in {"answered","partial","clarification"} and recipe not in disabled:
            return cached_result(result)
    with LOCK:
        previous = JOBS.get((str(target),key))
        if previous and previous["status"]=="running":
            return dict(previous)
        JOBS[(str(target),key)]={"job_id":key,"status":"running","phase":"planning","checked":0,"total":0}
        POOL.submit(work,key,database,endpoint,book_revision,question,history,chapter,scope,max_cost_usd)
        return dict(JOBS[(str(target),key)])


def work(key,database,endpoint,book_revision,question,history,chapter,scope,max_cost_usd=None):
    target = directory(database)
    def progress(**changes):
        with LOCK:
            JOBS[(str(target),key)].update(changes)
    harness = None
    accounting = None
    run_id = uuid4().hex
    run_status = "running"
    def capture_usage():
        if accounting is None or harness is None:
            return
        try:
            # A resumed checkpoint is already historical spend; only this
            # attempt's newly incurred calls are added to the lifetime ledger.
            accounting.update(run_id, database, key, harness.meter.summary(include_carried=False), run_status)
            progress(usage=harness.meter.summary())
        except (OSError, sqlite3.Error):
            logging.exception("Could not persist Reader usage snapshot")
    try:
        accounting = Spending(database)
        accounting.import_history()
        with closing(sqlite3.connect(database,timeout=30)) as conn:
            conn.row_factory=sqlite3.Row
            limits=Limits.configured()
            if max_cost_usd is not None:limits=replace(limits,max_cost_usd=min(limits.max_cost_usd,max_cost_usd))
            harness=Harness(conn,endpoint,target,book_revision,question,history,chapter,scope,limits=limits,
                            on_progress=progress,checkpoint=target/(key+".checkpoint.json"))
            harness.meter.on_change = capture_usage
            capture_usage()
            result=harness.run()
        run_status = "completed"
        capture_usage()
        result.update(job_id=key,status="completed",request={"question":question,"history":history,"chapter":chapter,"scope":scope})
        write_json(target/(key+".json"),result)
        with LOCK:
            JOBS[(str(target),key)]=result
        if result["harness"]["outcome"] in {"answered","clarification","partial"}:
            checkpoint=target/(key+".checkpoint.json")
            if checkpoint.exists():checkpoint.replace(target/(key+".completed.checkpoint.json"))
    except Exception as error:
        run_status = "failed"
        capture_usage()
        logging.exception("Reader harness job failed")
        progress(status="failed",error=f"{type(error).__name__}: {error}",
                 user_message="本次处理未完成，已保存执行进度和证据，可重试继续。",
                 usage=harness.meter.summary() if harness else None)
    finally:
        if harness:
            harness.meter.on_change = None


def status(key,database):
    if not re.fullmatch(r"[0-9a-f]{32}",key):
        return None
    target=directory(database)
    with LOCK:
        result=JOBS.get((str(target),key))
        if result:
            return dict(result)
    path=target/(key+".json")
    return json.loads(path.read_text()) if path.exists() else None


def feedback(key,database,helpful):
    result=status(key,database)
    if not result or result.get("status")!="completed":
        raise ValueError("没有可反馈的已完成回答")
    metadata=result.get("harness",{})
    learned=metadata.get("learned_workflow") or {}
    recipe=metadata.get("workflow_id") or learned.get("id")
    if recipe:
        store=WorkflowStore(directory(database),[VERSION,*model_identity()])
        store.feedback(recipe,helpful)
    write_json(directory(database)/(key+".feedback.json"),{"job_id":key,"helpful":helpful,"workflow_id":recipe})
    if not helpful:
        WorkflowStore(directory(database),[VERSION,*model_identity()]).forget_tools(result.get("_extraction_cache_keys",[]))
        (directory(database)/(key+".json")).unlink(missing_ok=True)
        (directory(database)/(key+".checkpoint.json")).unlink(missing_ok=True)
        with LOCK:
            JOBS.pop((str(directory(database)),key),None)
    return {"saved":True,"workflow_id":recipe,"workflow_status":"active" if helpful and recipe else "disabled" if recipe else None}
