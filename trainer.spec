# Build with: python -m PyInstaller --noconfirm --clean trainer.spec
# Qt on Windows uses the Windows ICU shim. A Poppler/Conda DLL with the same
# basename must not be bundled, because it exposes version-suffixed symbols.
a = Analysis(
    ['app.py'], pathex=[], binaries=[], datas=[('assets/liberl-toolkit.ico','assets')],
    hiddenimports=['zstandard.backend_c'], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=[], noarchive=False, optimize=0,
)
a.binaries = [entry for entry in a.binaries
              if entry[0].replace('\\', '/').split('/')[-1].lower()
              not in {'icuuc.dll', 'icuin.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='LiberlToolkit',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False, icon='assets/liberl-toolkit.ico')
