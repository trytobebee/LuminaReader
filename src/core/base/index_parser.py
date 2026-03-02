import re
import json
import os
import sqlite3
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Lumina.Indexer")

# 配置路径
STORY_PATH = '/Users/bytedance/code/LuminaReader/data/story.txt'
DB_PATH = '/Users/bytedance/code/LuminaReader/data/knowledge_base.db'

def cn_to_int(cn):
    """简单的中文数字转整数 (支持常见的章号格式)"""
    cn_map = {'零':0, '一':1, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9,
              '十':10, '百':100, '千':1000, '万':10000}
    if not cn: return 0
    if cn.isdigit(): return int(cn)
    
    res = 0
    unit = 1
    for i in range(len(cn)-1, -1, -1):
        d = cn_map.get(cn[i])
        if d >= 10:
            if d > unit: unit = d
            else: unit *= d
        else:
            res += d * unit
    return res if res > 0 else unit # 处理 "十" 这种开头的情况

def index_story_advanced(file_path):
    """
    高级章节解析器：
    1. 识别卷 (Volume) 和 章 (Chapter)
    2. 基于顶格特征过滤干扰（如“第五章纸”）
    3. 识别疑似标题并打出日志
    4. 校验章节长度与完整性
    """
    chapters = []
    
    # 顶格正则匹配
    vol_pattern = re.compile(r'^第[一二三四五六七八九十百千万\d]+卷\s+(.*)$')
    chap_pattern = re.compile(r'^第([0-9一二三四五六七八九十百千万]+)章\s*(.*)$')
    
    # 非顶格但像标题的正则（用于定位潜在错误）
    fuzzy_chap_pattern = re.compile(r'第[0-9一二三四五六七八九十百千万]+章')
    
    INDENT_CHAR = '\u3000'
    
    logger.info(f"� 开始深度特征解析: {file_path}")
    
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
        
        current_volume = "默认卷"
        current_chapter = None
        seen_numbers = {} # {number: [titles]}
        
        for i, line in enumerate(lines):
            line_raw = line.replace('\n', '')
            line_clean = line_raw.strip()
            if not line_clean: continue

            # --- 特征分析层 ---
            
            # 情况 A: 行首有缩进 (大概率是正文)
            if line_raw.startswith(INDENT_CHAR) or line_raw.startswith(' '):
                # 即使有缩进，如果内容包含了“第xx章”，可能是排版错误的章节头
                if fuzzy_chap_pattern.search(line_clean) and len(line_clean) < 30:
                    logger.warning(f"⚠️  [疑似错位章节] 第 {i+1} 行有缩进但内容极像标题: \"{line_clean}\"")
                continue
            
            # 情况 B: 顶格写 (可能是标题或特殊正文)
            
            # 1. 识别卷
            vol_match = vol_pattern.match(line_clean)
            if vol_match:
                current_volume = line_clean
                logger.info(f"📖 确认分卷: {current_volume} (L{i+1})")
                continue
                
            # 2. 识别章
            chap_match = chap_pattern.match(line_clean)
            if chap_match:
                # 再次确认：只有在这一行长得离谱（比如超过50字）时才认为是正文误判
                if len(line_clean) > 50:
                    logger.warning(f"🔍 忽略过长顶格行 (非标题): {line_clean[:10]}...")
                    continue
                
                # 序列检测：重复与遗漏
                num_str = chap_match.group(1)
                num_int = cn_to_int(num_str)
                
                if num_int in seen_numbers:
                    logger.error(f"❌ [序号重复] 第 {num_int} 章出现多次: \"{seen_numbers[num_int]}\" VS \"{line_clean}\" (L{i+1})")
                    seen_numbers[num_int].append(line_clean)
                else:
                    seen_numbers[num_int] = [line_clean]

                if current_chapter:
                    # 校验上一个章节的长度
                    length = i - current_chapter['start_line']
                    if length < 30:
                        logger.warning(f"❗ [短章预警] \"{current_chapter['title']}\" 仅 {length} 行，请检查是否解析中断或全文缺失。")
                    
                    current_chapter['end_line'] = i - 1
                    chapters.append(current_chapter)
                
                current_chapter = {
                    'chapter_index': num_int,
                    'volume': current_volume,
                    'title': chap_match.group(2).strip(),
                    'start_line': i,
                    'end_line': -1,
                    'status': 'pending'
                }
            else:
                # 既不是卷也不是章，但又是顶格写的非空行
                if fuzzy_chap_pattern.search(line_clean):
                    logger.warning(f"🕵️  [格式异常] 第 {i+1} 行顶格且含章节关键字，但不符合标准格式: \"{line_clean}\"")

        # 最后一章处理
        if current_chapter:
            current_chapter['end_line'] = len(lines) - 1
            chapters.append(current_chapter)

    # 3. 结果清点与断层检测
    valid_chapters = [c for c in chapters if (c['end_line'] - c['start_line']) >= 5]
    
    # 查找遗漏的章号
    nums = sorted(seen_numbers.keys())
    if nums:
        for i in range(len(nums)-1):
            if nums[i+1] - nums[i] > 1:
                # 记录断层
                missing_range = range(nums[i] + 1, nums[i+1])
                logger.error(f"🕳️ [序号遗漏] 发现章节断层: 第 {nums[i]} 章 之后缺失了 第 {list(missing_range)[:5]}... 章")

    logger.info(f"✅ 解析完成。共发现 {len(valid_chapters)} 个合规章节，已剔除 {len(chapters) - len(valid_chapters)} 个极短碎片。")
    return valid_chapters

def sync_to_db(chapters, all_lines):
    """将解析结果同步到数据库，包括原文内容"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 结构检查
    try:
        cursor.execute("ALTER TABLE chapters ADD COLUMN volume TEXT")
    except sqlite3.OperationalError: pass
    try:
        cursor.execute("ALTER TABLE chapters ADD COLUMN content TEXT")
    except sqlite3.OperationalError: pass
        
    logger.info(f"💾 正在同步 {len(chapters)} 个章节到数据库...")
    
    cursor.execute("DELETE FROM chapters")
    
    for c in chapters:
        # 提取原文内容（跳过标题行，从 start_line + 1 开始）
        lines_to_save = [line.lstrip() for line in all_lines[c['start_line'] + 1 : c['end_line'] + 1]]
        chapter_content = "".join(lines_to_save)
        
        cursor.execute('''
            INSERT INTO chapters (chapter_index, title, volume, start_line, end_line, content, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (c['chapter_index'], c['title'], c['volume'], c['start_line'], c['end_line'], chapter_content, 'pending'))
        
    conn.commit()
    conn.close()
    logger.info("✨ 数据库同步与内容灌库成功。")

if __name__ == "__main__":
    if not os.path.exists(STORY_PATH):
        logger.error(f"❌ 找不到源文件: {STORY_PATH}")
        exit(1)

    # 执行解析
    with open(STORY_PATH, 'r', encoding='utf-8', errors='ignore') as f:
        all_lines = f.readlines()
        
    raw_chapters = index_story_advanced(STORY_PATH)
    sync_to_db(raw_chapters, all_lines)
    
    # 持久化索引备份
    output_path = '/Users/bytedance/code/LuminaReader/data/chapters_index.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(raw_chapters, f, ensure_ascii=False, indent=2)
    
    logger.info(f"🏁 全链路索引重建任务结束。")
