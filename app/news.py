import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from email.message import Message

from bs4 import BeautifulSoup
from defusedxml import ElementTree

from .schemas import NewsItem
from .network import FetchError, canonical_url, public_addresses, safe_get

SOURCES = {
    'huggingface': {'name': 'Hugging Face Blog', 'url': 'https://huggingface.co/blog/feed.xml'},
    'hackernews': {'name': 'Hacker News', 'url': 'https://hacker-news.firebaseio.com/v0/topstories.json'},
}


def clean_text(value, limit=6000):
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'noscript']):
        tag.decompose()
    return soup.get_text(' ', strip=True)[:limit]


def parse_time(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (ValueError, TypeError):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    # Do not invent the timezone of a source timestamp.
    return dt.astimezone(timezone.utc) if dt.tzinfo else None


def make_item(title, url, source, date=None, excerpt=''):
    url = canonical_url(url)
    return NewsItem(id=hashlib.sha256(url.encode()).hexdigest()[:20], title=clean_text(title, 300),
                    url=url, source=source, published_at=date, excerpt=clean_text(excerpt))


def parse_rss(data):
    root = ElementTree.fromstring(data)
    result = []
    for entry in root.findall('.//item')[:50]:
        url = entry.findtext('link')
        if not url:
            continue
        try:
            result.append(make_item(entry.findtext('title') or '无标题', url, 'Hugging Face Blog',
                                   parse_time(entry.findtext('pubDate')), entry.findtext('description') or ''))
        except FetchError:
            continue
    return result


class NewsSource:
    async def fetch(self, source_id, started_at):
        if source_id not in SOURCES:
            raise FetchError('未知来源，允许 huggingface / hackernews')
        if source_id == 'huggingface':
            data, _ = await asyncio.to_thread(safe_get, SOURCES[source_id]['url'])
            items = parse_rss(data)
        else:
            data, _ = await asyncio.to_thread(safe_get, SOURCES[source_id]['url'])
            ids = json.loads(data)[:30]
            sem = asyncio.Semaphore(6)

            async def fetch_one(item_id):
                if not isinstance(item_id, int):
                    return None
                async with sem:
                    try:
                        raw, _ = await asyncio.to_thread(safe_get, f'https://hacker-news.firebaseio.com/v0/item/{item_id}.json')
                        item = json.loads(raw)
                        if not item or item.get('type') != 'story' or item.get('deleted') or item.get('dead'):
                            return None
                        return make_item(item.get('title', '无标题'), item.get('url') or f'https://news.ycombinator.com/item?id={item_id}',
                                         'Hacker News', datetime.fromtimestamp(item['time'], timezone.utc) if item.get('time') else None,
                                         item.get('text', ''))
                    except (FetchError, ValueError, KeyError, TypeError):
                        return 'failed'
            values = await asyncio.gather(*(fetch_one(i) for i in ids))
            if values and all(v == 'failed' for v in values):
                raise FetchError('Hacker News 条目请求全部失败')
            items = [v for v in values if isinstance(v, NewsItem)]
            partial = sum(v == 'failed' for v in values)
        since = started_at - timedelta(hours=24)
        eligible = [i for i in items if i.published_at is None or since <= i.published_at <= started_at]
        return eligible[:30], (f'{partial} 个条目读取失败' if source_id == 'hackernews' and partial else None)

    async def article(self, item):
        raw, content_type = await asyncio.to_thread(safe_get, item.url)
        return extract_article(raw, content_type)


def extract_article(raw, content_type):
    media_type = content_type.split(';', 1)[0].strip().lower()
    if media_type not in ('text/html', 'application/xhtml+xml', 'text/plain'):
        raise FetchError('来源不提供可读取的网页正文')
    header = Message()
    header['content-type'] = content_type
    soup = BeautifulSoup(raw, 'html.parser', from_encoding=header.get_content_charset())
    title = soup.title.get_text(' ', strip=True).lower() if soup.title else ''
    if any(marker in title for marker in ('just a moment', 'access denied', 'verify you are human', 'attention required!')):
        raise FetchError('来源返回访问验证页，未获取正文')
    main = soup.select_one('article') or soup.find('main') or soup.body or soup
    for tag in main(['script', 'style', 'nav', 'footer', 'header', 'noscript', 'form', 'aside']):
        tag.decompose()
    text = main.get_text(' ', strip=True)
    if len(text) < 160:
        raise FetchError('网页正文内容过短，无法作为摘要依据')
    return text[:12000]
