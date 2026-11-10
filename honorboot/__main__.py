import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .core import (ALGORITHM, ConnectionLost, InstanceLock, ReviewRequired, Runner,
                   Store, Transport, default_data_dir, parcel_imei, validate_imei)

CONSENT = 'I ACCEPT POSSIBLE UNLOCK AND DATA ERASURE'


def consent():
    print('WARNING: fastboot oem unlock is NOT a read-only code test. A valid code may unlock and ERASE ALL DATA immediately.')
    print('There is no guaranteed second confirmation on this PC. The sequence is unproven and may never contain the correct code.')
    return input('Type exactly: ' + CONSENT + '\n> ').strip() == CONSENT


def parser():
    p = argparse.ArgumentParser(description='Experimental Huawei/Honor sequence runner. Windows, Python 3.10+.')
    p.add_argument('--tools', type=Path, required=True, help='Official Android platform-tools directory')
    p.add_argument('--data', type=Path, default=default_data_dir(), help='Private local state directory; never commit it')
    p.add_argument('--serial', help='Explicit target; required when more than one phone is connected')
    p.add_argument('--imei', help='Explicit sequence seed; never silently replaced by auto-detection')
    p.add_argument('command', choices=['detect', 'status', 'run', 'pause', 'resume', 'reboot', 'fastboot', 'checkbattery', 'import-legacy', 'dashboard'])
    p.add_argument('--source', type=Path, help='Legacy progress.json to import, read-only')
    return p


def discover(tools):
    t = Transport(tools, '')
    found = set()
    for tool in ('adb', 'fastboot'):
        _, out = t.run(tool, 'devices', targeted=False)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] in ('device', 'fastboot'): found.add(parts[0])
    return sorted(found)


def resolve(args):
    if not args.serial:
        found = discover(args.tools)
        if len(found) != 1: raise ReviewRequired('Select --serial explicitly; authorized connected devices: ' + repr(found))
        args.serial = found[0]
    t = Transport(args.tools, args.serial)
    if not args.imei:
        if t.mode() != 'adb': raise ReviewRequired('IMEI auto-read requires authorized Android ADB. Use --imei for a saved/manual seed.')
        _, raw = t.run('adb', 'shell', 'service', 'call', 'iphonesubinfo', '1')
        args.imei = parcel_imei(raw)
        print('Android primary IMEI:', args.imei)
        print('Confirm this against *#06#. Additional SIMs and HCU may report different identifiers; choose them explicitly with --imei.')
    validate_imei(args.imei)
    return Store(args.data, args.serial, args.imei, reconcile=False), t


def import_legacy(store, source):
    if not source: raise ValueError('--source is required')
    old = json.loads(source.read_text(encoding='utf-8-sig'))
    if old.get('pending') is not None: raise ReviewRequired('Legacy pending attempt must be resolved before import')
    if old.get('imei') and old['imei'] != store.state['imei']: raise ReviewRequired('Legacy IMEI mismatch')
    if old.get('sequence_step', store.state['step']) != store.state['step']: raise ReviewRequired('Legacy sequence mismatch')
    index = old['next_index']
    if not isinstance(index, int) or not 0 <= index <= store.state['total']: raise ValueError('Invalid legacy progress')
    if store.file.exists(): raise ReviewRequired('Destination already exists; refusing overwrite')
    store.state.update(next_index=index, status='paused', legacy_source=str(source.resolve()), imported_at=time.time())
    # A fresh battery read is required; legacy snapshots are not treated as current.
    store.save()


def control(args, store, transport):
    stop = store.path / 'STOP'
    if args.command == 'status':
        print(json.dumps(store.state, indent=2)); print('Private data:', store.path); return
    if args.command == 'pause':
        stop.write_text('User requested pause', encoding='utf-8'); print('Pause requested; current bounded command may finish first.'); return
    if args.command == 'dashboard':
        from .dashboard import open_dashboard
        open_dashboard(args, store); return
    if args.command in ('run', 'resume') and not consent():
        print('Cancelled; no phone commands sent.'); return
    with InstanceLock(args.data):
        # Re-read inside lock: another process may have updated the file while we waited.
        store = Store(args.data, args.serial, args.imei)
        if args.command == 'import-legacy': import_legacy(store, args.source); return
        if args.command in ('run', 'resume'):
            if store.state.get('pending') is not None: raise ReviewRequired('Uncertain attempt: automatic resume disabled')
            stop.unlink(missing_ok=True)
            if os.name == 'nt': ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            try: Runner(store, transport).run()
            finally:
                if os.name == 'nt': ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        else:
            if store.state.get('pending') is not None: raise ReviewRequired('Uncertain unlock result: inspect phone before rebooting')
            stop.unlink(missing_ok=True)
            runner = Runner(store, transport)
            if args.command == 'checkbattery': runner.battery()
            elif args.command == 'reboot': runner.android()
            elif args.command == 'fastboot': runner.fastboot()
            if store.state['status'] != 'charging': runner.status('paused')
            print('Command finished. Attempts remain paused.')


def main():
    args = parser().parse_args()
    for tool in ('adb.exe', 'fastboot.exe'):
        if not (args.tools / tool).is_file(): raise SystemExit('Missing official tool: ' + tool)
    try:
        if args.command == 'detect':
            print('Authorized devices:', discover(args.tools)); return
        store, transport = resolve(args)
        control(args, store, transport)
    except (ConnectionLost, ReviewRequired, ValueError, OSError) as exc:
        print('STOPPED: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__': main()
