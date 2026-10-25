"""Integração Steam offline; nenhum comando físico/systemd real é executado."""
import copy
import json
import os
from pathlib import Path
import signal
import socket
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from test_ffb_audit import ROOT
from steam_fakes import READY, FakeProc, FakeRunner
from dfgt_bridge.config import default_profile, ProfileStore
from dfgt_bridge.steam_launcher import IntegrationSettings
from dfgt_bridge.steam_install import Installer, units, UNITS, RUNTIME, MARKER


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.folder=self.enterContext(tempfile.TemporaryDirectory());self.root=Path(self.folder)
        self.account=SimpleNamespace(pw_uid=1000,pw_gid=1000,pw_name='deck',pw_dir='/home/deck')
        self.cmd=Mock()
        self.inst=Installer(self.account,ROOT,root=self.root,command=self.cmd,user_action=lambda f:f(),chown=lambda *a:None)

    def test_install_twice_and_uninstall_twice_preserve_profiles_and_user_data(self):
        other=default_profile();other['ffb_strength']=50
        ProfileStore(self.inst.profiles).save('other game',other)
        option=self.inst.install();self.inst.install()
        self.assertEqual(option,'PROTON_ENABLE_HIDRAW=0x046d/0xc24f "/home/deck/.local/bin/dfgt-steam-launch" -- %command%')
        self.assertTrue(self.inst.wrapper.exists())
        self.assertTrue((self.inst.path(RUNTIME)/'src/dfgt_bridge/steam_service.py').is_file())
        original=self.inst.profiles.read_bytes()
        self.inst.uninstall();self.inst.uninstall()
        self.assertEqual(self.inst.profiles.read_bytes(),original)
        self.assertFalse(self.inst.wrapper.exists())
        self.assertEqual(ProfileStore(self.inst.profiles).load('other game'),other)
        self.assertFalse(IntegrationSettings(self.inst.profiles.with_name('integration.json')).enabled())
        self.assertFalse(any((self.inst.path('/etc/systemd/system')/u).exists() for u in UNITS))

    def test_invalid_saved_profile_is_not_silently_lowered_or_overwritten(self):
        bad=default_profile();bad['ffb_polarity']=1
        ProfileStore(self.inst.profiles).save('dirt5',bad)
        before=self.inst.profiles.read_bytes()
        with self.assertRaises(ValueError):self.inst.install()
        self.assertEqual(self.inst.profiles.read_bytes(),before);self.cmd.assert_not_called()

    def test_foreign_wrapper_and_units_are_preserved(self):
        self.inst.wrapper.parent.mkdir(parents=True);self.inst.wrapper.write_text('personal script')
        with self.assertRaises(ValueError):self.inst.install()
        self.assertEqual(self.inst.wrapper.read_text(),'personal script')

    def test_reinstall_preserves_disabled_gui_preference(self):
        self.inst.install()
        preferences=IntegrationSettings(self.inst.profiles.with_name('integration.json'))
        preferences.save(False)
        self.inst.install()
        self.assertFalse(preferences.enabled())

    def test_snapshot_update_does_not_keep_obsolete_code(self):
        self.inst.install()
        obsolete=self.inst.path(RUNTIME)/'src/dfgt_bridge/obsolete.py'
        obsolete.write_text('# old snapshot\n')
        self.inst.install()
        self.assertFalse(obsolete.exists())

    def test_privileged_symlink_destination_is_rejected(self):
        folder=self.inst.path('/var/lib/dfgt-bridge');folder.parent.mkdir(parents=True)
        folder.symlink_to(self.root/'other')
        with self.assertRaises(ValueError):self.inst.install()

    def test_units_have_fixed_backend_no_sudoers_or_writable_usr(self):
        text='\n'.join(units(1000,1000).values())
        self.assertIn('SocketMode=0600',text);self.assertIn('KillMode=control-group',text)
        self.assertIn('WatchdogSec=5',text);self.assertIn('DevicePolicy=closed',text)
        self.assertIn('ProtectHome=yes',text);self.assertIn('CapabilityBoundingSet=CAP_SYS_MODULE',text)
        self.assertNotIn('sudo',text);self.assertNotIn('chmod 666',text)
        self.assertNotIn('steamos-readonly',text)
        self.assertIn('/var/lib/dfgt-bridge/runtime',text)

    def test_service_runtime_never_chowns_control_socket_tree(self):
        config = units(1000, 1000)
        service = config['dfgt-bridge.service']
        socket_unit = config['dfgt-bridge.socket']
        self.assertIn('RuntimeDirectory=dfgt-bridge-backend\n', service)
        self.assertIn('RuntimeDirectoryMode=0700', service)
        self.assertIn('ListenSequentialPacket=/run/dfgt-bridge/control.sock', socket_unit)
        self.assertIn('SocketUser=1000', socket_unit)
        self.assertIn('SocketMode=0600', socket_unit)
        runtime = next(line.split('=', 1)[1] for line in service.splitlines() if line.startswith('RuntimeDirectory='))
        self.assertFalse('/run/dfgt-bridge/control.sock'.startswith('/run/'+runtime+'/'))
        self.assertIn('ProtectSystem=strict', service)
        self.assertIn('ProtectHome=yes', service)

    def test_reinstall_private_0600_profile_and_both_lock_generations(self):
        self.inst.install()
        self.assertEqual(self.inst.profiles.stat().st_mode & 0o777, 0o600)
        for name in ('dfgt-bridge', 'dfgt-bridge-backend'):
            lock = self.inst.path('/run/'+name+'/device.lock')
            lock.parent.mkdir(parents=True, exist_ok=True)
            from dfgt_bridge.steam_service import DeviceLock
            guard = DeviceLock(lock)
            try:
                with self.assertRaisesRegex(RuntimeError, 'reservado'):
                    self.inst.install()
            finally:
                guard.close()
        self.inst.install()
        self.assertEqual(ProfileStore(self.inst.profiles).load(), default_profile())
