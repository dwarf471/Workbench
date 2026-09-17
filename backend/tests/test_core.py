import hashlib
from base64 import b64encode

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pydantic import ValidationError

from app.config import Settings
from app.mcp_client import decode_token


def test_encrypted_token():
    token = 'ab' * 32
    padder = padding.PKCS7(128).padder()
    padded = padder.update(token.encode()) + padder.finalize()
    iv = bytes(range(16))
    encryptor = Cipher(algorithms.AES(hashlib.sha256(b'msgmcp').digest()), modes.CBC(iv)).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    envelope = f'v1:{b64encode(iv).decode()}:{b64encode(encrypted).decode()}'
    assert decode_token(envelope) == token


@pytest.mark.parametrize('value', ['', None, 'v1:a:b', 'v2:a:b', 'not-a-token'])
def test_bad_token(value):
    with pytest.raises(Exception):
        decode_token(value)


def test_plaintext():
    assert decode_token('ab' * 32) == 'ab' * 32


@pytest.mark.parametrize('url', ['https://example.com/mcp', 'http://127.0.0.1/other', 'http://user:pass@localhost/mcp'])
def test_bad_url(url):
    with pytest.raises(ValidationError):
        Settings(mcp_url=url)
