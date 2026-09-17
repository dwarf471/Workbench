import asyncio
import base64
import ctypes
from ctypes import wintypes
import json
import os
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr, field_validator

from .config import ROOT
from .prompts import DEFAULT_EXTRACTION_PROMPT

PATH = ROOT / 'data' / 'model.local.json'
router = APIRouter(prefix='/api/model')
lock = asyncio.Lock()


class ModelSettings(BaseModel):
    base_url: str = Field(default='', max_length=2000)
    model: str = Field(default='', max_length=200)
    timeout: int = Field(default=120, ge=5, le=300)
    extraction_prompt: str = Field(default=DEFAULT_EXTRACTION_PROMPT, min_length=1, max_length=8000)

    @field_validator('extraction_prompt')
    @classmethod
    def prompt_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Prompt cannot be empty')
        return value

    @field_validator('base_url')
    @classmethod
    def address(cls, value):
        value = value.strip().rstrip('/')
        if not value:
            return value
        url = urlsplit(value)
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Invalid model address')
        _ = url.port
        if url.scheme not in ('http', 'https'):
            raise ValueError('HTTP or HTTPS required')
        if any(char.isspace() for char in value):
            raise ValueError('Invalid model address')
        return value

    @field_validator('model')
    @classmethod
    def model_name(cls, value):
        return value.strip()


class Update(ModelSettings):
    api_key: SecretStr | None = None
    clear_key: bool = False


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(value: bytes, decrypt=False) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('Windows credential protection required')
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # UI_FORBIDDEN prevents a credential operation from opening an interactive dialog.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError('Credential operation failed')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        if decrypt:
            ctypes.memset(output.data, 0, output.size)
        kernel.LocalFree(output.data)
        ctypes.memset(buffer, 0, len(value))


def load():
    if not PATH.exists():
        return ModelSettings(), ''
    value = json.loads(PATH.read_text(encoding='utf-8'))
    return ModelSettings.model_validate(value), value.get('encrypted_key', '')


def public(settings, encrypted):
    return {**settings.model_dump(), 'key_configured': bool(encrypted), 'protocol': 'openai-chat-completions'}


@router.get('/settings')
def settings():
    try:
        value, encrypted = load()
        return public(value, encrypted)
    except Exception:
        raise HTTPException(500, '模型配置读取失败') from None


@router.get('/prompt-default')
def prompt_default():
    return {'extraction_prompt': DEFAULT_EXTRACTION_PROMPT}


@router.put('/settings')
async def save(request: Request):
    async with lock:
        try:
            value = Update.model_validate(await request.json())
        except Exception:
            # Never echo validation inputs: they may contain the API key.
            raise HTTPException(422, '配置无效：检查 HTTP/HTTPS 地址、5–300 秒超时及非空且不超过 8000 字符的提示词') from None
        try:
            previous, encrypted = load()
            key = value.api_key.get_secret_value() if value.api_key else ''
            if value.clear_key and key:
                raise HTTPException(422, '不能同时填写和清除 API Key')
            if value.clear_key:
                encrypted = ''
            elif key:
                if len(key) > 4096 or any(c.isspace() for c in key):
                    raise HTTPException(422, 'API Key 格式无效')
                encrypted = base64.b64encode(protect(key.encode())).decode('ascii')
            saved = ModelSettings(**value.model_dump(exclude={'api_key', 'clear_key'}))
            if 'extraction_prompt' not in value.model_fields_set:
                saved.extraction_prompt = previous.extraction_prompt
            PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = PATH.with_suffix('.tmp')
            temporary.write_text(json.dumps({**saved.model_dump(), 'encrypted_key': encrypted}, ensure_ascii=False), encoding='utf-8')
            temporary.replace(PATH)
            return public(saved, encrypted)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(500, '模型配置保存失败，请检查本地文件和 Windows 凭证保护') from None


@router.post('/check')
async def check():
    if lock.locked():
        raise HTTPException(409, '模型配置操作正在进行')
    async with lock:
        try:
            value, encrypted = load()
            if not value.base_url or not value.model:
                raise HTTPException(422, '请先保存接口地址和模型名称')
            key = protect(base64.b64decode(encrypted), decrypt=True).decode() if encrypted else ''
            headers = {'Authorization': f'Bearer {key}'} if key else {}
            async with httpx.AsyncClient(timeout=value.timeout, follow_redirects=False, trust_env=False) as client:
                response = await client.post(value.base_url + '/chat/completions', headers=headers,
                                             json={'model': value.model, 'messages': [{'role': 'user', 'content': 'Connection test. Reply OK.'}],
                                                   'max_tokens': 32, 'stream': False})
            if response.status_code in (401, 403):
                return {'connected': False, 'message': '模型认证失败，请检查 API Key 和权限'}
            if 300 <= response.status_code < 400:
                return {'connected': False, 'message': '接口发生重定向，已阻止；请配置最终接口地址'}
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content']
            if not isinstance(content, str) or not content.strip():
                raise ValueError('Invalid completion')
            return {'connected': True, 'message': '模型连接正常，固定文本测试通过；未发送聊天记录'}
        except HTTPException:
            raise
        except httpx.TimeoutException:
            return {'connected': False, 'message': '模型请求超时'}
        except Exception:
            return {'connected': False, 'message': '模型连接失败，请检查地址、模型名称、响应协议及本地凭证'}
