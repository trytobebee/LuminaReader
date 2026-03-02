import sqlite3
import json

db_path = '/Users/bytedance/code/LuminaReader/data/knowledge_base.db'
index_path = '/Users/bytedance/code/LuminaReader/data/chapters_index.json'

def sync_index():
    with open(index_path, 'r', encoding='utf-8') as f:
        chapters = json.load(f)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print(f"Syncing {len(chapters)} chapters...")
    
    for chap in chapters:
        cursor.execute('''
        INSERT OR IGNORE INTO chapters (chapter_index, title, start_line, end_line, status)
        VALUES (?, ?, ?, ?, 'pending')
        ''', (chap['index'], chap['title'], chap['start_line'], chap['end_line']))
    
    conn.commit()
    conn.close()
    print("Sync completed.")

if __name__ == "__main__":
    sync_index()
