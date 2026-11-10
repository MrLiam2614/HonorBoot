import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from honorboot.core import (BASE, ConnectionLost, Policy, ReviewRequired, Runner,
                            Store, parse_battery, parcel_imei, sequence, checksum)

IMEI = '490154203237518'  # Published GSM example, not a user's identifier.


class Clock:
    def __init__(self): self.value = 100000
    def now(self): return self.value
    def sleep(self, duration): self.value += duration


class FakePhone:
    def __init__(self, levels=(90,), outcome='rejected'):
        self.levels = iter(levels); self.level = 90; self.current = 'fastboot'
        self.calls = []; self.outcome = outcome; self.fail_info = False
    def mode(self): return self.current
    def checked(self, tool, *args):
        code, output = self.run(tool, *args)
        if code: raise ConnectionLost(output)
        return output
    def run(self, tool, *args):
        self.calls.append((tool, *args))
        if args == ('reboot',): self.current = 'adb'; return 0, 'OKAY'
        if args in (('reboot','bootloader'), ('reboot-bootloader',)):
            self.current = 'fastboot'; return 0, 'OKAY'
        if args == ('shell','dumpsys','battery'):
            self.level = next(self.levels, self.level)
            return 0, f'level: {self.level}\nscale: 100\n'
        if args == ('oem','get-bootinfo'):
            if self.fail_info:
                self.fail_info = False
                raise ConnectionLost('disconnected')
            return 0, '(bootloader) locked\nOKAY\n'
        if args[:2] == ('oem','unlock'):
            if self.outcome == 'timeout': raise ConnectionLost('timeout')
            if self.outcome == 'accepted': return 0, 'OKAY\n'
            if self.outcome == 'unknown': return 0, 'waiting for confirmation'
            return 1, "FAILED (remote: 'check password failed!')"
        raise AssertionError((tool,args))


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.store = Store(self.root,'TEST-SERIAL',IMEI); self.clock = Clock()
        self.output = contextlib.redirect_stdout(io.StringIO()); self.output.__enter__()
    def tearDown(self): self.output.__exit__(None,None,None); self.temp.cleanup()
    def runner(self, phone):
        return Runner(self.store,phone,clock=self.clock.now,sleep=self.clock.sleep,popup=lambda *args:None)
    def test_partition_by_device_and_imei(self):
        other = Store(self.root,'OTHER-SERIAL',IMEI)
        self.assertNotEqual(self.store.path, other.path)
        self.store.state['next_index']=42; self.store.save()
        self.assertEqual(Store(self.root,'TEST-SERIAL',IMEI).state['next_index'],42)
        self.assertEqual(other.state['next_index'],0)
    def test_sequence_bounds(self):
        step,total=sequence(IMEI)
        self.assertLessEqual(BASE+(total-1)*step,10**16-1)
        self.assertGreater(BASE+total*step,10**16-1)
    def test_two_imei_seeds_on_same_phone_are_independent(self):
        prefix='12345678901234'
        second=next(prefix+str(d) for d in range(10) if checksum(prefix+str(d))==0)
        other=Store(self.root,'TEST-SERIAL',second)
        self.assertNotEqual(other.path,self.store.path)
        self.store.state['next_index']=100; self.store.save()
        self.assertEqual(other.state['next_index'],0)
    def test_no_matching_device_recovery_does_not_send_unlock(self):
        phone=FakePhone(); phone.current=None
        runner=self.runner(phone); runner.p.boot_timeout=4
        with self.assertRaises(ConnectionLost): runner.android()
        self.assertFalse(any('unlock' in c for c in phone.calls))
        self.assertEqual(self.store.state['next_index'],0)
    def test_read_only_loading_never_reconciles_or_writes(self):
        self.store.state['pending']=0; self.store.save()
        self.store.outcome(0,'accepted','OKAY')
        before=self.store.file.read_bytes()
        Store(self.root,'TEST-SERIAL',IMEI,reconcile=False)
        self.assertEqual(self.store.file.read_bytes(),before)
        self.assertFalse((self.store.path/'BOOTLOADER_CODE.txt').exists())
    def test_four_rejections_only_advance_four(self):
        phone=FakePhone(); self.assertTrue(self.runner(phone).group())
        self.assertEqual(self.store.state['next_index'],4)
        self.assertIsNone(self.store.state['pending'])
        self.assertEqual(phone.calls[0],('fastboot','oem','get-bootinfo'))
    def test_unknown_unlock_does_not_reboot_or_advance(self):
        for outcome in ('timeout','unknown'):
            phone=FakePhone(outcome=outcome)
            with self.assertRaises(ReviewRequired): self.runner(phone).group()
            self.assertEqual(self.store.state['next_index'],0)
            self.assertEqual(self.store.state['pending'],0)
            self.assertFalse(any('reboot' in str(call) for call in phone.calls))
    def test_connection_loss_before_code_does_not_advance(self):
        phone=FakePhone(); phone.fail_info=True
        with self.assertRaises(ConnectionLost): self.runner(phone).group()
        self.assertEqual(self.store.state['next_index'],0)
        self.assertIsNone(self.store.state['pending'])
    def test_accepted_saved_and_popup(self):
        phone=FakePhone(outcome='accepted'); seen=[]; runner=self.runner(phone); runner.popup=lambda *x:seen.append(x)
        self.assertFalse(runner.group())
        self.assertEqual(self.store.state['status'],'accepted')
        self.assertTrue((self.store.path/'BOOTLOADER_CODE.txt').exists())
        self.assertEqual(len(seen),1)
        self.assertEqual(len([c for c in phone.calls if 'unlock' in c]),1)
    def test_recover_durable_rejection_after_failed_checkpoint(self):
        self.store.state['pending']=0; self.store.save()
        self.store.outcome(0,'rejected','wrong code')
        restored=Store(self.root,'TEST-SERIAL',IMEI)
        self.assertEqual(restored.state['next_index'],1)
        self.assertIsNone(restored.state['pending'])
    def test_recover_durable_accepted_after_crash(self):
        self.store.state['pending']=0; self.store.save()
        self.store.outcome(0,'accepted','OKAY')
        restored=Store(self.root,'TEST-SERIAL',IMEI)
        self.assertEqual(restored.state['status'],'accepted')
        self.assertTrue((restored.path/'BOOTLOADER_CODE.txt').exists())
    def test_unsettled_pending_is_not_retried(self):
        self.store.state['pending']=0; self.store.save()
        restored=Store(self.root,'TEST-SERIAL',IMEI)
        phone=FakePhone()
        with self.assertRaises(ReviewRequired): Runner(restored,phone).run()
        self.assertEqual(phone.calls,[])
    def test_charge_at_nine_wait_hour_and_strictly_above_eighty(self):
        phone=FakePhone((9,80,81)); runner=self.runner(phone)
        self.assertFalse(runner.battery())
        first=self.store.state['charging_until']
        self.assertEqual(first-self.clock.now(),3600)
        self.clock.value=first
        self.assertFalse(runner.battery())
        self.assertEqual(self.store.state['status'],'charging')
        self.clock.value=self.store.state['charging_until']
        self.assertTrue(runner.battery())
        self.assertIsNone(self.store.state['charging_until'])
    def test_ten_does_not_trigger_low_threshold(self):
        self.assertTrue(self.runner(FakePhone((10,))).battery())
    def test_charge_persists_after_restart(self):
        self.runner(FakePhone((1,))).battery()
        restored=Store(self.root,'TEST-SERIAL',IMEI)
        self.assertEqual(restored.state['charging_until'],self.store.state['charging_until'])
    def test_recovery_waits_five_minutes_then_android(self):
        phone=FakePhone(); phone.fail_info=True; runner=self.runner(phone)
        start=self.clock.now()
        def finish_group():
            if phone.fail_info:
                phone.fail_info=False; raise ConnectionLost('test')
            self.assertGreaterEqual(self.clock.now()-start,300)
            self.assertGreaterEqual(phone.calls.count(('fastboot','reboot')),2)
            return False
        runner.group=finish_group; runner.run()
        self.assertEqual(self.store.state['next_index'],0)
    def test_periodic_battery_is_automatic(self):
        phone=FakePhone(); runner=self.runner(phone); count=0
        def group():
            nonlocal count
            count+=1
            if count==1:
                self.clock.value+=21600
                return True
            self.assertEqual(phone.calls.count(('adb','shell','dumpsys','battery')),2)
            return False
        runner.group=group; runner.run()
    def test_stop_during_recovery_wait(self):
        (self.store.path/'STOP').write_text('pause')
        with self.assertRaises(InterruptedError): self.runner(FakePhone()).wait_until(self.clock.now()+300)
    def test_battery_frozen_or_invalid_is_rejected(self):
        for raw in ('level: 110\nscale: 100','level: 20','UPDATES STOPPED\nlevel: 20\nscale: 100'):
            with self.assertRaises((ConnectionLost,ReviewRequired)): parse_battery(raw)
    def test_imei_parcel(self):
        raw=(0).to_bytes(4,'little')+(15).to_bytes(4,'little')+IMEI.encode('utf-16le')+b'\x00\x00'
        words=[f'{int.from_bytes(raw[i:i+4],"little"):08x}' for i in range(0,len(raw),4)]
        text='\n'.join(f'0x{i*4:08x}: '+ ' '.join(words[i:i+4]) for i in range(0,len(words),4))
        self.assertEqual(parcel_imei(text),IMEI)
        with self.assertRaises(ValueError): parcel_imei('Permission denied')


if __name__=='__main__': unittest.main()
