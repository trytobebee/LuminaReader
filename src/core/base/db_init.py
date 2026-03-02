import sqlite3
import os

db_path = '/Users/bytedance/code/LuminaReader/data/knowledge_base.db'

def init_db():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 章节表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS chapters (
        id INTEGER PRIMARY KEY,
        chapter_index INTEGER UNIQUE,
        title TEXT,
        summary TEXT,
        key_events TEXT,
        status TEXT DEFAULT 'pending', -- pending, processing, completed
        start_line INTEGER,
        end_line INTEGER
    )
    ''')
    
    # 实体表 (人物, 角色, 地点, 物品)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        type TEXT, -- person, location, item, organization
        description TEXT,
        first_chap_id INTEGER,
        last_updated_chap_id INTEGER,
        FOREIGN KEY (first_chap_id) REFERENCES chapters(id)
    )
    ''')
    
    # 知识点与背景细节
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS knowledge_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT, -- plot_point, world_setting, power_system
        content TEXT,
        source_chap_id INTEGER,
        is_verified INTEGER DEFAULT 1, -- 0 for AI suggested, 1 for user verified
        FOREIGN KEY (source_chap_id) REFERENCES chapters(id)
    )
    ''')
    
    # 实体演进日志 (解决痛点 1: 时间线溯源)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS entity_evolutions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_name TEXT,
        chapter_index INTEGER,
        evolution_type TEXT, -- status_change, relation_update, background_reveal
        content TEXT,
        FOREIGN KEY (entity_name) REFERENCES entities(name)
    )
    ''')
    
    # 实体关系表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS relationships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER,
        target_id INTEGER,
        relation_type TEXT,
        description TEXT,
        first_chap_id INTEGER,
        last_updated_chap_id INTEGER,
        FOREIGN KEY (source_id) REFERENCES entities(id),
        FOREIGN KEY (target_id) REFERENCES entities(id)
    )
    ''')
    
    conn.commit()
    conn.close()
    print(f"Knowledge base initialized at {db_path}")

if __name__ == "__main__":
    init_db()
