import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / 'workbench.local.json'


class Settings(BaseModel):
    account_key: str = Field(default='default', min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    account_label: str = Field(default='我的新点账号', min_length=1, max_length=100)
    mcp_url: str = 'http://127.0.0.1:9527/mcp'
    settings_path: str = str(Path(os.environ.get('APPDATA', '')) / 'EpointMsg' / 'setting_rong.json')

    @field_validator('mcp_url')
    @classmethod
    def local_url(cls, value: str) -> str:
        from urllib.parse import urlparse
        url = urlparse(value)
        if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1') or url.username or url.password:
            raise ValueError('MCP 地址必须是无凭证的本机 HTTP 地址')
        if url.path != '/mcp' or url.query or url.fragment:
            raise ValueError('MCP 地址必须以 /mcp 结尾')
        return value

    @field_validator('settings_path')
    @classmethod
    def settings_file(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute() or path.name != 'setting_rong.json':
            raise ValueError('请选择绝对路径的 setting_rong.json')
        return value


def load_settings() -> Settings:
    if CONFIG_PATH.exists():
        return Settings.model_validate_json(CONFIG_PATH.read_text(encoding='utf-8'))
    return Settings()


def save_settings(settings: Settings) -> None:
    temporary = CONFIG_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(settings.model_dump(), ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(CONFIG_PATH)
