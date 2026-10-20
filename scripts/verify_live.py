"""Read real sources and optionally run the real model. Never sends email."""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent import Agent
from app.config import Settings
from app.news import NewsSource, SOURCES
from app.storage import dumps
from app.tools import Toolset


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', action='store_true')
    parser.add_argument('--partial', action='store_true', help='Inject one source failure, keeping other source/model real')
    parser.add_argument('--output-dir', help='Separate evidence directory, preserving previous verification artifacts')
    args = parser.parse_args()
    cfg = Settings.from_env()
    out = Path(args.output_dir) if args.output_dir else Path('artifacts') / ('live-partial' if args.partial else 'live-normal')
    out.mkdir(parents=True, exist_ok=True)
    source = NewsSource()
    start = datetime.now(timezone.utc)
    cached = {}
    report = {'checked_at': start.isoformat(), 'model': cfg.model, 'model_verified': False, 'email_verified': False, 'sources': {}}
    for name in SOURCES:
        try:
            items, warning = await source.fetch(name, start)
            cached[name] = (items, warning)
            report['sources'][name] = {'status': 'success' if items else 'empty', 'count': len(items), 'warning': warning}
        except Exception as exc:
            report['sources'][name] = {'status': 'failure', 'error_type': type(exc).__name__, 'message': str(exc)[:200]}
        print(dumps({'source': name, **report['sources'][name]}), flush=True)
    if args.model:
        class CachedSource:
            async def fetch(self, name, _):
                if args.partial and name == 'huggingface':
                    raise RuntimeError('Deliberately injected source failure')
                if name not in cached:
                    raise RuntimeError('Live source unavailable')
                return cached[name]

            async def article(self, item):
                return await source.article(item)

        events = []
        def event(kind, payload):
            events.append({'kind': kind, 'payload': payload})
            print(dumps({'kind': kind, 'name': payload.get('name'), 'ok': payload.get('ok'), 'round': payload.get('round')}), flush=True)
            (out/'events.json').write_text(dumps(events), encoding='utf-8')

        profile = {'topics': ['AI', '大语言模型', '开发工具'], 'keywords': ['Agent', 'LLM', '开源']}
        workspace = out/'workspace'
        tools = Toolset(workspace, cfg, start, event, CachedSource())
        (workspace/'profile.json').write_text(dumps(profile), encoding='utf-8')
        (workspace/'history.json').write_text('[]', encoding='utf-8')
        try:
            result = await Agent(cfg).run(tools, profile)
            (out/'briefing.json').write_text(dumps(result), encoding='utf-8')
            report['model_verified'] = True
            report['items'] = len(result['items'])
        except Exception as exc:
            report['model_error'] = {'type': type(exc).__name__, 'message': str(exc)[:300]}
        report['partial_failure_injected'] = args.partial
    (out/'report.json').write_text(dumps(report), encoding='utf-8')
    print(dumps(report), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
