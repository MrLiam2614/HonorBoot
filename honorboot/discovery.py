"""Bounded, read-only device discovery, called outside the UI thread."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import sys

from .core import Transport, parcel_imei


def find_tools(preferred='', base=None):
    base = Path(base or (Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path.cwd()))
    candidates = [Path(preferred)] if preferred else []
    candidates += [base, base / 'platform-tools', base.parent / 'platform-tools']
    for env in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
        if os.environ.get(env): candidates.append(Path(os.environ[env]) / 'platform-tools')
    candidates.append(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Android/Sdk/platform-tools')
    adb = shutil.which('adb.exe')
    if adb: candidates.append(Path(adb).parent)
    # Bounded adjacent-folder search; never recursively scan a drive.
    for parent in (base, *list(base.parents)[:4]):
        candidates.extend(list(parent.glob('*/platform-tools'))[:50])
    for path in candidates:
        if all((path / name).is_file() for name in ('adb.exe', 'fastboot.exe')):
            return path.resolve()
    return None


def detect_configuration(current, data):
    tools = find_tools(current.get('tools', ''))
    if not tools:
        return {'message': 'Platform-tools not found. Extract official Android platform-tools next to this app, or use Browse.'}
    result = {'tools': str(tools)}
    transport = Transport(tools, '')
    def devices(tool):
        _, output = transport.run(tool, 'devices', targeted=False, timeout=8)
        return {p[0]: p[1] for line in output.splitlines() if len(p := line.split()) == 2 and p[1] in ('device', 'fastboot')}
    found = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(devices, tool) for tool in ('adb', 'fastboot')]
        for future in futures:
            try: found.update(future.result())
            except OSError: pass
            except Exception: pass
    serial = current.get('serial', '')
    if serial not in found:
        if len(found) != 1:
            result['message'] = 'No authorized device found. Connect USB and authorize debugging, then Detect.' if not found else 'Multiple devices detected. Enter the intended serial: ' + ', '.join(found)
            return result
        serial = next(iter(found))
    result['serial'] = serial
    result['imei'] = ''
    seeds = set()
    for path in Path(data).glob('devices/*/progress.json'):
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            if saved['serial'] == serial: seeds.add(saved['imei'])
        except (OSError, ValueError, KeyError): continue
    result['imeis'] = sorted(seeds)
    if current.get('serial') == serial and current.get('imei') in seeds:
        result['imei'] = current['imei']
    elif len(seeds) == 1:
        result['imei'] = next(iter(seeds))
    elif len(seeds) > 1:
        result['message'] = 'Device found. Multiple saved IMEI sequences: select one from the IMEI list. Progress stays separate.'
        return result
    elif found[serial] == 'device':
        try:
            _, raw = Transport(tools, serial).run('adb', 'shell', 'service', 'call', 'iphonesubinfo', '1', timeout=8)
            result['imei'] = parcel_imei(raw)
        except Exception:
            result['message'] = 'Device found. Android did not expose an IMEI; enter it from *#06#.'
            return result
    else:
        result['message'] = 'Fastboot device found. No saved IMEI: enter it manually; detection will not reboot the phone.'
        return result
    result['message'] = 'Device and IMEI selected. Review the values, then open the dashboard. No attempts started.'
    return result
