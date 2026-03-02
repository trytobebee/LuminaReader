import os
import sys

# 允许直接从根目录启动，自动识别 src 包
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import json
from src.core.engine.summarizer import ChapterSummarizer

app = Flask(__name__)
CORS(app)

DB_PATH = '/Users/bytedance/code/LuminaReader/data/knowledge_base.db'
STORY_PATH = '/Users/bytedance/code/LuminaReader/data/story.txt'

DB_PATH = '/Users/bytedance/code/LuminaReader/data/knowledge_base.db'
STORY_PATH = '/Users/bytedance/code/LuminaReader/data/story.txt'

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# 1. 获取章节列表
@app.route('/api/chapters', methods=['GET'])
def get_chapters():
    conn = get_db_connection()
    chapters = conn.execute('SELECT * FROM chapters ORDER BY chapter_index ASC').fetchall()
    conn.close()
    return jsonify([dict(ix) for ix in chapters])

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
    
    conn.close()
    return jsonify([dict(ix) for ix in entities])

# 5. 知识点管理: 添加/更新实体
@app.route('/api/entities', methods=['POST'])
def upsert_entity():
    data = request.json
    name = data.get('name')
    type = data.get('type')
    description = data.get('description')
    
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO entities (name, type, description)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
        type = excluded.type,
        description = excluded.description
    ''', (name, type, description))
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
    user_msg = request.json.get('message', '').lower()
    conn = get_db_connection()
    
    # 尝试从实体库匹配关键词
    entities = conn.execute('SELECT * FROM entities').fetchall()
    found_context = ""
    for ent in entities:
        if ent['name'].lower() in user_msg:
            found_context += f"【{ent['name']}】: {ent['description']} "
            
    # 尝试从最近总结的章节中提取上下文
    last_summaries = conn.execute('SELECT title, summary FROM chapters WHERE status="completed" ORDER BY chapter_index DESC LIMIT 3').fetchall()
    context_summaries = " 最近剧情概况: " + " | ".join([f"{s['title']}:{s['summary']}" for s in last_summaries])

    conn.close()
    
    if found_context:
        response = f"根据解构的知识，关于您的提问：{found_context}。另外，{context_summaries}"
    elif last_summaries:
        response = f"目前我正在分析前面的章节，已了解到：{context_summaries}。您可以问我关于这些内容的问题。"
    else:
        response = "我正在扫描这部 840 万字的巨作。目前知识库还是空的，请选择一个章节开始分析！"
        
    return jsonify({"response": response})

# 7. [已停用] 实时分析接口已转向后台同步引擎以优化成本与稳定性
# 如需分析新章节，请手动运行: python3 src/core/pipelines/sequential/sequential_engine.py

if __name__ == '__main__':
    app.run(port=5001, debug=True)
