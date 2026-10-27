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
from dfgt_bridge.steam_protocol import approved, ready, encode, receive, peer, ControlClient
from dfgt_bridge.steam_service import SessionManager, DeviceLock, RootRunner


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.proc = FakeProc()
        self.factory = Mock(side_effect=FakeRunner)
        self.lock = Mock()
        self.manager = SessionManager(1000, self.factory, proc=self.proc, lock_factory=lambda: self.lock)
        self.credentials = dict(uid=1000, pid=55)
    def request(self, op, connection=1, now=0, **data):
        return self.manager.request(self.credentials, connection, dict(v=1, op=op, **data), now)
    def start(self, **kwargs):
        return self.request('start', profile='dirt5', mode='normal', detailed=False, settings=default_profile(), **kwargs)

    def test_only_authorized_unprivileged_uid_can_start(self):
        for uid in (0, 999, 1001):
            with self.subTest(uid=uid), self.assertRaises(PermissionError):
                self.manager.request(dict(uid=uid,pid=55),1,dict(v=1,op='status'),0)
        self.factory.assert_not_called()
        self.start()
        self.assertEqual(self.factory.call_args.kwargs['identity']['uid'],1000)

    def test_startup_waits_for_actual_pidff_evdev_and_physical_ready(self):
        self.assertEqual(self.start()['state'],'starting')
        runner = self.manager.runner
        invalid = copy.deepcopy(READY);invalid['binding']['driver']='unbound'
        runner.state=invalid;self.manager.tick(.1)
        self.assertFalse(ready(self.request('ping',now=.2),True))
        runner.state=copy.deepcopy(READY);self.manager.tick(.3)
        self.assertTrue(ready(self.request('ping',now=.4),True))

    def test_pidff_failure_preserves_reason_and_stops_without_ready(self):
        self.start();runner=self.manager.runner
        runner.state=dict(state='failed',error='logitech -22; sem PIDFF/EVDEV')
        self.manager.tick(.1)
        self.assertTrue(runner.stops)
        self.assertIn('-22',self.request('ping',now=.2)['error'])
        runner.code=1;self.manager.tick(.3)
        self.assertIn('-22',self.request('ping',now=.4)['error'])
        self.assertTrue(runner.closed);self.lock.close.assert_called_once()

    def test_second_instance_cannot_start_or_renew_first_lease(self):
        self.start()
        with self.assertRaisesRegex(RuntimeError,'ocupado'): self.start(connection=2)
        with self.assertRaises(PermissionError): self.request('ping',connection=2,now=2)
        self.assertEqual(self.factory.call_count,1)
        self.assertEqual(self.manager.heartbeat,0)

    def test_disconnect_and_heartbeat_expiry_stop_immediately(self):
        self.start();runner=self.manager.runner
        self.manager.disconnected(2,.1);self.assertFalse(runner.stops)
        self.manager.disconnected(1,.2);self.assertEqual(runner.stops,[.2])
        self.assertEqual(self.manager.state['reason'],'launcher_disconnected')

    def test_gui_status_does_not_renew_stalled_launcher(self):
        self.start();runner=self.manager.runner
        self.request('status',connection=2,now=2.9)
        self.manager.tick(3)
        self.assertTrue(runner.stops)
        self.assertIn('lease',self.manager.state['reason'])

    def test_launcher_pid_reuse_invalidates_lease(self):
        self.start();runner=self.manager.runner
        self.proc.values[55]['ticks']=200
        self.manager.tick(.1)
        self.assertTrue(runner.stops)

    def test_readiness_timeout_is_distinct_from_unlimited_game_session(self):
        self.start();self.request('ping',now=40);self.manager.tick(40)
        self.assertEqual(self.manager.state['reason'],'readiness_timeout')
        self.assertIsNone(self.factory.call_args.args[0]['ffb_seconds'])

    def test_play_close_and_start_again_reuses_no_old_effect_state(self):
        self.start();old=self.manager.runner
        self.request('stop',now=1);old.code=0;self.manager.tick(2)
        self.assertTrue(old.closed)
        self.start(connection=2,now=3)
        self.assertIsNot(self.manager.runner,old)
        self.assertEqual(self.manager.state['state'],'starting')

    def test_rpc_rejects_shell_paths_unknown_fields_and_unapproved_profiles(self):
        for data in (dict(v=1,op='exec',command='id'),
                     dict(v=1,op='status',argv=['sh']),
                     dict(v=1,op='start',profile='other',mode='normal',detailed=False,settings=default_profile())):
            with self.subTest(data=data),self.assertRaises(ValueError):
                self.manager.request(self.credentials,1,data,0)
        for change in ({'ffb_strength':10},{'ffb_polarity':1},{'ffb_seconds':120}):
            with self.subTest(change=change),self.assertRaises(ValueError): approved(dict(default_profile(),**change))
        bad=default_profile();bad['mapping']['bindings']['key:293']='button:1'
        with self.assertRaises(ValueError): approved(bad)
        self.factory.assert_not_called()

    def test_passive_probe_never_requests_physical_factory_mode(self):
        self.request('start',profile='dirt5',mode='passive',settings=default_profile(),detailed=False)
        self.assertFalse(self.factory.call_args.kwargs['physical'])
        self.assertFalse(self.factory.call_args.kwargs['detailed'])

    def test_service_revalidates_and_forwards_explicit_dfgt_calibration(self):
        profile = default_profile()
        profile.pop('ffb_spring_gain')
        profile['ffb_effect_gains'] = {'spring': 2}
        self.request('start', profile='dirt5', mode='normal', settings=profile, detailed=False)
        self.assertEqual(self.factory.call_args.args[0]['ffb_spring_gain'], 2.0)
        self.assertEqual(self.factory.call_args.args[0]['ffb_effect_gains'], {})
        self.assertEqual(self.factory.call_args.args[0]['ffb_strength'], 100)
        self.assertEqual(self.factory.call_args.args[0]['ffb_polarity'], -1)

    def test_unlimited_session_remains_ready_while_valid_launcher_renews(self):
        self.start();runner=self.manager.runner;runner.state=copy.deepcopy(READY)
        for second in range(1,601):
            self.request('ping',now=second);self.manager.tick(second)
        self.assertIs(self.manager.runner,runner)
        self.assertFalse(runner.stops)
        self.assertTrue(ready(self.manager.state,True))

    def test_device_lock_prevents_independent_service_reservations(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'device.lock';one=DeviceLock(path)
            try:
                with self.assertRaises(RuntimeError): DeviceLock(path)
            finally:one.close()
            two=DeviceLock(path);two.close()


class ProtocolTests(unittest.TestCase):
    def test_client_verifies_root_server_credentials(self):
        channel=Mock();channel.getsockopt.return_value=struct.pack('3i',1,0,0)
        channel.send.side_effect=lambda data:len(data)
        channel.recvmsg.return_value=(encode(dict(ok=True,state='idle')),[],0,None)
        client=ControlClient(factory=lambda *a:channel)
        self.assertEqual(client.request('status')['state'],'idle');client.close()
        channel.getsockopt.return_value=struct.pack('3i',99,1000,1000)
        with self.assertRaises(PermissionError):ControlClient(factory=lambda *a:channel)

    def test_seqpacket_codec_and_peercred_wire_abi_with_fake_socket(self):
        channel=Mock()
        channel.recvmsg.return_value=(encode(dict(v=1,op='status')),[],0,None)
        channel.getsockopt.return_value=struct.pack('3i',55,1000,1000)
        self.assertEqual(receive(channel),dict(v=1,op='status'))
        self.assertEqual(peer(channel),dict(pid=55,uid=1000,gid=1000))
        channel.getsockopt.assert_called_with(socket.SOL_SOCKET,socket.SO_PEERCRED,12)

    def test_oversized_or_non_object_protocol_is_rejected(self):
        with self.assertRaises(ValueError):encode({'x':'a'*70000})
        channel=Mock();channel.recvmsg.return_value=(b'[]',[],0,None)
        with self.assertRaises(ValueError):receive(channel)
        channel.recvmsg.return_value=(b'{}',[],socket.MSG_TRUNC,None)
        with self.assertRaises(ValueError):receive(channel)


class RootRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory=self.enterContext(tempfile.TemporaryDirectory())
        self.process=Mock(pid=999999)
        self.process.stdin.fileno.return_value=90;self.process.stdout.fileno.return_value=91
        self.process.stdin.closed=False;self.process.poll.return_value=None
        self.popen=self.enterContext(patch('subprocess.Popen',return_value=self.process))
        self.enterContext(patch('os.set_blocking'))
        self.signals=self.enterContext(patch('os.killpg'))
        self.writes=self.enterContext(patch('os.write'))
        self.enterContext(patch('os.read',side_effect=BlockingIOError))
        self.parent,self.child=Mock(),Mock()
        self.child.fileno.return_value=92
        self.parent.recvmsg.side_effect=BlockingIOError
        self.enterContext(patch('socket.socketpair',return_value=(self.parent,self.child)))
    def make(self,physical=True,detailed=False,profile=None):
        return RootRunner(default_profile() if profile is None else profile,physical=physical,detailed=detailed,
            identity=dict(uid=1000,ticks=100),runtime='/trusted/runtime',log_dir=self.directory,run_dir=self.directory)

    def test_live_spring_selection_needs_no_launcher_flags_or_private_profile_writes(self):
        from dfgt_bridge.supervised import SupervisedPolicy
        from dfgt_bridge.backends.mock import MockBackend
        profile = dict(default_profile(), ffb_spring_gain=1.25, ffb_effect_gains={'damper': 1.5})
        child = self.make(profile=profile)
        path = child.directory/'profiles.json'
        before = path.read_bytes()
        saved = ProfileStore(path).load('dirt5')
        for _ in range(2):
            policy = SupervisedPolicy(MockBackend(), effect_gains=dict(saved['ffb_effect_gains'], spring=saved['ffb_spring_gain']))
            self.assertEqual(policy.spring_selected, 1.25)
            policy.apply('spring_select', dict(key='F6'), 0)
            self.assertEqual(policy.spring_selected, 4)
            policy.apply('spring_select', dict(key='F5'), 1)
            self.assertEqual(policy.spring_selected, 3)
            policy.apply('close', {}, 1)
        self.assertEqual(path.read_bytes(), before)
        self.assertIn('--enable-ffb', self.popen.call_args.args[0])
        self.assertNotIn('--ffb-spring-gain', self.popen.call_args.args[0])
        self.process.poll.return_value = 0
        child.close()

    def test_root_child_is_fixed_hardware_runner_not_steam_or_user_code(self):
        value=self.make()
        saved = json.loads((value.directory/'profiles.json').read_text())['profiles']['dirt5']
        self.assertEqual(saved['ffb_spring_gain'], 3.0)
        command=self.popen.call_args.args[0];kwargs=self.popen.call_args.kwargs
        self.assertEqual(command[:4],['/usr/bin/python3','-I','-B','/trusted/runtime/src/dfgt_bridge/main.py'])
        self.assertIn('--enable-ffb',command);self.assertIn('--operational-log',command)
        self.assertIn('--game-uid',command);self.assertIn('--game-after-ticks',command)
        self.assertNotIn('--capture',command);self.assertNotIn('PYTHONPATH',kwargs['env'])
        self.assertFalse((Path(self.directory)/'captures').exists())
        value.stop(0);self.writes.assert_called_once_with(90,b'ffb-stop\nquit\n')
        self.process.poll.return_value=0;value.close()
        self.assertFalse(value.directory.exists())

    def test_stop_escalation_is_bounded_and_passive_child_has_no_ffb(self):
        value=self.make(physical=False)
        self.assertNotIn('--enable-ffb',self.popen.call_args.args[0])
        value.stop(0);value.poll(1.1);value.poll(2.1)
        self.assertIn(((999999,signal.SIGTERM),),[(c.args,) for c in self.signals.call_args_list])
        self.assertIn(((999999,signal.SIGKILL),),[(c.args,) for c in self.signals.call_args_list])
        self.process.poll.return_value=0;value.close()

    def test_explicit_capture_stays_in_service_directory(self):
        value=self.make(detailed=True)
        self.assertIn('--capture',self.popen.call_args.args[0])
        self.assertEqual(Path(value.capture).parent,Path(self.directory)/'captures')
        self.process.poll.return_value=0;value.close()

    def test_renderer_stall_is_reported_even_if_process_is_alive(self):
        value=self.make();value.state=copy.deepcopy(READY)
        value.poll(0);value.poll(1.5)
        self.assertEqual(value.state['state'],'failed')
        self.assertIn('progresso',value.state['error'])
        self.process.poll.return_value=0;value.close()

    def test_private_profile_is_read_from_run_even_when_home_is_denied(self):
        original = Path.open
        def restricted(path, *args, **kwargs):
            if str(path).startswith(('/home/', '/root/')):
                raise PermissionError(13, 'denied', str(path))
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', restricted):
            value = self.make(physical=False)
            config = value.directory/'profiles.json'
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(ProfileStore(config).load(), default_profile())
            self.assertIn(str(config), self.popen.call_args.args[0])
            self.process.poll.return_value = 0
            value.close()

    def test_spawn_permission_error_identifies_fixed_executable_and_cleans_session(self):
        self.popen.side_effect = PermissionError(13, 'denied')
        with self.assertRaisesRegex(OSError, 'service.runner.spawn.*main.py.*errno=13'):
            self.make(physical=False)
        self.assertEqual(list(Path(self.directory).glob('session-*')), [])

    def test_socketpair_denial_cleans_private_profile(self):
        with patch('socket.socketpair', side_effect=PermissionError(13, 'denied')):
            with self.assertRaisesRegex(OSError, 'service.runner.socketpair.*errno=13'):
                self.make(physical=False)
        self.assertEqual(list(Path(self.directory).glob('session-*')), [])
