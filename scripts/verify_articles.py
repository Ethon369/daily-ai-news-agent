"""Verify real article extraction; never calls a model or sends email."""
import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.news import NewsSource, make_item
from app.storage import dumps


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',action='append',required=True)
    parser.add_argument('--output-dir',default='artifacts/article-fetch')
    args=parser.parse_args()
    output=Path(args.output_dir)
    output.mkdir(parents=True,exist_ok=True)
    source=NewsSource()
    rows=[]
    for url in args.url:
        row={'url':url}
        try:
            item=make_item('Explicit article verification',url,'manual verification')
            text=await source.article(item)
            path=output/(item.id+'.txt')
            path.write_text(text,encoding='utf-8')
            row.update(status='success',characters=len(text),path=str(path))
        except Exception as exc:
            row.update(status='failure',error_type=type(exc).__name__,message=str(exc))
        rows.append(row)
        print(dumps(row),flush=True)
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'articles':rows,'email_sent':False}
    (output/'report.json').write_text(dumps(report),encoding='utf-8')


if __name__=='__main__':
    asyncio.run(main())
