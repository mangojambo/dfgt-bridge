"""Saída do jogo e reutilização de PID verificadas com um /proc sintético."""
from pathlib import Path
import tempfile
import unittest

from test_ffb_audit import ROOT
from dfgt_bridge.backends.mock import MockBackend
from dfgt_bridge.ffb.effects import OUTPUT_COMPONENTS
from dfgt_bridge.game_monitor import GameMonitor
from dfgt_bridge.supervised import SupervisedPolicy


class GameMonitorTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)

    def process(self, pid, comm='DIRT5.exe', start=123, state='S'):
        path = self.root / str(pid)
        path.mkdir(exist_ok=True)
        path.joinpath('comm').write_text(comm+'\n')
        fields = [state] + ['0']*18 + [str(start)] + ['0']*4
        path.joinpath('stat').write_text(f'{pid} ({comm}) '+' '.join(fields))

    def test_only_dirt5_process_is_selected_and_exit_detected(self):
        self.process(1, 'steam')
        self.process(2, 'DIRT5.exe')
        monitor = GameMonitor(root=self.root)
        self.assertEqual(monitor.poll(0), (2, 123))
        self.process(2, 'DIRT5.exe', state='Z')
        self.assertIsNone(monitor.poll(1))

    def test_pid_reuse_never_counts_as_same_game(self):
        self.process(2)
        monitor = GameMonitor(2, root=self.root)
        self.assertEqual(monitor.poll(0), (2, 123))
        self.process(2, start=456)
        self.assertIsNone(monitor.poll(1))
        self.assertEqual(monitor.poll(2), (2, 456))

    def test_unknown_explicit_pid_does_not_enable_force(self):
        self.process(2, 'proton')
        self.assertIsNone(GameMonitor(2, root=self.root).poll(0))

    def test_physical_policy_waits_for_game_and_latches_on_exit(self):
        policy = SupervisedPolicy(MockBackend(), require_game=True, polarity=1)
        self.addCleanup(lambda: policy.apply('close', {}, policy.now_ns) if not policy.closed else None)
        frame = dict(generation=1, calculated_ns=1, components=dict.fromkeys(OUTPUT_COMPONENTS, 1000))
        policy.apply('frame', frame, 1)
        self.assertEqual(policy.output.writes, 0)
        policy.apply('game', dict(running=True), 2)
        frame.update(generation=2, calculated_ns=3)
        self.assertGreater(policy.apply('frame', frame, 3)['evdev_level'], 0)
        policy.apply('game', dict(running=False), 4)
        self.assertTrue(policy.latched)
        self.assertEqual(policy.output.last_sent, 0)
        policy.apply('arm', dict(requested_ns=5), 5)
        self.assertTrue(policy.latched)
        policy.apply('game', dict(running=True), 6)
        frame.update(generation=3, calculated_ns=7)
        self.assertEqual(policy.apply('frame', frame, 7)['evdev_level'], 0)

