import asyncio
import json
import os
import re
import shlex
import time
from pathlib import Path, PureWindowsPath
from typing import Annotated

from pydantic import Field, StringConstraints, ValidationError

from .news import NewsSource, SOURCES
from .schemas import StrictModel
from .storage import dumps, redact

Text = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class PathArgs(StrictModel):
    path: Text


class SearchArgs(StrictModel):
    keyword: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    dir: Text


class WriteArgs(PathArgs):
    content: str = Field(max_length=24000)


class BashArgs(StrictModel):
    command: Text


class FetchArgs(StrictModel):
    source_id: str = Field(pattern=r'^(huggingface|hackernews)$')


class ArticleArgs(StrictModel):
    item_id: Text


TOOL_TYPES = {
    'list_dir': (PathArgs, '列出当前运行目录中的文件，根目录用 .'),
    'read_file': (PathArgs, '读取运行材料文本，单次最多 16000 字符'),
    'search_content': (SearchArgs, '在当前运行的指定目录中按关键词搜索文本'),
    'write_file': (WriteArgs, '写入 drafts/ 下的草稿或笔记，不能修改新闻证据'),
    'bash': (BashArgs, '受限 Bash：pwd、ls、cat、head、tail、wc；仅单命令和当前运行路径'),
    'fetch_news': (FetchArgs, '采集指定来源，返回真实新闻索引并写入 evidence/；可选 huggingface、hackernews'),
    'read_article': (ArticleArgs, '按已采集的新闻编号读取原文，失败时如实返回错误'),
}


def definitions():
    return [{'type': 'function', 'function': {'name': name, 'description': desc,
            'parameters': model.model_json_schema()}} for name, (model, desc) in TOOL_TYPES.items()]


class ToolError(Exception):
    pass


