"""sysfs em diretório temporário: nunca altera drivers/devices reais."""
import errno
from pathlib import Path
import subprocess
import threading
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_ffb_audit import ROOT
from dfgt_bridge.input.virtual_g29 import NAME
from dfgt_bridge.pid_binding import PIDBinding, BindingError, DRIVER, DYNAMIC_ID, bitmap, load_existing_module

TOKEN = 'dfgt-bridge-test-owned'
NODE = '0003:046D:C24F.0011'


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory) / 'sys'
        self.devices = self.root / 'bus/hid/devices'
        self.drivers = self.root / 'bus/hid/drivers'
        self.devices.mkdir(parents=True)
        self.driver = self.drivers / DRIVER
        self.driver.mkdir(parents=True)
        for name in ('bind', 'new_id'):
            (self.driver / name).write_bytes(b'')
        (self.drivers / 'logitech').mkdir()
        (self.drivers / 'logitech/unbind').write_bytes(b'')
        self.now = 0.
        self.writes = []
        self.native = False
        self.event_present = True
        self.hold = None
        self.probed = threading.Event()
        self.probe_failed = False
        self.error_code = errno.ENODEV
        self.binder = PIDBinding(root=self.root, dev_root=Path(self.directory)/'dev', clock=lambda: self.now,
                                 module_loader=Mock(side_effect=AssertionError('must not load real modules')),
                                 writer=self.write, event_exists=lambda path: self.event_present)
        self.addCleanup(self.binder.finish)

    def device(self, node=NODE, *, token=TOKEN, physical=False, driver=None):
        parent = 'devices/pci0000:00/usb1' if physical else 'devices/virtual/misc/uhid'
        real = self.root / parent / node
        real.mkdir(parents=True)
        product = node.split(':')[2].split('.')[0]
        (real / 'uevent').write_text(f'HID_ID=0003:0000046D:0000{product}\nHID_NAME={NAME}\nHID_PHYS={token}\nHID_UNIQ=\n')
        path = self.devices / node
        path.symlink_to(real)
        if driver:
            (real / 'driver').symlink_to(self.drivers / driver)
        return path

    def input(self, path=None, *, ff='40000 0', ev='200009', axes='1'):
        path = path or self.devices / NODE
        base = path / 'input/input42'
        (base / 'capabilities').mkdir(parents=True, exist_ok=True)
        for name, value in (('ff', ff), ('ev', ev), ('abs', axes)):
            (base / 'capabilities' / name).write_text(value+'\n')
        event = base / 'event42'
        event.mkdir(exist_ok=True)
        (event / 'dev').write_text('13:106\n')

    def bound(self, path=None):
        path = path or self.devices / NODE
        link = path / 'driver'
        if link.is_symlink():
            link.unlink()
        link.symlink_to(self.driver)
        self.input(path)

    def write(self, path, payload):
        self.assertTrue(path.is_relative_to(self.root))
        self.writes.append((path, payload))
        if path.name == 'unbind':
            self.assertEqual(payload, (NODE+'\n').encode())
            (self.devices / NODE / 'driver').unlink()
            if self.probe_failed:
                raise OSError(errno.ENODEV, 'logitech probe falhou com -22 antes do unbind')
        elif path.name == 'bind':
            self.assertEqual(payload, (NODE+'\n').encode())
            if not self.native:
                raise OSError(self.error_code, 'driver não aceita este ID')
            self.bound()
            self.probed.set()
            if self.hold:
                self.hold.wait(1)
        elif path.name == 'new_id':
            self.assertEqual(payload, DYNAMIC_ID)
            if self.hold:
                self.hold.wait(1)
            self.bound()
        else:
            self.fail('sysfs write inesperado')

    def begin(self):
        self.binder.prepare()
        self.binder.start(TOKEN)
        self.binder.poll()

    def ready(self):
        self.begin()
        self.binder.job.join(1)
        return self.binder.poll()

    def test_successful_guarded_dynamic_id_and_ff_readiness(self):
        self.device()
        self.assertTrue(self.ready())
        self.assertEqual([p.name for p, data in self.writes], ['bind', 'new_id'])
        self.assertEqual(self.binder.last['driver'], DRIVER)
        self.assertEqual(self.binder.last['ff_input'], ['event42'])
        self.assertTrue(self.binder.last['dynamic_id_added'])

    def test_native_targeted_bind_does_not_register_global_id(self):
        self.native = True
        self.device()
        self.assertTrue(self.ready())
        self.assertEqual([p.name for p, data in self.writes], ['bind'])

    def test_already_pidff_only_verifies_input_without_any_write(self):
        path = self.device(driver=DRIVER)
        self.input(path)
        self.assertTrue(self.ready())
        self.assertEqual(self.writes, [])

    def test_logitech_minus_22_recovery_never_unbinds_physical_dfgt(self):
        dfgt = self.device('0003:046D:C29A.000E', token='physical-dfgt', physical=True, driver='logitech')
        g920 = self.device('0003:046D:C262.000F', token='physical-g920', physical=True, driver='logitech')
        original = [(p / 'driver').readlink() for p in (dfgt, g920)]
        self.device(driver='logitech')
        self.probe_failed = True
        self.assertTrue(self.ready())
        self.assertEqual([(p / 'driver').readlink() for p in (dfgt, g920)], original)
        self.assertEqual(self.writes[0][1], (NODE+'\n').encode())
        self.assertEqual([p.name for p, data in self.writes], ['unbind', 'bind', 'new_id'])

    def test_foreign_physical_g29_prevents_dynamic_registration(self):
        foreign = self.device('0003:046D:C24F.0020', token='physical-g29', physical=True, driver='logitech')
        self.device()
        with self.assertRaisesRegex(BindingError, 'Outro G29'):
            self.begin()
            self.binder.job.join(1)
            self.binder.poll()
        self.assertFalse(any(p.name == 'new_id' for p, _ in self.writes))
        self.assertEqual((foreign / 'driver').resolve(), self.drivers / 'logitech')

    def test_physical_path_with_copied_token_is_never_a_valid_target(self):
        self.device(physical=True)
        with self.assertRaisesRegex(BindingError, 'UHID virtual'):
            self.begin()
        self.assertEqual(self.writes, [])

    def test_another_uhid_is_never_selected_by_difference_alone(self):
        self.device(token='dfgt-bridge-another-process')
        self.begin()
        self.now = 26
        with self.assertRaisesRegex(BindingError, 'Timeout'):
            self.binder.poll()
        self.assertEqual(self.writes, [])

    def test_pidff_probe_error_is_not_hidden_by_new_id_retry(self):
        self.device()
        self.error_code = errno.EINVAL
        with self.assertRaisesRegex(BindingError, 'Errno 22'):
            self.begin()
            self.binder.job.join(1)
            self.binder.poll()
        self.assertEqual([p.name for p, _ in self.writes], ['bind'])

    def test_ff_and_event_presence_are_both_required(self):
        path = self.device(driver=DRIVER)
        self.input(path)
        self.event_present = False
        self.begin()
        self.binder.job.join(1)
        self.assertFalse(self.binder.poll())
        self.event_present = True
        self.input(path, ff='0')
        self.assertFalse(self.binder.poll())
        self.input(path, ff='40000 0')
        self.assertTrue(self.binder.poll())

    def test_wrong_ff_bitmap_or_missing_x_axis_never_claims_ready(self):
        path = self.device(driver=DRIVER)
        self.input(path, ff='10000 0')
        self.begin()
        self.binder.job.join(1)
        self.assertFalse(self.binder.poll())
        self.input(path, axes='0')
        self.assertFalse(self.binder.poll())
        self.now = 26
        with self.assertRaisesRegex(BindingError, 'EVDEV virtual e FF_CONSTANT'):
            self.binder.poll()

    def test_link_and_capabilities_do_not_mean_probe_thread_has_finished(self):
        self.native = True
        self.hold = threading.Event()
        self.addCleanup(self.hold.set)
        self.device()
        self.begin()
        # O sysfs pode publicar entrada/caps enquanto o probe ainda espera ACK.
        self.assertTrue(self.probed.wait(1))
        self.assertFalse(self.binder.poll())
        self.hold.set()
        self.binder.job.join(1)
        self.assertTrue(self.binder.poll())

    def test_timeout_and_cancel_do_not_schedule_more_writes(self):
        self.device()
        self.binder.prepare()
        self.binder.start(TOKEN)
        self.binder.cancel()
        with self.assertRaisesRegex(BindingError, 'cancelada'):
            self.binder.poll()
        self.assertEqual(self.writes, [])

    def test_missing_device_times_out_with_driver_diagnostic(self):
        self.begin()
        self.now = 26
        with self.assertRaisesRegex(BindingError, "driver.*unbound"):
            self.binder.poll()

    def test_disconnect_after_readiness_fails_and_never_retargets_another_device(self):
        path = self.device()
        self.assertTrue(self.ready())
        path.unlink()
        self.device('0003:046D:C24F.0044', token='someone-else')
        with self.assertRaisesRegex(BindingError, 'removido'):
            self.binder.poll()
        self.assertEqual(len(self.writes), 2)

    def test_lost_ff_after_ready_is_fatal(self):
        path = self.device()
        self.assertTrue(self.ready())
        self.input(path, ff='0')
        with self.assertRaisesRegex(BindingError, 'perdeu driver'):
            self.binder.poll()

    def test_late_readiness_cannot_bypass_the_startup_deadline(self):
        self.device(driver=DRIVER)
        self.input()
        self.binder.prepare()
        self.binder.start(TOKEN)
        self.now = 26
        with self.assertRaisesRegex(BindingError, 'Timeout'):
            self.binder.poll()

    def test_driver_missing_after_module_loader_reports_clear_failure(self):
        other = self.root / 'missing_sys'
        load = Mock()
        binder = PIDBinding(root=other, module_loader=load)
        with self.assertRaisesRegex(BindingError, 'ausente'):
            binder.prepare()
        load.assert_called_once()

    def test_installed_module_is_loaded_once_only_when_driver_is_absent(self):
        root = self.root / 'not_loaded_sys'
        driver = root / 'bus/hid/drivers' / DRIVER
        def load():
            driver.mkdir(parents=True)
            (driver / 'bind').write_bytes(b'')
        loader = Mock(side_effect=load)
        binder = PIDBinding(root=root, module_loader=loader)
        binder.prepare()
        binder.prepare()
        loader.assert_called_once()

    def test_event_node_check_uses_stat_without_opening_input(self):
        path = self.device()
        self.input(path)
        event = path / 'input/input42/event42'
        self.assertFalse(self.binder._event_exists(event))
        dev = self.binder.dev_root / 'input/event42'
        dev.parent.mkdir(parents=True)
        dev.write_bytes(b'fake-node')
        (event / 'dev').write_text('0:0')
        with patch('dfgt_bridge.pid_binding.stat.S_ISCHR', return_value=True):
            self.assertTrue(self.binder._event_exists(event))


class ModuleTests(unittest.TestCase):
    def test_missing_module_and_loader_timeout_are_explicit_without_installation(self):
        for result in (FileNotFoundError('modprobe missing'), subprocess.TimeoutExpired('modprobe', 5)):
            with patch('subprocess.run', side_effect=result), self.assertRaises(BindingError):
                load_existing_module()
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'module not found')) as run:
            with self.assertRaisesRegex(BindingError, 'indisponível'):
                load_existing_module()
            self.assertEqual(run.call_args.args[0], ['modprobe', DRIVER])

    def test_bitmap_has_kernel_word_order_and_constant_ff_bit(self):
        self.assertTrue(bitmap('11fff0000 0') & (1 << 0x52))
        self.assertEqual(bitmap('0'), 0)
        with self.assertRaises(ValueError):
            bitmap('not-a-bitmap')
