"""Run a bounded pilot. Existing source and V1 answers are never modified."""
import argparse
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.core.pipelines.novel.compact_index import preprocess


def prepare(source,target,start,end):
    target=Path(target); target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists(): return
    with sqlite3.connect('file:'+str(Path(source).resolve())+'?mode=ro',uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        dst.execute('DELETE FROM chapters WHERE chapter_index NOT BETWEEN ? AND ?',(start,end))
        dst.execute('DELETE FROM chapter_people WHERE chapter_index NOT BETWEEN ? AND ?',(start,end))
        dst.commit()
        dst.execute('VACUUM')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',default=str(ROOT/'data/reader_novel.db'))
    parser.add_argument('--database',default=str(ROOT/'data/version-comparison/v2/book.db'))
    parser.add_argument('--start',type=int,default=1738)
    parser.add_argument('--end',type=int,default=1841)
    parser.add_argument('--budget',type=float,default=10.0)
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    if not 0<args.budget<=10: parser.error('Authorized pilot preprocessing budget must be at most $10')
    prepare(args.source,args.database,args.start,args.end)
    if not args.prepare_only: preprocess(args.database,args.start,args.end,args.budget)
