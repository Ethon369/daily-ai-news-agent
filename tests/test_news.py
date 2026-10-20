import socket
from datetime import datetime, timezone

import pytest

from app.news import FetchError, NewsSource, canonical_url, parse_rss, parse_time, public_addresses, safe_get, extract_article
from conftest import run


@pytest.mark.parametrize('ip',['127.0.0.1','10.0.0.1','192.168.1.1','169.254.169.254','::1','0.0.0.0','224.0.0.1'])
def test_private_dns_blocked(monkeypatch,ip):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *_args,**_kwargs:[(2,1,6,'',(ip,443))])
    with pytest.raises(FetchError):public_addresses('example.com',443)


def test_fake_ip_resolved_and_rechecked(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *_args,**_kwargs:[(2,1,6,'',('198.18.0.1',443))])
    monkeypatch.setattr('app.network.resolve_public_dns',lambda host:['93.184.216.34'])
    assert public_addresses('example.com',443)==['93.184.216.34']
    monkeypatch.setattr('app.network.resolve_public_dns',lambda host:['127.0.0.1'])
    with pytest.raises(FetchError):public_addresses('example.com',443)


def test_url_normalization():
    assert canonical_url('https://EXAMPLE.com/news?utm_source=x&id=2#hello')=='https://example.com/news?id=2'
    for url in ['file:///etc/passwd','http://user:pass@example.com','http://example.com:8000/x']:
        with pytest.raises(FetchError):canonical_url(url)


def test_rss_unknown_date_and_entities():
    xml=b'<rss><channel><item><title>Model</title><link>https://example.com/a</link><description>&lt;b&gt;text&lt;/b&gt;</description></item></channel></rss>'
    item=parse_rss(xml)[0]
    assert item.published_at is None and item.excerpt=='text'
    assert parse_time('bad') is None
    assert parse_time('2026-10-18T01:00:00') is None
    assert parse_time('2026-10-18T01:00:00Z').tzinfo
    with pytest.raises(Exception):parse_rss(b'<!DOCTYPE x [<!ENTITY evil SYSTEM "file:///etc/passwd">]><rss>&evil;</rss>')


def test_time_filter(monkeypatch):
    xml=b'<rss><channel><item><title>Old</title><link>https://example.com/old</link><pubDate>Thu, 01 Jan 2026 00:00:00 GMT</pubDate></item><item><title>Unknown</title><link>https://example.com/new</link></item></channel></rss>'
    monkeypatch.setattr('app.news.safe_get',lambda *a,**k:(xml,'application/xml'))
    items,_=run(NewsSource().fetch('huggingface',datetime(2026,10,18,tzinfo=timezone.utc)))
    assert [i.title for i in items]==['Unknown']


def test_extract_main_article_and_remove_navigation():
    story='Article text describing actual model capabilities and limitations. '*8
    raw=f'<html><body><nav>navigation</nav><main>menu<article><h1>Release</h1><p>{story}</p><script>secret()</script><aside>advertising</aside></article></main></body></html>'.encode()
    text=extract_article(raw,'text/html; charset=utf-8')
    assert story.strip() in text and 'Release' in text
    assert all(word not in text for word in ('navigation','menu','secret','advertising'))


def test_extract_charset_and_limit():
    text='这是一篇新闻正文，包含发布信息与使用限制。'*1000
    result=extract_article(('<article>'+text+'</article>').encode('gb18030'),'text/html; charset=gb18030')
    assert result.startswith('这是一篇新闻正文') and len(result)==12000


@pytest.mark.parametrize('raw,content_type',[
    (b'<html><title>Just a moment...</title><body>'+b'challenge '*40+b'</body></html>','text/html'),
    (b'<article>Title only</article>','text/html'),
    (b'%PDF-1.0','application/pdf'),
])
def test_non_article_responses_are_not_evidence(raw,content_type):
    with pytest.raises(FetchError):extract_article(raw,content_type)
