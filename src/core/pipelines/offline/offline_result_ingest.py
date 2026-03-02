import json
import sqlite3
import os
from src.core.engine.summarizer import ChapterSummarizer

DB_PATH = '/Users/bytedance/code/LuminaReader/data/knowledge_base.db'
RESULT_PATH = '/Users/bytedance/code/LuminaReader/data/batch_results.jsonl'

def ingest_offline_results():
    """
    【模式 2-C：离线结果回填】
    将下载好的 batch_results.jsonl 中的解构内容同步到本地数据库。
    """
    if not os.path.exists(RESULT_PATH):
        print(f"❌ 未找到结果文件: {RESULT_PATH}")
        print("💡 请先在火山引擎下载处理完成的文件并保存为 data/batch_results.jsonl")
        return

    conn = sqlite3.connect(DB_PATH)
    summarizer = ChapterSummarizer(conn)
    
    success_count = 0
    print(f"📥 开始将离线解构成果导入数据库...")
    
    with open(RESULT_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                result = json.loads(line)
                custom_id = result.get('custom_id', '')
                if not custom_id.startswith('chap_'): continue
                
                chapter_index = int(custom_id.replace('chap_', ''))
                
                # 获取 AI 实际返回的 JSON 字符串
                choices = result.get('response', {}).get('body', {}).get('choices', [])
                if not choices: continue
                    
                ai_json_str = choices[0]['message']['content']
                
                # 调用统一的落库逻辑 (summarizer.py)
                if summarizer.process_ai_response(chapter_index, ai_json_str):
                    success_count += 1
                    
            except Exception as e:
                print(f"⚠️ 处理行出错: {e}")

    conn.close()
    print(f"✨ 导入完成! 成功更新 {success_count} 个章节的深度知识库。")

if __name__ == "__main__":
    ingest_offline_results()
