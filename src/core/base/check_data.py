import sqlite3
DB_PATH = '/Users/bytedance/code/LuminaReader/data/knowledge_base.db'
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
res = cursor.execute("SELECT * FROM entities WHERE name='秦宇'").fetchone()
print(f"Qin Yu Row: {res}")
conn.close()
