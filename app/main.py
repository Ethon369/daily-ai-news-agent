import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from .config import ROOT, Settings
from .schemas import SubscriptionCreate, SubscriptionPatch
from .service import BEIJING, Service


def error(code, message, status=400):
    return JSONResponse({'error': {'code': code, 'message': message}}, status_code=status)


def create_app(settings=None, client=None, source=None, mailer=None):
    settings = settings or Settings.from_env()
    service = Service(settings, client, source, mailer)

    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(title='每日 AI 新闻助手', version='0.1.0', lifespan=lifespan)
    app.state.service = service

    @app.middleware('http')
    async def local_guard(request, call_next):
        host = request.url.hostname
        if host not in ('localhost', '127.0.0.1', '::1'):
            return error('LOCAL_ONLY', '此应用仅支持本机访问', 403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin != f'{request.url.scheme}://{request.url.netloc}':
                return error('ORIGIN_REJECTED', '拒绝跨站请求', 403)
            if request.headers.get('x-requested-with') != 'DailyBrief':
                return error('REQUEST_HEADER_REQUIRED', '缺少 X-Requested-With: DailyBrief', 403)
            try:
                if int(request.headers.get('content-length', '0')) > 64000:
                    return error('BODY_TOO_LARGE', '请求体过大', 413)
            except ValueError:
                return error('INVALID_LENGTH', '无效请求长度')
        try:
            response = await call_next(request)
        except Exception as exc:
            logging.getLogger('dailybrief').error('Request failed: %s', type(exc).__name__)
            return error('INTERNAL_ERROR', '服务暂时无法处理请求，请检查本地服务状态', 500)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        if request.url.path == '/' or request.url.path.startswith('/static/'):
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error('VALIDATION_ERROR', '输入不符合要求，请检查邮箱、时间、话题长度及必填项', 422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error('NOT_FOUND' if exc.status_code == 404 else 'HTTP_ERROR', str(exc.detail), exc.status_code)

    @app.get('/')
    def index():
        return FileResponse(ROOT/'static/index.html')

    @app.get('/api/health')
    def health():
        service.store.one('SELECT 1')
        return {'data': {'status': 'ok', 'model': settings.model, 'model_configured': bool(settings.api_key),
                         'smtp_configured': bool(settings.smtp_ready), 'scheduler_enabled': settings.scheduler_enabled,
                         'bash_configured': bool(settings.bash_path)}}

    @app.get('/api/subscriptions')
    def list_subscriptions():
        return {'data': service.store.subscriptions()}

    @app.post('/api/subscriptions', status_code=201)
    def create_subscription(body: SubscriptionCreate):
        return {'data': service.store.save_subscription(body.model_dump())}

    @app.patch('/api/subscriptions/{sub_id}')
    def patch_subscription(sub_id: str, body: SubscriptionPatch):
        current = service.store.subscription(sub_id)
        if not current:
            return error('NOT_FOUND', '订阅不存在', 404)
        merged = {key: current[key] for key in SubscriptionCreate.model_fields}
        merged.update(body.model_dump(exclude_unset=True))
        try:
            validated = SubscriptionCreate.model_validate(merged)
        except ValidationError:
            return error('VALIDATION_ERROR', '请检查邮箱、时间和关注内容', 422)
        return {'data': service.store.save_subscription(validated.model_dump(), sub_id)}

    @app.post('/api/subscriptions/{sub_id}/runs', status_code=202)
    async def create_run(sub_id: str):
        sub = service.store.subscription(sub_id)
        if not sub:
            return error('NOT_FOUND', '订阅不存在', 404)
        run_id, created = service.store.create_run(sub, 'preview', datetime.now(BEIJING).date().isoformat())
        service.wakeup.set()
        return {'data': {'id': run_id, 'created': created}}

    @app.get('/api/runs/{run_id}')
    def get_run(run_id: str):
        run = service.store.one('SELECT * FROM runs WHERE id=?', (run_id,))
        if not run:
            return error('NOT_FOUND', '任务不存在', 404)
        run.pop('snapshot')
        run['events'] = [{**r, 'payload': json.loads(r['payload'])} for r in service.store.all('SELECT * FROM events WHERE run_id=? ORDER BY id', (run_id,))]
        brief = service.store.one('SELECT id FROM briefings WHERE run_id=?', (run_id,))
        run['briefing_id'] = brief['id'] if brief else None
        return {'data': run}

    @app.get('/api/subscriptions/{sub_id}/briefings')
    def history(sub_id: str):
        if not service.store.subscription(sub_id):
            return error('NOT_FOUND', '订阅不存在', 404)
        rows = service.store.all('''SELECT b.id,b.run_id,b.created_at,b.content,r.kind,r.local_date,d.status AS delivery_status
            FROM briefings b JOIN runs r ON r.id=b.run_id LEFT JOIN deliveries d ON d.briefing_id=b.id
            WHERE b.subscription_id=? ORDER BY b.created_at DESC LIMIT 100''', (sub_id,))
        return {'data': [{k: v for k, v in r.items() if k != 'content'} | {'title': json.loads(r['content'])['title']} for r in rows]}

    # This small extra endpoint restores queued/failed tasks after a page refresh.
    @app.get('/api/subscriptions/{sub_id}/runs')
    def run_history(sub_id: str):
        rows = service.store.all('SELECT id,status,kind,created_at,error FROM runs WHERE subscription_id=? ORDER BY created_at DESC LIMIT 20', (sub_id,))
        return {'data': rows}

    @app.get('/api/briefings/{brief_id}')
    def briefing(brief_id: str):
        row = service.store.one('SELECT * FROM briefings WHERE id=?', (brief_id,))
        if not row:
            return error('NOT_FOUND', '简报不存在', 404)
        row['content'] = json.loads(row['content'])
        row['delivery'] = service.store.one('SELECT * FROM deliveries WHERE briefing_id=?', (brief_id,))
        return {'data': row}

    @app.post('/api/briefings/{brief_id}/deliveries', status_code=202)
    async def deliver(brief_id: str):
        if not service.store.one('SELECT id FROM briefings WHERE id=?', (brief_id,)):
            return error('NOT_FOUND', '简报不存在', 404)
        delivery, created = service.store.claim_delivery(brief_id)
        if created:
            service.spawn(service.deliver(delivery))
        return {'data': delivery}

    app.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')
    return app


app = create_app()
