import asyncio
from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.news import make_item
from app.storage import Store, dumps
from app.tools import Toolset


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path/'data', scheduler_enabled=False)


@pytest.fixture
def store(settings):
    db = Store(settings.data_dir)
    db.initialize()
    return db


@pytest.fixture
def sub(store):
    return store.save_subscription({'nickname':'测试订阅', 'email':'reader@example.com', 'topics':['LLM'],
                                   'keywords':['Agent'], 'send_time':'09:00', 'enabled':True})


class FixtureSource:
    def __init__(self, failures=(), empty=False):
        self.failures, self.empty = failures, empty

    async def fetch(self, name, started_at):
        if name in self.failures:
            raise RuntimeError('Injected source failure')
        if self.empty:
            return [], None
        return [make_item('AI tool update', 'https://example.com/ai', name, started_at, 'A release about tool calling.'),
                make_item('Model research', 'https://example.com/research', name, None, 'A research article.')], None

    async def article(self, item):
        return 'Test article content about AI tools, not live news.'


class ScriptedClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    async def complete(self, messages, tools):
        self.messages.append(list(messages))
        value = next(self.responses)
        if callable(value):
            value = value(messages)
        return value, {'total_tokens': 42}


def calls(*pairs):
    return {'tool_calls':[{'id':f'call_{i}', 'type':'function','function':{'name':name, 'arguments':dumps(args)}} for i,(name,args) in enumerate(pairs)]}


def final_message(messages):
    import json
    fetched = [json.loads(m['content']) for m in messages if m['role']=='tool']
    items = next(v['data']['items'] for v in reversed(fetched) if v.get('data',{}).__class__ is dict and 'items' in v['data'] and v['data']['items'])
    return {'content':dumps({'title':'测试简报', 'overview':'使用固定测试材料生成。', 'items':[{'title':'工具更新', 'summary':'工具调用能力更新。', 'relevance':'符合 Agent 偏好', 'source_ids':[items[0]['id']]}]})}


@pytest.fixture
def tools(settings):
    events = []
    toolset = Toolset(settings.data_dir/'run', settings, datetime.now(timezone.utc), lambda kind,payload:events.append((kind,payload)), FixtureSource())
    toolset.events = events
    (toolset.root/'profile.json').write_text('{"topics":["LLM"]}', encoding='utf-8')
    return toolset


def run(awaitable):
    return asyncio.run(awaitable)
