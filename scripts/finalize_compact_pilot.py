"""Wait for local extraction, then finish global links under the same $10 cap."""
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.core.pipelines.novel.compact_index import connect,index_path,finalize_map

database=ROOT/'data/version-comparison/v2/book.db'
while True:
    ready=False
    if index_path(database).exists():
        with connect(index_path(database)) as conn:
            row=conn.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()
        ready=bool(row and json.loads(row[0]).get('status')=='published')
    process_file=database.parent/'preprocessing-process.json'
    worker=json.loads(process_file.read_text())['pid'] if process_file.exists() else None
    alive=False
    if worker:
        try:os.kill(worker,0);alive=True
        except ProcessLookupError:pass
    if ready and not alive:break
    time.sleep(10)
finalize_map(database,10.0)
