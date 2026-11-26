# PyInstaller spec: builds ClipSaver.app on macOS (run in GitHub Actions).
# -*- mode: python -*-

a = Analysis(
    ['main.py'],
    datas=[('clipsaver/ui.html', 'clipsaver'), ('clipsaver/logo.png', 'clipsaver')],
    excludes=['tkinter', 'unittest', 'pydoc', 'test'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='ClipSaver',
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name='ClipSaver')
app = BUNDLE(
    coll,
    name='ClipSaver.app',
    icon='art/ClipSaver.icns',   # made by tools/build_icon.py from the user's logo design
    bundle_identifier='io.github.skot-chen.clipsaver',
    info_plist={
        'CFBundleShortVersionString': __import__('os').environ.get('APP_VERSION', '0.1.0'),
        'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'ClipSaver',
    },
)
