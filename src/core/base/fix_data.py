import sqlite3
import os

DB_PATH = '/Users/bytedance/code/LuminaReader/data/knowledge_base.db'

def fix_protagonist():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO entities (name, type, description, first_chap_id, last_updated_chap_id) 
            VALUES (?, ?, ?, ?, ?)
        ''', ('秦宇', 'person', '《超品相师》男主角，机缘巧合下获得诸葛亮传承《诸葛内经》，从此开启相师之路。', 1, 1))
        conn.commit()
        print("✅ 秦宇已成功补录进知识库。")
    except Exception as e:
        print(f"❌ 补录失败: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    fix_protagonist()
