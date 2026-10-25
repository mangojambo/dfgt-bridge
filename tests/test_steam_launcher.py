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
from dfgt_bridge.steam_launcher import run_game, GameTree, IntegrationSettings


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('os.geteuid',return_value=1000))
        self.enterContext(patch('os.getuid',return_value=1000))
        self.enterContext(patch('os.getpid',return_value=55))
        self.signals=self.enterContext(patch('os.kill'))
        self.groups=self.enterContext(patch('os.killpg'))
        self.access=self.enterContext(patch('os.access',return_value=True))
        self.proc=FakeProc();self.now=0
        self.client=Mock();self.client.request.return_value=copy.deepcopy(READY)
        self.process=Mock(pid=100);self.process.poll.return_value=0;self.process.wait.return_value=0
        self.spawn=Mock(return_value=self.process)
    def sleep(self,n):self.now+=n
    def execute(self,command=None,**kw):
        return run_game(command or ['proton','waitforexitandrun','DiRT 5.exe'],settings=default_profile(),
            client_factory=lambda:self.client,spawn=self.spawn,proc=self.proc,clock=lambda:self.now,sleep=self.sleep,**kw)

    def test_preserves_command_spaces_quotes_and_environment_without_shell(self):
        command=['/path with spaces/proton','waitforexitandrun','DiRT 5.exe','a b','"literal"','$(not shell)']
        with patch.dict(os.environ,{'DISPLAY':':42','STEAM_COMPAT_DATA_PATH':'/prefix with spaces','PROTON_ENABLE_HIDRAW':'wrong'}):
            with self.assertRaises(TimeoutError):self.execute(command,startup_timeout=1.5)
        self.assertEqual(self.spawn.call_args.args[0],command)
        env=self.spawn.call_args.kwargs['env']
        self.assertEqual(env['DISPLAY'],':42');self.assertEqual(env['STEAM_COMPAT_DATA_PATH'],'/prefix with spaces')
        self.assertEqual(env['PROTON_ENABLE_HIDRAW'],'0x046d/0xc24f')
        self.assertNotIn('shell',self.spawn.call_args.kwargs)

    def test_no_game_spawn_until_real_ready(self):
        def response(op,**kwargs):
            return dict(state='starting') if op=='start' else dict(state='stopping') if op=='stop' else copy.deepcopy(READY)
        self.client.request.side_effect=response
        with self.assertRaises(TimeoutError):self.execute(startup_timeout=1.5)
        self.assertGreaterEqual(self.now,.25)
        self.assertEqual(self.client.request.call_args_list[0].args,('start',))

    def test_connect_denial_reports_socket_before_game_start(self):
        factory = Mock(side_effect=PermissionError(13, 'denied'))
        with self.assertRaisesRegex(OSError, 'launcher.control.connect.*control.sock.*errno=13'):
            run_game([], settings=default_profile(), passive=True, client_factory=factory,
                     proc=self.proc, clock=lambda:self.now, sleep=self.sleep)
        self.spawn.assert_not_called()

    def test_pidff_failure_does_not_launch_game(self):
        self.client.request.return_value=dict(state='failed',error='PIDFF -22')
        with self.assertRaisesRegex(RuntimeError,'-22'):self.execute()
        self.spawn.assert_not_called();self.client.close.assert_called_once()

    def test_busy_second_launcher_never_stops_first_session(self):
        self.client.request.side_effect=RuntimeError('DFGT ocupado')
        with self.assertRaises(RuntimeError):self.execute()
        self.assertEqual(self.client.request.call_count,1)
        self.spawn.assert_not_called()

    def test_cancel_startup_stops_lease_and_never_runs_game(self):
        self.client.request.return_value=dict(state='starting')
        with self.assertRaises(InterruptedError):self.execute(cancelled=lambda:True)
        self.spawn.assert_not_called()
        self.assertEqual(self.client.request.call_args.args,('stop',))

    def test_missing_unprivileged_evdev_permission_does_not_start_game(self):
        self.access.return_value=False
        with self.assertRaisesRegex(PermissionError,'udev/logind'):self.execute()
        self.spawn.assert_not_called()

    def test_evdev_permission_wait_renews_lease_without_opening_device(self):
        self.access.side_effect=[False,False,True]
        run_game([],settings=default_profile(),passive=True,client_factory=lambda:self.client,
                 proc=self.proc,clock=lambda:self.now,sleep=self.sleep)
        self.assertGreaterEqual(self.now,.2)
        self.spawn.assert_not_called()

    def test_abrupt_service_loss_terminates_only_owned_game(self):
        self.process.poll.return_value=None
        self.proc.values[100]=dict(pid=100,uid=1000,ppid=55,ticks=101,comm='proton')
        self.client.request.side_effect=[copy.deepcopy(READY),OSError('daemon died'),dict(state='stopping')]
        with self.assertRaises(OSError):self.execute()
        self.assertIn(((100,signal.SIGTERM),),[(c.args,) for c in self.signals.call_args_list])
        self.client.close.assert_called_once()

    def test_root_can_never_execute_proton(self):
        with patch('os.geteuid',return_value=0),self.assertRaises(PermissionError):self.execute()
        self.spawn.assert_not_called();self.client.request.assert_not_called()

    def test_passive_readiness_probe_has_no_game_or_motor(self):
        self.assertEqual(run_game([],settings=default_profile(),passive=True,client_factory=lambda:self.client,
            spawn=self.spawn,proc=self.proc,clock=lambda:self.now,sleep=self.sleep),0)
        self.spawn.assert_not_called()
        self.assertEqual(self.client.request.call_args_list[0].kwargs['mode'],'passive')

    def test_explicitly_disabled_automation_runs_unprivileged_without_service(self):
        factory=Mock(side_effect=AssertionError('service must stay idle'))
        code=run_game(['game','a b'],settings=default_profile(),automatic=False,client_factory=factory,
            spawn=self.spawn,proc=self.proc,clock=lambda:self.now,sleep=self.sleep)
        self.assertEqual(code,0);factory.assert_not_called()
        self.assertEqual(self.spawn.call_args.args[0],['game','a b'])

    def test_wrapper_exit_does_not_end_session_before_game_and_proton_helpers(self):
        self.proc.values.update({100:dict(pid=100,uid=1000,ppid=55,ticks=101,comm='proton'),
            101:dict(pid=101,uid=1000,ppid=100,ticks=102,comm='DiRT5.exe'),
            102:dict(pid=102,uid=1000,ppid=100,ticks=103,comm='wineserver')})
        self.process.poll.side_effect=lambda:None if self.now<.2 else 0
        stops=[]
        def response(op,**data):
            if op=='stop':stops.append(self.now);return dict(state='stopping')
            return copy.deepcopy(READY)
        self.client.request.side_effect=response
        def advance(n):
            self.now+=n
            if self.now>=.2:self.proc.values.pop(100,None)
            if self.now>=2:self.proc.values.pop(101,None)
            if self.now>=2.4:self.proc.values.pop(102,None)
        self.sleep=advance
        self.assertEqual(self.execute(),0)
        self.assertGreaterEqual(stops[0],2)
        self.assertLess(stops[0],2.4)
        self.assertGreaterEqual(self.now,3.4)
        self.signals.assert_not_called()

    def test_tree_tracks_reparented_appid_and_excludes_old_other_uid_and_pid_reuse(self):
        self.proc.values.update({100:dict(pid=100,uid=1000,ppid=55,ticks=101,comm='proton'),
            101:dict(pid=101,uid=1000,ppid=100,ticks=102,comm='DiRT5.exe'),
            102:dict(pid=102,uid=1000,ppid=1,ticks=99,comm='DiRT5.exe'),
            103:dict(pid=103,uid=999,ppid=1,ticks=110,comm='DiRT5.exe')})
        tree=GameTree(100,1000,100,proc=self.proc)
        self.proc.apps.add(55)  # O launcher também herda SteamAppId; nunca deve rastrear/matar a si próprio.
        self.assertEqual({p['pid'] for p in tree.poll()[0]},{100,101})
        self.proc.values.pop(100);self.proc.values[101]['ppid']=1;self.proc.apps.add(101)
        self.assertTrue(tree.poll()[1])
        tree.discover=False;self.proc.values[101]['ticks']=200
        tree.terminate();self.signals.assert_not_called()
        self.assertEqual(tree.poll()[0],[])
