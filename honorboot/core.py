from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from dataclasses import dataclass

BASE = 10**15
MAX_CODE = 10**16 - 1
ALGORITHM = 'legacy-luhn-sqrt-v1'


class ConnectionLost(Exception): pass
class ReviewRequired(Exception): pass


def checksum(value: str) -> int:
    digits = list(map(int, value))
    return (sum(digits[-1::-2]) + sum(sum(map(int, str(d * 2))) for d in digits[-2::-2])) % 10


def validate_imei(value: str) -> str:
    if not re.fullmatch(r'\d{15}', value) or checksum(value) != 0:
        raise ValueError('IMEI must contain 15 digits with a valid Luhn checksum')
    return value


def sequence(value: str) -> tuple[int, int]:
    validate_imei(value)
    # Deliberately matches the referenced experimental project's floating-point arithmetic.
    step = int(checksum(value) + math.sqrt(int(value)) * 1024)
    return step, (MAX_CODE - BASE) // step + 1


def default_data_dir() -> Path:
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'HonorBoot'


def atomic_json(path: Path, data: dict):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    for retry in range(20):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if retry == 19: raise
            time.sleep(.1)


class Store:
    def __init__(self, root: Path, serial: str, imei: str, reconcile=True):
        step, total = sequence(imei)
        key = hashlib.sha256(f'{serial}|{imei}|{ALGORITHM}'.encode()).hexdigest()[:24]
        self.path = root / 'devices' / key
        self.path.mkdir(parents=True, exist_ok=True)
        self.file = self.path / 'progress.json'
        self.state = json.loads(self.file.read_text(encoding='utf-8-sig')) if self.file.exists() else {
            'serial': serial, 'imei': imei, 'algorithm': ALGORITHM, 'step': step,
            'total': total, 'next_index': 0, 'pending': None, 'status': 'paused',
            'battery_checked_at': 0, 'charging_until': None,
        }
        if any(self.state[k] != v for k, v in dict(serial=serial, imei=imei, algorithm=ALGORITHM, step=step, total=total).items()):
            raise ReviewRequired('Stored identity or sequence mismatch')
        if reconcile: self.reconcile()

    def reconcile(self):
        pending = self.state.get('pending')
        if pending is None: return
        last = None
        journal = self.path / 'attempts.jsonl'
        if journal.exists():
            # Only the final durable outcome can settle the outstanding attempt.
            with journal.open('rb') as f:
                f.seek(max(0, journal.stat().st_size - 65536))
                lines = f.read().splitlines()
            if lines:
                try: last = json.loads(lines[-1])
                except ValueError: pass
        if last and last.get('index') == pending:
            if last['outcome'] == 'rejected':
                self.state.update(next_index=pending + 1, pending=None)
            elif last['outcome'] == 'accepted':
                self.accept(BASE + pending * self.state['step'])

    def save(self):
        self.state['updated_at'] = time.time()
        self.state['percent'] = 100 * self.state['next_index'] / self.state['total']
        atomic_json(self.file, self.state)

    def log(self, message: str):
        line = time.strftime('%Y-%m-%d %H:%M:%S') + ' | ' + message
        print(line, flush=True)
        with (self.path / 'events.log').open('a', encoding='utf-8') as f: f.write(line + '\n')

    def outcome(self, index: int, outcome: str, output: str):
        with (self.path / 'attempts.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(dict(time=time.time(), index=index, outcome=outcome, output=output[-8192:])) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def accept(self, code: int):
        result = f'{code}\nCommand accepted; verify the phone screen. This is NOT proof that unlocking completed.\n'
        with (self.path / 'BOOTLOADER_CODE.txt').open('w', encoding='utf-8') as f:
            f.write(result); f.flush(); os.fsync(f.fileno())
        self.state.update(status='accepted', accepted_code=str(code), pending=None)
        self.save()


class InstanceLock:
    """One process controlling phones per data root, including console commands."""
    def __init__(self, root: Path): self.root = root
    def __enter__(self):
        import msvcrt
        self.root.mkdir(parents=True, exist_ok=True)
        self.f = (self.root / 'controller.lock').open('a+b')
        self.f.write(b'0'); self.f.flush(); self.f.seek(0)
        try: msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.f.close()
            raise ReviewRequired('Another controller is active. Use pause and wait for it to stop.')
        return self
    def __exit__(self, *args): self.f.close()


class Transport:
    def __init__(self, tools: Path, serial: str): self.tools, self.serial = tools, serial
    def run(self, tool: str, *args: str, timeout=30, targeted=True):
        command = [str(self.tools / (tool + '.exe'))]
        if targeted: command += ['-s', self.serial]
        command += list(args)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise ConnectionLost(str(exc)) from exc
        return result.returncode, result.stdout + result.stderr
    def mode(self):
        _, adb = self.run('adb', 'devices', targeted=False, timeout=10)
        if re.search(r'^' + re.escape(self.serial) + r'\s+device\b', adb, re.M): return 'adb'
        _, fastboot = self.run('fastboot', 'devices', targeted=False, timeout=10)
        if re.search(r'^' + re.escape(self.serial) + r'\s+fastboot\b', fastboot, re.M): return 'fastboot'
        return None
    def checked(self, tool, *args):
        code, out = self.run(tool, *args)
        if code: raise ConnectionLost(out)
        return out


def parse_battery(output: str) -> float:
    level = re.search(r'^\s*level:\s*(\d+)\s*$', output, re.M)
    scale = re.search(r'^\s*scale:\s*(\d+)\s*$', output, re.M)
    if not level or not scale or not 0 < int(scale[1]) or not 0 <= int(level[1]) <= int(scale[1]):
        raise ConnectionLost('Battery reading unavailable or invalid')
    if 'UPDATES STOPPED' in output.upper(): raise ReviewRequired('Android battery reporting is simulated/frozen')
    return 100 * int(level[1]) / int(scale[1])


def parcel_imei(output: str) -> str:
    words = []
    for line in output.splitlines():
        match = re.match(r'\s*0x[0-9a-fA-F]+:\s*(.*)', line)
        if match: words += re.findall(r'\b[0-9a-fA-F]{8}\b', match[1].split("'")[0])
    raw = b''.join(int(word, 16).to_bytes(4, 'little') for word in words)
    if len(raw) < 8 or int.from_bytes(raw[:4], 'little') != 0 or int.from_bytes(raw[4:8], 'little') != 15:
        raise ValueError('IMEI unavailable: Android may restrict identifier access')
    return validate_imei(raw[8:38].decode('utf-16le'))


def notify(title: str, message: str):
    if os.name == 'nt': ctypes.windll.user32.MessageBoxW(0, message, title, 0x40 | 0x10000)
    else: print(title + ': ' + message)


@dataclass
class Policy:
    battery_interval: float = 21600
    recovery_delay: float = 300
    charge_interval: float = 3600
    low: float = 10
    resume_above: float = 80
    boot_timeout: float = 300


class Runner:
    def __init__(self, store: Store, transport: Transport, policy=None, clock=time.time, sleep=time.sleep, popup=notify):
        self.store, self.t, self.p = store, transport, policy or Policy()
        self.now, self.sleep, self.popup = clock, sleep, popup
        self.s = store.state
        self.started = clock(); self.initial = self.s['next_index']
    def stopped(self): return (self.store.path / 'STOP').exists()
    def status(self, name, **fields):
        self.s.update(status=name, **fields)
        elapsed = max(1, self.now() - self.started)
        rate = (self.s['next_index'] - self.initial) * 3600 / elapsed
        self.s.update(rate_per_hour=rate, eta_hours=(self.s['total'] - self.s['next_index']) / rate if rate else None)
        self.store.save()
    def wait_until(self, deadline):
        while self.now() < deadline:
            if self.stopped(): raise InterruptedError('Pause requested')
            self.status(self.s['status'])
            self.sleep(min(5, deadline - self.now()))
    def wait_mode(self, wanted):
        deadline = self.now() + self.p.boot_timeout
        while self.now() < deadline:
            if self.stopped(): raise InterruptedError('Pause requested')
            if self.t.mode() == wanted: return
            self.status('waiting_' + wanted)
            self.sleep(2)
        raise ConnectionLost('Timed out waiting for ' + wanted + ' on the selected serial')
    def android(self):
        mode = self.t.mode()
        if mode == 'fastboot': self.t.checked('fastboot', 'reboot')
        self.wait_mode('adb')
    def fastboot(self):
        mode = self.t.mode()
        if mode == 'adb': self.t.checked('adb', 'reboot', 'bootloader')
        elif mode == 'fastboot': self.t.checked('fastboot', 'reboot-bootloader')
        self.wait_mode('fastboot')
    def battery(self):
        self.status('checking_battery')
        self.android()
        self.wait_until(self.now() + 5)
        raw = self.t.checked('adb', 'shell', 'dumpsys', 'battery')
        level = parse_battery(raw)
        (self.store.path / 'battery-raw.txt').write_text(raw, encoding='utf-8')
        self.s.update(battery_percent=level, battery_checked_at=self.now())
        self.store.log(f'BATTERY Android reports {level:g}% (snapshot)')
        if level < self.p.low or self.s.get('charging_until') is not None:
            if self.s.get('charging_until') is None:
                self.s['charging_until'] = self.now() + self.p.charge_interval
            if self.now() < self.s['charging_until'] or level <= self.p.resume_above:
                if self.now() >= self.s['charging_until']:
                    self.s['charging_until'] = self.now() + self.p.charge_interval
                self.status('charging', next_battery_check=self.s['charging_until'])
                self.store.log('CHARGING: attempts paused; next check after one hour; resume only above 80%. Connect a charger.')
                return False
            self.s['charging_until'] = None
        self.status('ready')
        return True
    def group(self):
        info = self.t.checked('fastboot', 'oem', 'get-bootinfo').lower()
        if 'unlocked' in info or not re.search(r'^\s*(?:\(bootloader\)\s*)?locked\s*$', info, re.M):
            raise ReviewRequired('Bootloader is not confirmed LOCKED; no code sent')
        self.store.log('READY: device acknowledged LOCKED')
        for _ in range(min(4, self.s['total'] - self.s['next_index'])):
            if self.stopped(): raise InterruptedError('Pause requested')
            index = self.s['next_index']; candidate = BASE + index * self.s['step']
            self.status('probing', pending=index)
            self.store.log(f'TRY {candidate} | attempt {index + 1}/{self.s["total"]}')
            try: code, out = self.t.run('fastboot', 'oem', 'unlock', str(candidate))
            except ConnectionLost as exc:
                raise ReviewRequired('Unlock outcome unknown. No automatic resend or reboot: ' + str(exc)) from exc
            if code == 0 and 'okay' in out.lower() and 'failed' not in out.lower():
                self.store.outcome(index, 'accepted', out)
                self.store.accept(candidate)
                self.store.log('ACCEPTED: code saved; all attempts stopped. Check the phone confirmation screen.')
                self.popup('Bootloader command accepted', f'Code: {candidate}\nSaved in {self.store.path}\nCheck the phone. Unlocking may already have started. No further commands will be sent.')
                return False
            if code != 0 and 'check password failed' in out.lower() and not any(x in out.lower() for x in ('reboot', 'not allowed', 'too many')):
                self.store.outcome(index, 'rejected', out)
                self.status('running', next_index=index + 1, pending=None)
            else:
                self.store.outcome(index, 'unknown', out)
                raise ReviewRequired('Unexpected unlock response; no automatic resend: ' + out)
        return True
    def run(self):
        if self.s.get('pending') is not None: raise ReviewRequired('Outstanding unlock outcome needs review')
        if self.s['status'] in ('accepted', 'exhausted'): return
        check_battery = True
        try:
            while self.s['next_index'] < self.s['total']:
                if self.stopped(): raise InterruptedError('Pause requested')
                try:
                    if self.s.get('charging_until'):
                        self.status('charging')
                        self.wait_until(self.s['charging_until'])
                        check_battery = True
                    if check_battery or self.now() - self.s.get('battery_checked_at', 0) >= self.p.battery_interval:
                        if not self.battery(): continue
                        self.fastboot(); check_battery = False
                    if not self.group(): return
                    if self.s['next_index'] < self.s['total']:
                        self.status('rebooting_bootloader')
                        self.t.checked('fastboot', 'reboot-bootloader')
                        self.wait_mode('fastboot')
                except ConnectionLost as exc:
                    self.status('recovering', error=str(exc), recovery_at=self.now() + self.p.recovery_delay)
                    self.store.log('CONNECTION LOST: no probes sent; normal-boot/battery recovery in 5 minutes')
                    self.wait_until(self.s['recovery_at'])
                    check_battery = True
            self.status('exhausted')
            self.popup('Sequence exhausted', 'No accepted code in this experimental sequence. Progress saved.')
        except InterruptedError:
            self.status('paused')
            self.store.log('PAUSED: no further commands; saved progress')
        except ReviewRequired as exc:
            self.status('review_required', error=str(exc))
            self.store.log('STOPPED: ' + str(exc))
        except Exception as exc:
            # Disk failures must never lead to additional phone writes.
            try: self.status('storage_or_internal_error', error=str(exc))
            except Exception: pass
            print('STOPPED: ' + str(exc), flush=True)
            raise
