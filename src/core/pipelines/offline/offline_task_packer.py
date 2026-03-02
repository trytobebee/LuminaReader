import json
import sqlite3
import os
from src.core.engine.summarizer import ChapterSummarizer
from dotenv import load_dotenv

load_dotenv()

DB_PATH = '/Users/bytedance/code/LuminaReader/data/knowledge_base.db'
STORY_PATH = '/Users/bytedance/code/LuminaReader/data/story.txt'
BATCH_OUTPUT = '/Users/bytedance/code/LuminaReader/data/doubao_offline_tasks.jsonl'

def pack_offline_tasks(start_idx, end_idx):
    """
    【模式 2-A：离线任务打包】
    将大量章节内容按照 AI 解构模板打包成 JSONL 文件。
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    summarizer = ChapterSummarizer(conn)
    
    chapters = conn.execute('''
        SELECT chapter_index, title, start_line, end_line 
        FROM chapters 
        WHERE chapter_index BETWEEN ? AND ?
    ''', (start_idx, end_idx)).fetchall()
    
    if not chapters:
        print(f"没有找到索引在 {start_idx} 到 {end_idx} 之间的章节。")
        return

    with open(STORY_PATH, 'r', encoding='utf-8', errors='ignore') as f:
        all_lines = f.readlines()

    print(f"📦 [离线模式] 正在打包 {len(chapters)} 个解构任务...")
    
    with open(BATCH_OUTPUT, 'w', encoding='utf-8') as f_out:
        for chap in chapters:
            idx = chap['chapter_index']
            content = "".join(all_lines[chap['start_line']:chap['end_line']+1])
            
            # 使用统一的解构模版构建 Prompt
            context_data = summarizer.get_comprehensive_context(idx, content)
            full_prompt = summarizer.generate_prompt(chap['title'], content, context_data)
            
            task = {
                "custom_id": f"chap_{idx}",
                "method": "POST",
                "url": "/api/v3/chat/completions",
                "body": {
                    "model": os.getenv("OPENAI_MODEL"), # 必须是豆包的模型 ID
                    "messages": [
                        {"role": "system", "content": "你是一个只输出 JSON 的高阶文学解构助手。"},
                        {"role": "user", "content": full_prompt}
                    ],
                    "max_tokens": 500,
                    "response_format": {"type": "json_object"}
                }
            }
            f_out.write(json.dumps(task, ensure_ascii=False) + '\n')

    conn.close()
    print(f"✅ 打包完成: {BATCH_OUTPUT}")

if __name__ == "__main__":
    # 打包 100 章任务作为例子
    pack_offline_tasks(1, 100)
