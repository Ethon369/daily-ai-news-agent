"""Bounded public HTTP reads with pinned DNS and verified TLS.

Fake-IP DNS responses are resolved through a fixed HTTPS DNS service. Neither
the Fake-IP address nor an unchecked redirect is ever used as a TCP target.
"""
import http.client
import ipaddress
import json
import socket
import ssl
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

MAX_BODY = 2_000_000
FAKE_IP_RANGE = ipaddress.ip_network('198.18.0.0/15')
DOH_HOST = 'cloudflare-dns.com'
DOH_ADDRESSES = ('1.1.1.1', '1.0.0.1')
LOCAL_SUFFIXES = ('.localhost', '.local', '.internal', '.home.arpa')


class FetchError(ValueError):
    pass


def canonical_url(url):
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) <= 32 or c == '\\' for c in url):
        raise FetchError('无效新闻 URL')
    try:
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            raise ValueError()
        if parts.port and parts.port not in (80, 443):
            raise ValueError()
    except ValueError as exc:
        raise FetchError('无效新闻 URL 或不支持的端口') from exc
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', urlencode(query), ''))


def _global(address):
    try:
        ip = ipaddress.ip_address(address)
    except (ValueError, TypeError):
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _read_once(url, address, timeout, *, limit=MAX_BODY, accept=None):
    """No DNS lookup here: address is the validated peer; hostname remains TLS SNI."""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    conn = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
    raw = None
    deadline = time.monotonic() + timeout
    try:
        raw = socket.create_connection((address, port), timeout=timeout)
        raw.settimeout(max(.01, deadline - time.monotonic()))
        conn.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parts.hostname) if parts.scheme == 'https' else raw
        peer = conn.sock
        conn.sock.settimeout(max(.01, deadline - time.monotonic()))
        conn.request('GET', parts.path + ('?' + parts.query if parts.query else ''), headers={
            'User-Agent': 'DailyAIBrief/0.1 (personal research reader)',
            'Accept-Encoding': 'identity',
            'Accept': accept or 'application/rss+xml,application/json,text/html,application/xml;q=0.9,*/*;q=0.5',
        })
        response = conn.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}
        if response.status != 200:
            return response.status, headers, b''
        if headers.get('content-encoding', 'identity').lower() != 'identity':
            raise FetchError('不支持压缩的来源响应')
        try:
            content_length = int(headers.get('content-length', '0'))
        except ValueError as exc:
            raise FetchError('来源返回无效正文长度') from exc
        if content_length < 0 or content_length > limit:
            raise FetchError('来源正文超过大小限制')
        data = bytearray()
        while len(data) <= limit:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            peer.settimeout(remaining)
            chunk = response.read1(min(16384, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > limit:
            raise FetchError('来源正文超过大小限制')
        return response.status, headers, bytes(data)
    finally:
        conn.close()
        if raw:
            raw.close()


def resolve_public_dns(host):
    """Use the fixed resolver only after a Fake-IP-only local DNS response.

    Query A records for predictable IPv4 connectivity, with no DNS cache or
    second hostname lookup between validation and connection. A resolver error
    or any non-public answer fails closed.
    """
    query = urlencode({'name': host, 'type': 'A'})
    url = f'https://{DOH_HOST}/dns-query?{query}'
    for address in DOH_ADDRESSES:
        try:
            status, headers, raw = _read_once(url, address, 5, limit=64000, accept='application/dns-json')
            if status != 200 or 'json' not in headers.get('content-type', '').lower():
                continue
            payload = json.loads(raw)
        except (OSError, http.client.HTTPException, ValueError):
            continue
        if not isinstance(payload, dict):
            raise FetchError('公共 DNS 返回格式无效')
        questions = payload.get('Question', [])
        if payload.get('Status') != 0 or payload.get('TC') or not isinstance(questions, list) or not any(
                isinstance(q, dict) and q.get('name', '').rstrip('.').lower() == host and q.get('type') == 1 for q in questions):
            raise FetchError('公共 DNS 未返回匹配的有效解析')
        answers = payload.get('Answer', [])
        if not isinstance(answers, list):
            raise FetchError('公共 DNS 返回格式无效')
        addresses = [a.get('data') for a in answers if isinstance(a, dict) and a.get('type') in (1, 28)]
        if not addresses or not all(isinstance(ip, str) and _global(ip) for ip in addresses):
            raise FetchError('公共 DNS 未返回纯公网地址，已阻止连接')
        return list(dict.fromkeys(addresses))
    raise FetchError('公共 DNS 查询失败，未连接代理占位地址')


def public_addresses(host, port):
    host = host.rstrip('.').lower()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal:
        if not _global(host):
            raise FetchError('禁止访问本机、内网或保留地址')
        return [host]
    if '.' not in host or host.endswith(LOCAL_SUFFIXES):
        raise FetchError('禁止访问本机或内部域名')
    try:
        host = host.encode('idna').decode('ascii')
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    except (OSError, UnicodeError) as exc:
        raise FetchError('来源 DNS 解析失败') from exc
    if addresses and all(ipaddress.ip_address(ip) in FAKE_IP_RANGE for ip in addresses):
        addresses = resolve_public_dns(host)
    # Recheck resolver output at this boundary even if the resolver is replaced.
    if not addresses or not all(_global(ip) for ip in addresses):
        raise FetchError('禁止访问本机、内网或保留地址')
    return addresses


def safe_get(url, timeout=10):
    # The deadline includes resolution, retries and all redirect hops. A single
    # blocking OS DNS lookup still follows the platform resolver's own timeout.
    deadline = time.monotonic() + timeout
    for _ in range(4):
        url = canonical_url(url)
        parts = urlsplit(url)
        addresses = public_addresses(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80))
        last_error = None
        response = None
        for address in addresses[:3]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError('来源网络请求超时')
            try:
                response = _read_once(url, address, remaining)
                break
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
        if response is None:
            raise FetchError(f'来源网络请求失败 ({type(last_error).__name__})') from last_error
        status, headers, data = response
        if status in (301, 302, 303, 307, 308):
            location = headers.get('location')
            if not location:
                raise FetchError('无效来源重定向')
            url = urljoin(url, location)
            continue
        if status != 200:
            raise FetchError(f'来源 HTTP {status}')
        return data, headers.get('content-type', '')
    raise FetchError('来源重定向次数超限')
