# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[('plugins', 'plugins'), ('miloto', 'miloto')],
    hiddenimports=['ext.file_relay', 'ext.file_relay.forwarder', 'ext.file_relay.matcher', 'ext.file_relay.watcher', 'ext.origimg', 'ext.origimg.grabber', 'ext.origimg.vision_locate', 'ext.scheduler', 'ext.scheduler.timer', 'win.finder', 'win.typist', 'net.file_server', 'net.onebot_client', 'net.onebot_proto', 'net.portutil', 'net.weflow_client', 'web_panel', 'uiautomation', 'miloto'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Miloto',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['miloto.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Miloto',
)
