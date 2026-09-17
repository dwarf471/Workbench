import asyncio
import hashlib
import json
import re
from base64 import b64decode
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .config import Settings


class ToolFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def call_tool(settings: Settings, name: str, arguments: dict) -> dict:
    if name not in {'get_conversation_list', 'search_contacts', 'get_history_messages', 'get_profile'}:
        raise ToolFailure('INVALID_TOOL')
    for attempt in range(2):
        try:
            token = read_token(settings)
        except Exception:
            raise ToolFailure('TOKEN_READ_FAILED') from None
        try:
            async with asyncio.timeout(45):
                async with streamablehttp_client(settings.mcp_url, headers={'Authorization': f'Bearer {token}'}) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        if initialized.serverInfo.name != 'rongyun-im-mcp':
                            raise ToolFailure('UNEXPECTED_SERVER')
                        response = await session.call_tool(name, arguments)
                        if response.isError:
                            raise ToolFailure('TOOL_ERROR')
                        if response.structuredContent is not None:
                            payload = response.structuredContent
                        else:
                            blocks = [part.text for part in response.content if part.type == 'text']
                            if len(blocks) != 1:
                                raise ToolFailure('INVALID_RESULT')
                            payload = json.loads(blocks[0])
                        if not isinstance(payload, dict):
                            raise ToolFailure('INVALID_RESULT')
                        return payload
        except Exception as error:
            if contains_401(error):
                if attempt == 0:
                    continue
                raise ToolFailure('UNAUTHORIZED') from None
            if isinstance(error, ToolFailure):
                raise
            raise ToolFailure('CONNECTION_FAILED') from None
        finally:
            token = None
    raise ToolFailure('UNAUTHORIZED')


def decode_token(stored: str) -> str:
    if not isinstance(stored, str) or not stored:
        raise ValueError('missing token')
    if stored.startswith('v1:'):
        parts = stored.split(':')
        if len(parts) != 3:
            raise ValueError('invalid envelope')
        iv, ciphertext = (b64decode(part, validate=True) for part in parts[1:])
        decryptor = Cipher(algorithms.AES(hashlib.sha256(b'msgmcp').digest()), modes.CBC(iv)).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        token = (unpadder.update(padded) + unpadder.finalize()).decode('utf-8')
    else:
        token = stored
    if not re.fullmatch(r'[0-9a-fA-F]+', token):
        raise ValueError('unsupported token format')
    return token


def read_token(settings: Settings) -> str:
    value = json.loads(Path(settings.settings_path).read_text(encoding='utf-8-sig'))
    return decode_token(value.get('mcpToken'))


def contains_401(error: BaseException) -> bool:
    if getattr(getattr(error, 'response', None), 'status_code', None) == 401:
        return True
    return any(contains_401(child) for child in getattr(error, 'exceptions', []))


async def check_connection(settings: Settings) -> dict:
    for attempt in range(2):
        try:
            token = read_token(settings)
        except Exception:
            return {'connected': False, 'code': 'TOKEN_READ_FAILED', 'message': '无法读取或解密令牌，请检查新点配置文件。'}
        try:
            async with asyncio.timeout(15):
                async with streamablehttp_client(settings.mcp_url, headers={'Authorization': f'Bearer {token}'}) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        if initialized.serverInfo.name != 'rongyun-im-mcp':
                            return {'connected': False, 'code': 'UNEXPECTED_SERVER', 'message': '当前地址不是预期的新点 MCP 服务。'}
                        tools = await session.list_tools()
                        names = [tool.name for tool in tools.tools]
                        required = {'get_conversation_list', 'get_history_messages', 'search_contacts', 'get_profile'}
                        if not required.issubset(names):
                            return {'connected': False, 'code': 'MISSING_TOOLS', 'message': '服务缺少预期的新点工具。'}
                        return {'connected': True, 'code': 'OK', 'message': '连接正常', 'server': initialized.serverInfo.name,
                                'version': initialized.serverInfo.version, 'protocol': initialized.protocolVersion, 'tools': names}
        except Exception as error:
            if contains_401(error):
                if attempt == 0:
                    continue
                return {'connected': False, 'code': 'UNAUTHORIZED', 'message': '令牌未获授权，请确认客户端与配置文件一致。'}
            return {'connected': False, 'code': 'CONNECTION_FAILED', 'message': '连接或协议检查失败，请确认新点已运行及 MCP 地址正确。'}
        finally:
            token = None
    return {'connected': False, 'code': 'UNAUTHORIZED', 'message': '认证失败'}
