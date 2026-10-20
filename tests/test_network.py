import json
import socket

import pytest

from app.network import FetchError, public_addresses, resolve_public_dns, safe_get


def dns_payload(host='example.com', addresses=('93.184.216.34',)):
    return {'Status': 0, 'Question': [{'name': host+'.', 'type': 1}],
            'Answer': [{'type': 1, 'data': ip} for ip in addresses]}


def test_doh_uses_fixed_peer_and_verified_endpoint(monkeypatch):
    seen=[]
    def request(url, address, timeout, **kwargs):
        seen.append((url,address,kwargs))
        return 200, {'content-type':'application/dns-json'}, json.dumps(dns_payload()).encode()
    monkeypatch.setattr('app.network._read_once',request)
    assert resolve_public_dns('example.com')==['93.184.216.34']
    assert seen[0][0]=='https://cloudflare-dns.com/dns-query?name=example.com&type=A'
    assert seen[0][1]=='1.1.1.1'


@pytest.mark.parametrize('payload',[
    dns_payload(addresses=('127.0.0.1',)),
    dns_payload(addresses=('93.184.216.34','10.0.0.1')),
    dns_payload(addresses=('198.18.0.1',)),
    dns_payload(host='other.example'),
    dns_payload(addresses=()),
    {**dns_payload(),'Status':3},
    {**dns_payload(),'TC':True},
    {**dns_payload(),'Question':None},
    {**dns_payload(),'Answer':None},
    [],
])
def test_doh_rejects_invalid_answers(monkeypatch,payload):
    monkeypatch.setattr('app.network._read_once',lambda *a,**k:(200,{'content-type':'application/dns-json'},json.dumps(payload).encode()))
    with pytest.raises(FetchError):resolve_public_dns('example.com')


def test_doh_failure_never_connects_to_fake_ip(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('198.18.0.5',443))])
    def fail(host):raise FetchError('DoH unavailable')
    monkeypatch.setattr('app.network.resolve_public_dns',fail)
    contacted=[]
    monkeypatch.setattr('app.network._read_once',lambda *a,**k:contacted.append(a))
    with pytest.raises(FetchError):safe_get('https://example.com/article')
    assert contacted==[]


def test_mixed_private_dns_never_uses_doh(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',(ip,443)) for ip in ('198.18.0.5','10.0.0.1')])
    monkeypatch.setattr('app.network.resolve_public_dns',lambda _:pytest.fail('must not resolve mixed private answers'))
    with pytest.raises(FetchError):public_addresses('example.com',443)


@pytest.mark.parametrize('host',['localhost','localhost.','service.local','service.internal','router.home.arpa','127.0.0.1','::1','169.254.169.254','::ffff:127.0.0.1','::ffff:224.0.0.1'])
def test_local_targets_rejected_before_dns(monkeypatch,host):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:pytest.fail('must not resolve local targets'))
    with pytest.raises(FetchError):public_addresses(host,443)


def test_redirect_private_target_blocked(monkeypatch):
    seen=[]
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    def redirect(url,address,timeout):
        seen.append(address)
        return 302,{'location':'http://127.0.0.1/secret'},b''
    monkeypatch.setattr('app.network._read_once',redirect)
    with pytest.raises(FetchError):safe_get('https://example.com/article')
    assert seen==['93.184.216.34']


def test_public_redirect_revalidated_and_pinned(monkeypatch):
    seen=[]
    def lookup(host,*a,**k):return [(2,1,6,'',('93.184.216.34' if host=='example.com' else '1.1.1.1',443))]
    monkeypatch.setattr(socket,'getaddrinfo',lookup)
    def request(url,address,timeout):
        seen.append((url,address))
        if len(seen)==1:return 302,{'location':'https://other.example/article'},b''
        return 200,{'content-type':'text/html'},b'article'
    monkeypatch.setattr('app.network._read_once',request)
    assert safe_get('https://example.com/article')[0]==b'article'
    assert [ip for _,ip in seen]==['93.184.216.34','1.1.1.1']


def test_tries_next_validated_peer(monkeypatch):
    seen=[]
    monkeypatch.setattr('app.network.public_addresses',lambda *a:['93.184.216.34','1.1.1.1'])
    def request(url,address,timeout):
        seen.append(address)
        if len(seen)==1:raise ConnectionRefusedError()
        return 200,{'content-type':'text/plain'},b'ok'
    monkeypatch.setattr('app.network._read_once',request)
    assert safe_get('https://example.com/article')[0]==b'ok'
    assert len(seen)==2
