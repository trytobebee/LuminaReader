import sqlite3
DB_PATH = '/Users/bytedance/code/LuminaReader/data/knowledge_base.db'

def reset_entity_origins():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("🧹 正在清理错误的实体起源数据...")
    
    # 1. 修正秦宇的起源
    cursor.execute("UPDATE entities SET first_chap_id = 1 WHERE name = '秦宇'")
    
    # 2. 修正二舅的起源 (二舅在第 3 章才出现的风水问题，由于此时没有跑完，先设为 3)
    cursor.execute("UPDATE entities SET first_chap_id = 3 WHERE name = '二舅'")

    # 3. 逻辑普适性修复：如果发现时间大于更新时间，说明是坏数据，统一对齐
    cursor.execute('''
        UPDATE entities 
        SET first_chap_id = last_updated_chap_id 
        WHERE first_chap_id > last_updated_chap_id
    ''')
    
    conn.commit()
    print("✅ 数据校正完成。")
    conn.close()

if __name__ == "__main__":
    reset_entity_origins()