class Toolset:
    def __init__(self, root, settings, started_at, event, source=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.started_at = started_at
        self.event = event
        self.source = source or NewsSource()
        self.items = {}
        self.outcomes = {}
        self.articles = {}
        self.secrets = (settings.api_key, settings.smtp_password, settings.smtp_username, settings.smtp_from)

    def path(self, value, *, writing=False):
        if not value or len(value) > 500 or '\x00' in value or '\\' in value or ':' in value:
            raise ToolError('非法路径')
        if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
            raise ToolError('仅支持运行目录内的相对路径')
        pieces = Path(value).parts
        for piece in pieces:
            if piece == '..' or piece.endswith((' ', '.')) and piece != '.' or piece.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(10)], *[f'LPT{i}' for i in range(10)]}:
                raise ToolError('路径越界或保留名称')
        target = self.root / value
        for part in (target, *target.parents):
            if part == self.root:
                break
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise ToolError('禁止符号链接或目录联接')
        resolved = target.resolve()
        if not resolved.is_relative_to(self.root):
            raise ToolError('路径越界')
        if writing and (not pieces or pieces[0] != 'drafts' or len(pieces) < 2):
            raise ToolError('仅允许写入 drafts/ 下的文件')
        return resolved

    async def call(self, name, arguments):
        started = time.monotonic()
        try:
            if name not in TOOL_TYPES:
                raise ToolError('未知工具')
            if isinstance(arguments, str):
                if len(arguments) > 30000:
                    raise ToolError('工具参数过大')
                arguments = json.loads(arguments)
            args = TOOL_TYPES[name][0].model_validate(arguments)
            result = await getattr(self, name)(**args.model_dump())
            result = {'ok': True, 'data': result}
        except asyncio.CancelledError:
            self.event('tool', {'name': name, 'arguments': '[cancelled]', 'ok': False, 'error': '执行被取消', 'elapsed_ms': round((time.monotonic()-started)*1000)})
            raise
        except Exception as exc:
            if isinstance(exc, ValidationError):
                message = '工具参数不符合定义'
            elif isinstance(exc, (ToolError, ValueError, OSError)):
                message = str(exc)[:500]
            else:
                message = f'工具执行失败 ({type(exc).__name__})'
            result = {'ok': False, 'error': redact(message, self.secrets)}
        # Sanitize data before it is shown to either the model or the browser.
        result = json.loads(redact(dumps(result), self.secrets))
        self.event('tool', {'name': name, 'arguments': redact(arguments, self.secrets)[:1200],
                           'ok': result['ok'], 'result': dumps(result)[:1800],
                           'elapsed_ms': round((time.monotonic()-started)*1000)})
        return result

    async def list_dir(self, path):
        folder = self.path(path)
        if not folder.is_dir():
            raise ToolError('目录不存在')
        entries = sorted(folder.iterdir(), key=lambda p: p.name)[:100]
        return [{'name': p.name, 'type': 'link' if p.is_symlink() else 'directory' if p.is_dir() else 'file'} for p in entries]

    async def read_file(self, path):
        file = self.path(path)
        if not file.is_file() or file.stat().st_size > 256000:
            raise ToolError('文件不存在或超过大小限制')
        text = file.read_text(encoding='utf-8')
        return {'text': text[:16000], 'truncated': len(text) > 16000}

    async def search_content(self, keyword, dir):
        folder = self.path(dir)
        if not folder.is_dir():
            raise ToolError('目录不存在')
        matches = []
        visited = 0
        for base, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(base)/d).is_symlink() and not (hasattr(Path(base)/d, 'is_junction') and (Path(base)/d).is_junction())]
            for name in files:
                visited += 1
                if visited > 100:
                    return {'matches': matches, 'truncated': True}
                candidate = Path(base)/name
                if candidate.is_symlink():
                    continue
                file = self.path(candidate.relative_to(self.root).as_posix())
                if file.stat().st_size > 256000:
                    continue
                for number, line in enumerate(file.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
                    if keyword.casefold() in line.casefold():
                        matches.append({'path': file.relative_to(self.root).as_posix(), 'line': number, 'text': line[:500]})
                        if len(matches) >= 30:
                            return {'matches': matches, 'truncated': True}
        return {'matches': matches, 'truncated': False}

    async def write_file(self, path, content):
        file = self.path(path, writing=True)
        if len(list(self.root.rglob('*'))) >= 150 and not file.exists():
            raise ToolError('本次运行文件数量达到上限')
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding='utf-8')
        return {'path': file.relative_to(self.root).as_posix(), 'characters': len(content)}

    async def bash(self, command):
        if not self.settings.bash_path or not Path(self.settings.bash_path).is_file():
            raise ToolError('未找到 Bash，请配置 BASH_PATH')
        if any(ch in command for ch in ';|&<>$`\n\r\\*?{}[]!~'):
            raise ToolError('禁止组合命令、重定向、替换和通配符')
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ToolError('命令引号无效') from exc
        if not argv or argv[0] not in {'pwd', 'ls', 'cat', 'head', 'tail', 'wc'}:
            raise ToolError('命令不在允许列表')
        cmd, rest = argv[0], argv[1:]
        flags = {'pwd': set(), 'ls': {'-l', '-a', '-la'}, 'cat': set(), 'head': set(), 'tail': set(), 'wc': {'-l', '-w', '-c'}}[cmd]
        safe_args, paths = [], []
        while rest:
            value, *rest = rest
            if value == '-n' and cmd in {'head', 'tail'}:
                if not rest or not rest[0].isdigit() or not 1 <= int(rest[0]) <= 100:
                    raise ToolError('-n 只允许 1 到 100')
                safe_args += [value, rest.pop(0)]
            elif value.startswith('-'):
                if value not in flags:
                    raise ToolError('不允许此命令参数')
                safe_args.append(value)
            else:
                path = self.path(value)
                if not path.exists():
                    raise ToolError('路径不存在')
                if cmd != 'ls' and (not path.is_file() or path.stat().st_size > 256000):
                    raise ToolError('仅允许读取有大小限制的普通文件')
                paths.append('./' + path.relative_to(self.root).as_posix())
        if cmd == 'pwd' and argv != ['pwd'] or len(paths) > 8:
            raise ToolError('参数数量不支持')
        if cmd in {'cat', 'head', 'tail', 'wc'} and not paths:
            raise ToolError('必须提供文件路径，不读取标准输入')
        safe_command = 'exec ' + shlex.join([cmd, *safe_args, *paths])
        # No inherited HOME, BASH_ENV, API credentials or SMTP credentials.
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
        if os.name == 'nt':
            env['SYSTEMROOT'] = os.environ.get('SYSTEMROOT', 'C:\\Windows')
        process = await asyncio.create_subprocess_exec(self.settings.bash_path, '--noprofile', '--norc', '-c', safe_command,
                                                       cwd=self.root, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            async def limited_read():
                chunks = bytearray()
                while len(chunks) <= 16000:
                    chunk = await process.stdout.read(min(4096, 16001-len(chunks)))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                return bytes(chunks)
            output = await asyncio.wait_for(limited_read(), timeout=5)
            if len(output) > 16000:
                process.kill()
                await process.wait()
                return {'stdout': output[:16000].decode('utf-8', errors='replace'), 'truncated': True, 'exit_code': None}
            await asyncio.wait_for(process.wait(), timeout=2)
            return {'stdout': output.decode('utf-8', errors='replace'), 'truncated': False, 'exit_code': process.returncode}
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def fetch_news(self, source_id):
        if source_id in self.outcomes:
            return {**self.outcomes[source_id], 'cached': True}
        try:
            items, warning = await self.source.fetch(source_id, self.started_at)
            indices = []
            for item in items:
                self.items.setdefault(item.id, item)
                indices.append(item.model_dump(mode='json', exclude={'excerpt'}))
            path = self.root / 'evidence' / f'{source_id}.json'
            path.parent.mkdir(exist_ok=True)
            path.write_text(dumps([i.model_dump(mode='json') for i in items]), encoding='utf-8')
            outcome = {'status': 'success' if items else 'empty', 'items': indices,
                       'path': path.relative_to(self.root).as_posix(), 'warning': warning}
        except Exception as exc:
            outcome = {'status': 'failure', 'items': [], 'error': f'来源读取失败 ({type(exc).__name__})'}
        self.outcomes[source_id] = outcome
        return outcome

    async def read_article(self, item_id):
        if item_id not in self.items:
            raise ToolError('新闻编号不属于本次采集')
        if item_id not in self.articles:
            self.articles[item_id] = await self.source.article(self.items[item_id])
            path = self.root / 'evidence' / f'{item_id}.txt'
            path.parent.mkdir(exist_ok=True)
            path.write_text(self.articles[item_id], encoding='utf-8')
        return {'item_id': item_id, 'text': self.articles[item_id], 'path': f'evidence/{item_id}.txt'}
