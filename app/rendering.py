from html import escape


def markdown(brief):
    lines = [f'# {brief["title"]}', '', brief['overview'], '']
    lines += [f'> {warning}' for warning in brief.get('warnings', [])]
    if not brief['items']:
        lines += ['本次没有符合订阅偏好的新闻。', '']
    for item in brief['items']:
        lines += [f'## {item["title"]}', '', item['summary'], '', f'与你的关注相关：{item["relevance"]}',
                  f'资料范围：{item["evidence_level"]}', '']
        for source in item['sources']:
            lines += [f'- {source["source"]} · {source["published_at"] or "发布时间未知"} · {source["url"]}']
        lines += ['']
    return '\n'.join(lines)


def html_email(brief):
    e = escape
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body style="font:16px/1.7 sans-serif;color:#192c35;max-width:720px;margin:auto;padding:24px">',
             '<p style="color:#536a74">每日 AI 简报</p>', f'<h1>{e(brief["title"])}</h1>', f'<p>{e(brief["overview"])}</p>']
    parts += [f'<p>{e(warning)}</p>' for warning in brief.get('warnings', [])]
    if not brief['items']:
        parts.append('<p>本次没有符合订阅偏好的新闻。</p>')
    for item in brief['items']:
        parts += [f'<h2>{e(item["title"])}</h2>', f'<p>{e(item["summary"])}</p>',
                  f'<p><strong>与你的关注相关：</strong>{e(item["relevance"])}</p>',
                  f'<p>{e(item["evidence_level"])}</p>', '<ul>']
        for source in item['sources']:
            parts += [f'<li><a href="{e(source["url"], quote=True)}">{e(source["source"])}</a> · {e(source["published_at"] or "发布时间未知")}</li>']
        parts.append('</ul>')
    parts += ['<p style="color:#536a74">内容由 AI 根据所列来源整理，请以原始报道为准。修改或停用订阅请打开本地应用。</p></body></html>']
    return ''.join(parts)
