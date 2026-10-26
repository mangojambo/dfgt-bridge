"""Integração Steam offline; nenhum comando físico/systemd real é executado."""
import copy
import io
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
from dfgt_bridge.steam_launcher import (run_game, GameTree, IntegrationSettings, main,
    dfgt_connected, DFGT_INPUT, DFGT_MISSING_MESSAGE)


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

    def test_disabled_bridge_preserves_proton_and_other_environment(self):
        with patch.dict(os.environ, {'PROTON_ENABLE_HIDRAW':'user-value','STEAM_COMPAT_DATA_PATH':'/prefix here'}):
            run_game(['game','a b'],settings=None,automatic=False,client_factory=Mock(side_effect=AssertionError),
                spawn=self.spawn,proc=self.proc,clock=lambda:self.now,sleep=self.sleep)
        env=self.spawn.call_args.kwargs['env']
        self.assertEqual(env['PROTON_ENABLE_HIDRAW'],'user-value')
        self.assertEqual(env['STEAM_COMPAT_DATA_PATH'],'/prefix here')

    def test_game_exit_code_is_returned_without_bridge(self):
        self.process.poll.return_value=23
        self.process.wait.return_value=23
        self.assertEqual(run_game(['game'],settings=None,automatic=False,client_factory=Mock(),
            spawn=self.spawn,proc=self.proc,clock=lambda:self.now,sleep=self.sleep),23)

    def _main(self, args, *, detected, run_side_effect=None, run_return=0, enabled=True,
              profile_factory=None):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        class Preferences:
            def __init__(self, path=None): self.path=Path(temp.name)/'integration.json'
            def enabled(self): return enabled
        class Profiles:
            def __init__(self, path=None): self.path=Path(temp.name)/'profiles.json'
            def load(self, name): return default_profile()
        self.enterContext(patch.dict(os.environ, {'XDG_STATE_HOME':temp.name}))
        self.enterContext(patch('os.geteuid',return_value=1000))
        self.enterContext(patch('signal.signal',return_value=signal.SIG_DFL))
        detector_kwargs = ({'side_effect':detected} if isinstance(detected,(list,tuple)) or isinstance(detected,BaseException)
                           else {'return_value':detected})
        detector=self.enterContext(patch('dfgt_bridge.steam_launcher.dfgt_connected',**detector_kwargs))
        self.enterContext(patch('dfgt_bridge.steam_launcher.IntegrationSettings',Preferences))
        self.enterContext(patch('dfgt_bridge.steam_launcher.ProfileStore',Profiles if profile_factory is None else profile_factory))
        launcher=self.enterContext(patch('dfgt_bridge.steam_launcher.run_game',return_value=run_return,
                                          side_effect=run_side_effect))
        return main(args), launcher, detector

    def test_present_device_starts_automatic_bridge_for_original_argv(self):
        command=['--','/proton path','waitforexitandrun','DiRT 5.exe','--option','value with spaces']
        code, launcher, detector=self._main(command,detected=True,run_return=17)
        self.assertEqual(code,17)
        detector.assert_called_once_with()
        self.assertEqual(launcher.call_args.args[0],command[1:])
        self.assertTrue(launcher.call_args.kwargs['automatic'])

    def test_missing_device_skips_preferences_profile_and_bridge(self):
        command=['--','proton','waitforexitandrun','game with spaces.exe','arg with spaces']
        class Forbidden:
            def __init__(self,*args,**kwargs): raise AssertionError('profile settings must not be read')
        with patch('dfgt_bridge.steam_launcher.logging.info') as info:
            code, launcher, _=self._main(command,detected=False,profile_factory=Forbidden,run_return=6)
        self.assertEqual(code,6)
        self.assertEqual(launcher.call_args.args[0],command[1:])
        self.assertIsNone(launcher.call_args.kwargs['settings'])
        self.assertFalse(launcher.call_args.kwargs['automatic'])
        info.assert_any_call(DFGT_MISSING_MESSAGE)

    def test_absent_device_fallback_uses_no_service_and_preserves_game_code(self):
        self.process.poll.return_value=41;self.process.wait.return_value=41
        factory=Mock(side_effect=AssertionError('service must not start'))
        with patch('dfgt_bridge.steam_launcher.logging.info') as info:
            result=run_game(['proton','game arg'],settings=None,automatic=False,client_factory=factory,
                spawn=self.spawn,proc=self.proc,clock=lambda:self.now,sleep=self.sleep)
        self.assertEqual(result,41)
        factory.assert_not_called()
        self.assertEqual(self.spawn.call_args.args[0],['proton','game arg'])
        self.assertEqual(self.spawn.call_args.kwargs['env'],dict(os.environ))
        info.assert_called_once_with('Integração desativada explicitamente pelo usuário; jogo sem bridge')

    def test_device_detection_checks_stable_link_and_sysfs_identity_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);dev=root/'dev/input';sysfs=root/'sys/class/input';dev.mkdir(parents=True)
            node=dev/'event12';node.touch()
            stable=dev/'dfgt';stable.symlink_to(node)
            ids=sysfs/'event12/device/id';ids.mkdir(parents=True)
            (ids/'vendor').write_text('0x046d\n');(ids/'product').write_text('c29a\n')
            self.assertTrue(dfgt_connected(stable,sysfs=sysfs,device_root=dev))
            self.assertTrue(node.exists())
            (ids/'product').write_text('c24f\n')
            self.assertFalse(dfgt_connected(stable,sysfs=sysfs,device_root=dev))
            stable.unlink()
            self.assertFalse(dfgt_connected(stable,sysfs=sysfs,device_root=dev))

    def test_detection_permission_error_is_not_device_absence(self):
        with patch.object(Path,'resolve',side_effect=PermissionError(13,'denied')):
            with self.assertRaises(PermissionError): dfgt_connected('/unreadable/link')

    def test_service_failure_with_present_wheel_keeps_critical_diagnostic(self):
        command=['--','proton','game']
        failure=RuntimeError('PIDFF bind failed')
        failure.bridge_startup_failure=True
        stderr=self.enterContext(patch('sys.stderr',new_callable=io.StringIO))
        code, launcher, detector=self._main(command,detected=True,run_side_effect=failure)
        self.assertEqual(code,1)
        launcher.assert_called_once()
        self.assertTrue(launcher.call_args.kwargs['automatic'])
        self.assertIn('PIDFF bind failed',stderr.getvalue())
        detector.assert_called_once_with()

    def test_startup_permission_failure_is_not_reclassified_as_unplug(self):
        failure=PermissionError(13,'Permission denied')
        failure.bridge_startup_failure=True
        stderr=self.enterContext(patch('sys.stderr',new_callable=io.StringIO))
        code, launcher, detector=self._main(['--','proton','game'],detected=[True,False],
                                             run_side_effect=failure)
        self.assertEqual(code,1)
        launcher.assert_called_once()
        self.assertIn('Permission denied',stderr.getvalue())
        detector.assert_called_once_with()

    def test_unplug_during_startup_falls_back_only_for_confirmed_missing_device(self):
        failure=RuntimeError(f'origem=runner.io; caminho={DFGT_INPUT}; errno=2; No such file or directory')
        failure.bridge_startup_failure=True
        command=['--','proton','game arg']
        with patch('sys.stderr',new_callable=io.StringIO):
            code, launcher, detector=self._main(command,detected=[True,False],run_side_effect=[failure,0])
        self.assertEqual(code,0)
        self.assertEqual(detector.call_count,2)
        self.assertEqual(launcher.call_count,2)
        self.assertTrue(launcher.call_args_list[0].kwargs['automatic'])
        self.assertFalse(launcher.call_args_list[1].kwargs['automatic'])
        self.assertEqual(launcher.call_args_list[1].args[0],command[1:])

    def test_check_ready_does_not_use_automatic_fallback_detection(self):
        command=['--check-ready']
        code, launcher, detector=self._main(command,detected=AssertionError('not run'),run_return=0,enabled=True)
        self.assertEqual(code,0)
        detector.assert_not_called()
        self.assertTrue(launcher.call_args.kwargs['passive'])

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
