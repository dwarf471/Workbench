"""Platform-specific protection; legacy Windows DPAPI ciphertext stays readable."""
import ctypes
from ctypes import wintypes
import os
import sys

class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _windows_protect(value: bytes, decrypt=False) -> bytes:
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


def protect(value: bytes, decrypt=False) -> bytes:
    if sys.platform == 'win32':
        return _windows_protect(value, decrypt)
    if sys.platform == 'darwin':
        from keyring.backends.macOS import Keyring
        from cryptography.fernet import Fernet
        keychain = Keyring()
        service, account = 'PersonalWorkbench', 'model-encryption-v1'
        key = keychain.get_password(service, account)
        if key is None:
            if decrypt:
                raise RuntimeError('Credential unavailable; enter the API key again')
            key = Fernet.generate_key().decode('ascii')
            keychain.set_password(service, account, key)
        cipher = Fernet(key.encode('ascii'))
        prefix = b'keychain-v1:'
        if decrypt:
            if not value.startswith(prefix):
                raise RuntimeError('Credential belongs to another platform; enter the API key again')
            return cipher.decrypt(value[len(prefix):])
        return prefix + cipher.encrypt(value)
    raise RuntimeError('Credential protection is supported on Windows and macOS')
