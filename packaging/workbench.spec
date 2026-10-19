from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
data = collect_data_files('webview') + [
    (str(root / 'frontend' / 'dist'), 'frontend/dist'),
    (str(root / 'backend' / 'alembic.ini'), 'backend'),
] + [(str(path), str(Path('backend/migrations') / path.relative_to(root / 'backend/migrations').parent))
     for path in (root / 'backend/migrations').rglob('*.py')]
a = Analysis(
    [str(root / 'backend' / 'desktop.py')],
    pathex=[str(root / 'backend')],
    binaries=[], datas=data,
    hiddenimports=collect_submodules('app') + [
        'webview.platforms.winforms', 'webview.platforms.edgechromium',
        'uvicorn.logging', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
        'uvicorn.lifespan.on', 'sqlalchemy.dialects.sqlite',
    ],
    hookspath=[], runtime_hooks=[],
    excludes=['pytest', 'IPython', 'matplotlib', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cefpython3'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PersonalWorkbench',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PersonalWorkbench')
