import re
import os
import shutil
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Lumina.Repairer")

STORY_PATH = '/Users/bytedance/code/LuminaReader/data/story.txt'
BACKUP_PATH = '/Users/bytedance/code/LuminaReader/data/story.txt.bak'

def repair_story_file():
    # 1. 备份
    if not os.path.exists(STORY_PATH):
        logger.error("❌ 找不到 story.txt 文件")
        return
    
    shutil.copy(STORY_PATH, BACKUP_PATH)
    logger.info(f"🛡️ 已创建文件备份: {BACKUP_PATH}")

    # 2. 读取
    with open(STORY_PATH, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    # 匹配模式
    # 卷：顶格写，第xx卷 (不参与编号修改，但用来识别边界)
    vol_pattern = re.compile(r'^第[一二三四五六七八九十百千万\d]+卷\s+(.*)$')
    # 章：顶格写，第xx章 (抓取核心，包括不规范的格式)
    chap_find_pattern = re.compile(r'^第[0-9一二三四五六七八九十百千万]+章\s*(.*)$')
    
    INDENT_CHAR = '\u3000'
    new_lines = []
    chapter_counter = 1
    repair_count = 0

    logger.info("🔧 开始扫描并重构章节号...")

    for i, line in enumerate(lines):
        line_raw = line.replace('\n', '').replace('\r', '')
        
        # 排除缩进行（正文）
        if line_raw.startswith(INDENT_CHAR) or line_raw.startswith(' '):
            new_lines.append(line)
            continue
            
        line_clean = line_raw.strip()
        if not line_clean:
            new_lines.append(line)
            continue

        # 检查是否是卷头 (跳过)
        if vol_pattern.match(line_clean):
            new_lines.append(line)
            continue

        # 检查是否是章号 (重点修复)
        match = chap_find_pattern.match(line_clean)
        if match:
            # 过滤掉过长的顶格行（可能是误报的正文）
            if len(line_clean) > 30:
                new_lines.append(line)
                continue

            # 提取原有的标题部分（去除原有的第xxx章）
            title_part = match.group(1).strip()
            
            # 构建新的标准标题格式
            new_header = f"第{chapter_counter}章"
            if title_part:
                new_header += f" {title_part}"
            
            # 如果发生了变化，记录日志
            if new_header != line_clean:
                repair_count += 1
                # 只打出前几个和关键的日志，避免刷屏
                if repair_count < 10 or repair_count % 100 == 0:
                    logger.info(f"📝 修正 (L{i+1}): [{line_clean}] -> [{new_header}]")
            
            new_lines.append(new_header + "\n")
            chapter_counter += 1
        else:
            # 其他顶格行（可能是空行、特殊标识等），原样保留
            new_lines.append(line)

    # 3. 写入回原文件
    with open(STORY_PATH, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

    logger.info(f"✅ 修复完成！")
    logger.info(f"📊 统计数据：")
    logger.info(f"   - 总章节数: {chapter_counter - 1}")
    logger.info(f"   - 修正标题数: {repair_count}")
    logger.info(f"   - 原文件已更新。")

if __name__ == "__main__":
    repair_story_file()
