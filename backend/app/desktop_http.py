"""Authentication for the private desktop HTTP listener (not browser mode)."""
import secrets
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

COOKIE = 'workbench_desktop'


async def guard(request: Request, call_next):
    token = request.app.state.desktop_token
    origin = request.app.state.desktop_origin
    if request.headers.get('host') != urlsplit(origin).netloc:
        return JSONResponse({'detail': '无效的本机地址'}, status_code=400)
    if request.headers.get('origin', origin) != origin:
        return JSONResponse({'detail': '不允许跨站访问'}, status_code=403)
    if request.url.path == '/_desktop/bootstrap':
        supplied = request.query_params.get('token', '')
        if request.method != 'GET' or not secrets.compare_digest(supplied.encode(), token.encode()):
            return JSONResponse({'detail': '无效会话'}, status_code=401)
        response = RedirectResponse('/', status_code=303)
        response.set_cookie(COOKIE, token, httponly=True, samesite='strict')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response
    supplied = request.cookies.get(COOKIE, '')
    authorization = request.headers.get('authorization', '')
    if authorization.startswith('Bearer '):
        supplied = authorization[7:]
    if not secrets.compare_digest(supplied.encode(), token.encode()):
        return JSONResponse({'detail': '桌面会话已失效，请重新启动工作台'}, status_code=401)
    if request.url.path == '/_desktop/activate' and request.method == 'POST':
        request.app.state.desktop_activate()
        return JSONResponse({'ok': True})
    return await call_next(request)
