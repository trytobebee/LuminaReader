import os
import sys

# 允许直接从根目录启动，自动识别 src 包
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from flask import Flask, request, jsonify
from src.api import documents
from flask_cors import CORS
import sqlite3
import json
from contextlib import closing
from src.core.pipelines.novel import search, chat_runtime
from src.core.pipelines.novel.reader_workflows import VERSION, WorkflowStore
from src.core.pipelines.novel.model_client import MODEL, PROVIDER
from src.core.pipelines.novel.spending import Spending

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024
CORS(app)

DB_PATH = os.getenv("LUMINA_DB_PATH", os.path.join(app.root_path, "../../data/knowledge_base.db"))
MODEL_BASE = os.getenv("LUMINA_MODEL_BASE")

def active_database():
    payload = request.get_json(silent=True) or {}
    document_id = request.args.get("document_id") or payload.get("document_id", "default")
    try:
        return documents.resolve_database(DB_PATH, document_id)
    except ValueError:
        from flask import abort
        abort(404, description="文档不存在")


def get_db_connection():
    conn = sqlite3.connect(active_database())
    conn.row_factory = sqlite3.Row
    return conn

@app.get("/api/documents")
def document_library():
    return jsonify(documents.list_documents(DB_PATH))


@app.post("/api/documents")
def load_document():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "请选择文档"}), 400
    try:
        return jsonify(documents.import_document(DB_PATH, file.filename, file.read())), 201
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@app.errorhandler(413)
def document_too_large(error):
    return jsonify({"error": "文件超过 64 MB，请拆分后载入"}), 413


@app.get("/api/model")
def active_model():
    return jsonify({"model": MODEL, "provider": PROVIDER, "harness": VERSION})


@app.get("/api/usage")
def cumulative_usage():
    response = jsonify(Spending(DB_PATH).snapshot())
    response.headers["Cache-Control"] = "no-store"
    return response


# 1. 获取章节列表
@app.route('/api/chapters', methods=['GET'])
def get_chapters():
    conn = get_db_connection()
    chapters = conn.execute('SELECT chapter_index,title,volume,summary,key_events,status FROM chapters ORDER BY chapter_index ASC').fetchall()
    conn.close()
    return jsonify([dict(ix) for ix in chapters])

@app.get("/api/chapters/search")
def search_chapters():
    mode = request.args.get("mode", "people")
    query = request.args.get("q", "").strip()
    if mode not in {"people", "plot"} or len(query) > 100:
        return jsonify({"error": "搜索条件无效。"}), 400
    with closing(get_db_connection()) as conn:
        return jsonify(search.search(conn, query, mode))


# 2. 获取单章原文
@app.route('/api/chapters/<int:index>/content', methods=['GET'])
def get_chapter_content(index):
    conn = get_db_connection()
    chap = conn.execute('SELECT content FROM chapters WHERE chapter_index = ?', (index,)).fetchone()
    conn.close()
    
    if not chap:
        return jsonify({"error": "Chapter not found"}), 404
    
    return jsonify({"content": chap['content']})

# 3. 更新章节总结 (AI 推送或手动编辑)
@app.route('/api/chapters/<int:index>/summary', methods=['POST'])
def update_summary(index):
    data = request.json
    summary = data.get('summary')
    key_events = data.get('key_events')
    
    conn = get_db_connection()
    conn.execute('UPDATE chapters SET summary = ?, key_events = ?, status = "completed" WHERE chapter_index = ?',
                 (summary, key_events, index))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

# 4. 知识点管理: 获取所有实体
@app.route('/api/entities', methods=['GET'])
def get_entities():
    chapter_index = request.args.get('chapter_index')
    conn = get_db_connection()
    
    if chapter_index:
        try:
            target_idx = int(chapter_index)
            # 只获取在本章被发现或更新的实体
            entities = conn.execute('''
                SELECT * FROM entities 
                WHERE first_chap_id = ? OR last_updated_chap_id = ?
            ''', (target_idx, target_idx)).fetchall()
        except ValueError:
            entities = []
    else:
        entities = conn.execute('SELECT * FROM entities').fetchall()
    
    result = [dict(ix) for ix in entities]
    has_chapter_people = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chapter_people'"
    ).fetchone()
    if chapter_index and has_chapter_people:
        result.extend(
            {"name": row["name"], "type": "person", "description": row["role"],
             "aliases": json.loads(row["aliases"]), "extracted": True}
            for row in conn.execute(
                "SELECT name,aliases,role FROM chapter_people WHERE chapter_index=? ORDER BY rowid",
                (chapter_index,),
            )
        )
    conn.close()
    return jsonify(result)

