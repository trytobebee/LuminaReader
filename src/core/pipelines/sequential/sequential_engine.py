import sqlite3
import os
import time
import logging
from src.core.engine.summarizer import ChapterSummarizer
from dotenv import load_dotenv

load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Lumina.Sequential")

DB_PATH = '/Users/bytedance/code/LuminaReader/data/knowledge_base.db'
STORY_PATH = '/Users/bytedance/code/LuminaReader/data/story.txt'

def run_sequential_analysis(limit=10, use_local=True):
    """
    【顺序解构引擎】
    支持本地 MLX 流式引擎（极速）与在线 API 模式。
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    summarizer = ChapterSummarizer(conn, use_local=use_local)
    
    # 查找未完成的章节
    pending_chapters = conn.execute('''
        SELECT chapter_index, title, content 
        FROM chapters 
        WHERE status = 'pending' 
        ORDER BY chapter_index ASC 
        LIMIT ?
    ''', (limit,)).fetchall()
    
    if not pending_chapters:
        logger.info("💡 没有待处理的章节。")
        return

    mode_name = "本地 MLX 模式" if use_local else "在线 API 模式"
    logger.info(f"🚀 [{mode_name}] 开始处理 {len(pending_chapters)} 个章节...")
    
    for row in pending_chapters:
        idx = row['chapter_index']
        title = row['title']
        content = row['content']
        
        logger.info(f"📖 正在分析 (Ch{idx}): {title}...")
        
        # 触发调用并落库
        result = summarizer.analyze_chapter(idx, title, content)
        
        if "error" in result:
            logger.error(f"❌ {title} 处理出错: {result['error']}")
            break
        else:
            logger.info(f"✅ {title} 完成解构与知识沉淀。")
            
        if not use_local:
            time.sleep(1) 

    conn.close()
    logger.info(f"✨ {mode_name} 处理链路结束。")

if __name__ == "__main__":
    # 默认开启本地极速模式进行大规模处理
    run_sequential_analysis(limit=20, use_local=True)
