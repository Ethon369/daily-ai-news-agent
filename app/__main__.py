import argparse
import asyncio
import json
from datetime import datetime

from .config import Settings
from .service import BEIJING, Service


def main():
    parser = argparse.ArgumentParser(description='每日 AI 简报命令行，生成预览不发信')
    parser.add_argument('--topics', default='大语言模型,AI Agent')
    parser.add_argument('--keywords', default='开源,工具调用')
    args = parser.parse_args()

    async def run():
        cfg = Settings.from_env()
        service = Service(cfg)
        service.lock.acquire()
        try:
            service.store.initialize()
            sub = service.store.save_subscription({'nickname': 'CLI 预览', 'email': 'preview@example.com',
                'topics': args.topics.split(','), 'keywords': args.keywords.split(','), 'send_time': '09:00', 'enabled': False})
            run_id, _ = service.store.create_run(sub, 'preview', datetime.now(BEIJING).date().isoformat())
            await service.execute_run(service.store.one('SELECT * FROM runs WHERE id=?', (run_id,)))
            result = service.store.one('SELECT id,status,error FROM runs WHERE id=?', (run_id,))
            print(json.dumps(result, ensure_ascii=False))
            if result['status'] != 'succeeded':
                raise SystemExit(1)
            print(str(cfg.data_dir/'runs'/run_id/'briefing.md'))
        finally:
            service.lock.release()

    asyncio.run(run())


if __name__ == '__main__':
    main()
