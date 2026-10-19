import socket
from datetime import datetime, timezone

import pytest

from app.news import FetchError, NewsSource, canonical_url, parse_rss, parse_time, public_addresses, safe_get
from conftest import run


@pytest.mark.parametrize('ip',['127.0.0.1','10.0.0.1','192.168.1.1','169.254.169.254','::1','198.18.0.1','0.0.0.0','224.0.0.1'])
def test_private_dns_blocked(monkeypatch,ip):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *_args,**_kwargs:[(2,1,6,'',(ip,443))])
    with pytest.raises(FetchError):public_addresses('example.com',443)


def test_curated_fake_ip_only(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *_args,**_kwargs:[(2,1,6,'',('198.18.0.1',443))])
    assert public_addresses('huggingface.co',443,curated=True)==['198.18.0.1']
    with pytest.raises(FetchError):safe_get('https://example.com/story',curated=True)


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