# 5. 知识点管理: 添加/更新实体
@app.route('/api/entities', methods=['POST'])
def upsert_entity():
    data = request.json
    chapter_index = data.get("chapter_index")
    if chapter_index is not None and type(chapter_index) is not int:
        return jsonify({"error": "章节编号无效"}), 400
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO entities (name,type,description,first_chap_id,last_updated_chap_id)
        VALUES (?,?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
        type=excluded.type, description=excluded.description,
        first_chap_id=coalesce(entities.first_chap_id,excluded.first_chap_id),
        last_updated_chap_id=coalesce(excluded.last_updated_chap_id,entities.last_updated_chap_id)
    """, (data.get("name"), data.get("type"), data.get("description"), chapter_index, chapter_index))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

# 6. 知识点管理: 删除实体
@app.route('/api/entities/<string:name>', methods=['DELETE'])
def delete_entity(name):
    conn = get_db_connection()
    conn.execute('DELETE FROM entities WHERE name = ?', (name,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

# 7. Chatbot 接口: 基于知识库的智能回答
@app.route('/api/chat', methods=['POST'])
def chat():
    payload = request.get_json(silent=True) or {}
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"response": "请输入问题。"}), 400
    if not MODEL_BASE and PROVIDER != "openai":
        return jsonify({"response": "尚未配置问答模型。"}), 503
    turns = payload.get("history")
    turns = turns if isinstance(turns, list) else []
    history = [{"role": turn["role"], "content": turn["content"][:1500]}
               for turn in turns[-4:] if isinstance(turn, dict)
               and turn.get("role") in {"user", "assistant"} and isinstance(turn.get("content"), str)]
    chapter = payload.get("chapter_index")
    chapter = chapter if type(chapter) is int else None
    scope = payload.get("scope")
    if scope is not None:
        if not isinstance(scope, dict) or type(scope.get("start")) is not int or type(scope.get("end")) is not int or not 0 < scope["start"] <= scope["end"]:
            return jsonify({"response": "剧情范围无效，请重新选择章节。"}), 400
        scope = {"start": scope["start"], "end": scope["end"], "location": str(scope.get("location", ""))[:80]}
    result = chat_runtime.start(active_database(), MODEL_BASE or "", message.strip()[:2000], history, chapter, scope)
    return jsonify(result), 202 if result["status"] == "running" else 200

@app.get("/api/chat/jobs/<job_id>")
def chat_job(job_id):
    result = chat_runtime.status(job_id, active_database())
    if result is None:
        return jsonify({"error": "查询任务不存在，请重新发送问题。"}), 404
    return jsonify(result)


@app.post("/api/chat/feedback")
def chat_feedback():
    payload = request.get_json(silent=True) or {}
    if type(payload.get("helpful")) is not bool or not isinstance(payload.get("job_id"), str):
        return jsonify({"error": "反馈参数无效"}), 400
    try:
        return jsonify(chat_runtime.feedback(payload["job_id"], active_database(), payload["helpful"]))
    except ValueError as error:
        return jsonify({"error": str(error)}), 404


@app.get("/api/harness/workflows")
def harness_workflows():
    store = WorkflowStore(chat_runtime.directory(active_database()), [VERSION, *chat_runtime.model_identity()])
    return jsonify({"version": VERSION, "workflows": store.list()})


# 7. [已停用] 实时分析接口已转向后台同步引擎以优化成本与稳定性
# 如需分析新章节，请手动运行: python3 src/core/pipelines/sequential/sequential_engine.py

if __name__ == '__main__':
    app.run(port=5001)
