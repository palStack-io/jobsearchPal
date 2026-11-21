"""FastAPI wiring (gatherPal, pantryPal).

    router, kit = report_router(KitConfig.from_env(...), user_ref_dependency=get_current_user_id_or_none,
                                app_root=os.path.dirname(__file__))
    app.include_router(router, prefix='/api')
    install_error_capture(app, kit)

The dependency returns the signed-in user's id (or None). Error capture is an ASGI middleware
that reports and re-raises, so the pal's own exception handlers still answer.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .client import AdminClient
from .errors import ErrorReporter
from .inbound import verify_manager
from .routes import MAX_BODY_BYTES, Kit, handle_report


def report_router(config, *, user_ref_dependency, app_root, require_user=True, app_version=None):
    """app_root is required: without it fingerprints point at library code and merge unrelated bugs.
    user_ref_dependency must return the signed-in user's id or None (raise HTTPException, nothing else)."""
    client = AdminClient(config)
    kit = Kit(client=client, errors=ErrorReporter(client, app_version=app_version, app_root=app_root))
    router = APIRouter()

    @router.post('/support/report')
    async def support_report(request: Request, user=Depends(user_ref_dependency)):
        if int(request.headers.get('content-length') or 0) > MAX_BODY_BYTES:
            return JSONResponse({'error': 'The report is too large.'}, status_code=413)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = None
        ref = str(user) if user else None
        status, payload = await run_in_threadpool(handle_report, body, ref, kit.client, require_user)
        return JSONResponse(payload, status_code=status)

    return router, kit


class _CaptureMiddleware:
    def __init__(self, app, kit):
        self.app = app
        self.kit = kit

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http':
            return await self.app(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            self.kit.errors.capture(exc, route=scope.get('path'))
            raise


def install_error_capture(app, kit) -> None:
    app.add_middleware(_CaptureMiddleware, kit=kit)


def stats_router(config, compute, *, roles=None, signed_prefix=''):
    """GET /admin/stats for adminPal's Home, answered only to adminPal's signature (403 otherwise).
    compute() returns stats_v2(...) and runs in a thread. Include with prefix '/api'. signed_prefix: what a proxy
    in front strips (adminPal signs /api/admin/stats; if the app sees /admin/stats, pass signed_prefix='/api')."""
    router = APIRouter()

    @router.get('/admin/stats')
    async def admin_stats(request: Request):
        if not verify_manager(config, 'GET', signed_prefix + request.url.path, request.headers, roles=roles):
            return JSONResponse({'error': 'forbidden'}, status_code=403)
        return JSONResponse(await run_in_threadpool(compute))

    return router
