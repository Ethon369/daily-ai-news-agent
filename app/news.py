import asyncio
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from defusedxml import ElementTree

from .schemas import NewsItem

SOURCES = {
    'huggingface': {'name': 'Hugging Face Blog', 'url': 'https://huggingface.co/blog/feed.xml'},
    'hackernews': {'name': 'Hacker News', 'url': 'https://hacker-news.firebaseio.com/v0/topstories.json'},
}
MAX_BODY = 2_000_000


class FetchError(Exception):
    pass


def canonical_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise FetchError('无效新闻 URL')
    if parts.port and parts.port not in (80, 443):
        raise FetchError('不支持的 URL 端口')
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', urlencode(query), ''))


def public_addresses(host, port, *, curated=False):
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FetchError('来源 DNS 解析失败') from exc
    addresses = list(dict.fromkeys(result[4][0] for result in results))
    # Fake-IP proxy DNS (198.18/15) is supported ONLY for the two code-owned
    # feed endpoints, never for article URLs or user/model-supplied addresses.
    proxy_range = ipaddress.ip_network('198.18.0.0/15')
    allowed_proxy = curated and addresses and all(ipaddress.ip_address(ip) in proxy_range for ip in addresses)
    if not addresses or (not allowed_proxy and any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in addresses)):
        raise FetchError('禁止访问本机、内网或保留地址')
    return addresses


def safe_get(url, timeout=10, *, curated=False):
    """Pin each connection to validated DNS answers; TLS still verifies the hostname.

    No proxy inheritance, cookies, credentials or automatic redirects. The DNS
    lookup and actual TCP peer cannot be swapped through a second lookup.
    """
    for _ in range(4):
        url = canonical_url(url)
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        is_feed = (url == SOURCES['huggingface']['url'] or url == SOURCES['hackernews']['url'] or
                   host == 'hacker-news.firebaseio.com' and parts.scheme == 'https' and
                   re.fullmatch(r'/v0/item/\d+\.json', parts.path) and not parts.query and port == 443)
        addresses = public_addresses(host, port, curated=bool(curated and is_feed))
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        raw = None
        try:
            raw = socket.create_connection((addresses[0], port), timeout=timeout)
            conn.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if parts.scheme == 'https' else raw
            conn.request('GET', parts.path + ('?' + parts.query if parts.query else ''), headers={
                'User-Agent': 'DailyAIBrief/0.1 (personal research reader)', 'Accept-Encoding': 'identity',
                'Accept': 'application/rss+xml,application/json,text/html,application/xml;q=0.9,*/*;q=0.5',
            })
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise FetchError('无效来源重定向')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise FetchError(f'来源 HTTP {response.status}')
            if response.getheader('Content-Encoding', 'identity') != 'identity':
                raise FetchError('不支持压缩的来源响应')
            data = response.read(MAX_BODY + 1)
            if len(data) > MAX_BODY:
                raise FetchError('来源正文超过大小限制')
            return data, response.getheader('Content-Type', '')
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError(f'来源网络请求失败 ({type(exc).__name__})') from exc
        finally:
            conn.close()
            if raw:
                raw.close()
    raise FetchError('来源重定向次数超限')


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
            data, _ = await asyncio.to_thread(safe_get, SOURCES[source_id]['url'], curated=True)
            items = parse_rss(data)
        else:
            data, _ = await asyncio.to_thread(safe_get, SOURCES[source_id]['url'], curated=True)
            ids = json.loads(data)[:30]
            sem = asyncio.Semaphore(6)

            async def fetch_one(item_id):
                if not isinstance(item_id, int):
                    return None
                async with sem:
                    try:
                        raw, _ = await asyncio.to_thread(safe_get, f'https://hacker-news.firebaseio.com/v0/item/{item_id}.json', curated=True)
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
        if not any(t in content_type.lower() for t in ('text/', 'html', 'xml')):
            raise FetchError('来源不提供可读取的文本正文')
        text = clean_text(raw.decode('utf-8', errors='replace'), 12000)
        if not text:
            raise FetchError('未获取到正文')
        return text
