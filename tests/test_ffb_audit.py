"""Auditoria offline da v0.13. Nunca importa o bridge nem abre dispositivos.

Executar: python3 -B -m unittest discover -s tests -v
expectedFailure identifica uma divergência conhecida, ainda NÃO corrigida.
Os casos com CSV são capturas existentes; vetores adicionais são sintéticos.
"""
import ast
from collections import Counter
import csv
import math
from pathlib import Path
import struct
import sys
import time
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
BASE = ROOT / 'reference/v0.8/dfgt_uhid_g29_probe_v08_driving.py'
LIVE = ROOT / 'reference/v0.13/dfgt_uhid_g29_ffb_v013_live.py'
FIXTURES = ROOT / 'tests/fixtures/dirt5'


def load_reviewed_definitions(path, physical_mock=False):
    """Executa só definições auditadas; exclui imports, main, run e binding.

    Não disponibiliza os, InputDevice, evdev, list_devices ou qualquer fd.
    O backend, quando solicitado, recebe apenas construtores de dados falsos.
    Não é um sandbox para código arbitrário: aplica-se aos fontes auditados.
    """
    names = {'PID_DESCRIPTOR', 'PID_BLOCK_FREE', 'BUTTON_CODES', 'BUTTON_BITS',
             'OUTPUT_COMPONENTS', 'PID_EFFECT_NAMES', 'descriptor', 'report',
             'PIDPassive', 'PIDSteeringBridge'}
    if physical_mock:
        names.add('PhysicalConstantMotor')
    selected = []
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            selected.append(node)
        elif isinstance(node, ast.Assign) and all(
                isinstance(target, ast.Name) and target.id in names
                for target in node.targets):
            selected.append(node)
    def data(*args, **kwargs):
        return SimpleNamespace(args=args, **kwargs)
    namespace = dict(Counter=Counter, struct=struct, math=math, time=time,
                     e=SimpleNamespace(EV_FF=0x15, FF_CONSTANT=0x52),
                     ff=SimpleNamespace(**{name: data for name in (
                         'Effect', 'Trigger', 'Replay', 'EffectType',
                         'Constant', 'Envelope')}))
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
    return SimpleNamespace(**namespace)


from dfgt_bridge.ffb.effects import PIDPassive, PIDSteeringBridge
from dfgt_bridge.ffb.safety import ConstantOutput

M = SimpleNamespace(PIDPassive=PIDPassive, PIDSteeringBridge=PIDSteeringBridge,
                    PhysicalConstantMotor=ConstantOutput)


class FakeDevice:
    info = SimpleNamespace(vendor=0x046d, product=0xc29a)

    def __init__(self):
        self.actions = []

    def capabilities(self):
        return {0x15: [0x52]}

    def upload_effect(self, effect):
        self.actions.append(('upload', effect))
        return 7

    def write(self, *args):
        self.actions.append(('write', args))

    def syn(self):
        self.actions.append(('syn',))

    def erase_effect(self, effect_id):
        self.actions.append(('erase', effect_id))


class OfflineAudit(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.bridge = M.PIDSteeringBridge(clock=lambda: self.now)
        self.bridge.arm()

    def create(self, kind=1):
        self.bridge.set_report(bytes((0x54, kind, 0, 0)), 0x54)
        return self.bridge.last_block

    def start(self, block, loops=1):
        self.bridge.seen(bytes((0x5a, block, 1, loops)))

    def constant(self, level=16384):
        block = self.create()
        self.bridge.seen(bytes((0x55, block)) + struct.pack('<h', level))
        self.start(block)
        return block

    def effect(self, block, kind=1, duration=0xffff, direction=9000):
        self.bridge.seen(bytes((0x51, block, kind)) + struct.pack(
            '<HHHHBBBHH', duration, 0, 0, 0, 100, 255, 4, direction, 0))

    def spring(self, coeff=32767, saturation=65535):
        block = self.create(9)
        self.bridge.seen(bytes((0x53, block, 0)) + struct.pack(
            '<hhhHHH', 0, coeff, coeff, saturation, saturation, 0))
        self.start(block)
        return block

    def motor(self, strength=20):
        dev = FakeDevice()
        motor = M.PhysicalConstantMotor(dev, strength, 45, clock=lambda: self.now)
        return motor, dev

    def test_v08_input_contract_unchanged_in_all_ffb_versions(self):
        baseline = load_reviewed_definitions(BASE)
        base_tree = ast.parse(BASE.read_text())
        versions = sorted((ROOT / 'archive/experiments/ffb').rglob('dfgt_uhid_g29_ffb_v0*.py')) + [LIVE]
        self.assertEqual(len(versions), 5)
        for path in versions:
            with self.subTest(version=path.name):
                other = load_reviewed_definitions(path)
                self.assertEqual(other.descriptor(), baseline.descriptor())
                tree = ast.parse(path.read_text())
                for name in ('report', 'create_device', 'send_input'):
                    a = next(n for n in base_tree.body if getattr(n, 'name', None) == name)
                    b = next(n for n in tree.body if getattr(n, 'name', None) == name)
                    self.assertEqual(ast.dump(a), ast.dump(b))
                for steering in (0, 8192, 16383):
                    state = (steering, 32, 170, 1, -1, {288, 300, 712})
                    for axis in ('z', 'y', 'both'):
                        for invert in (False, True):
                            self.assertEqual(other.report(state, axis, invert),
                                             baseline.report(state, axis, invert))

    def test_real_csv_handshake_and_no_running_torque(self):
        files = sorted(FIXTURES.glob('ffb*.csv'))
        self.assertEqual(len(files), 5)
        total = 0
        for path in files:
            bridge = M.PIDSteeringBridge(clock=lambda: self.now)
            bridge.arm()
            with path.open() as handle:
                rows = list(csv.DictReader(handle))
            for row in rows:
                total += 1
                self.now = int(row['timestamp_ns']) / 1e9
                packet = bytes.fromhex(row['hex_bytes'])
                if row['kind'] == 'SET_REPORT':
                    bridge.set_report(packet, int(row['report_num']))
                elif row['kind'] == 'OUTPUT':
                    bridge.seen(packet)
                elif row['kind'] == 'GET_REPORT':
                    self.assertEqual(bridge.get_report(int(row['report_num'])), packet)
                else:
                    self.fail('Tipo de captura inesperado')
                self.assertEqual(bridge.raw_level(), 0, path.name)
            self.assertFalse(any(x['running'] for x in bridge.effects.values()))
        self.assertEqual(total, 132)

    def test_real_csv_contains_no_race_effect_parameters_or_start(self):
        ids = Counter()
        for path in FIXTURES.glob('ffb*.csv'):
            with path.open() as handle:
                for row in csv.DictReader(handle):
                    ids[int(row['hex_bytes'][:2], 16)] += 1
        self.assertEqual(sum(ids.values()), 132)
        self.assertTrue({0x50, 0x54, 0x56, 0x57, 0x59, 0x5b} <= ids.keys())
        self.assertFalse({0x51, 0x52, 0x53, 0x55, 0x58, 0x5a, 0x5d} & ids.keys())

    def test_synthetic_constant_stop_reset_and_gain(self):
        block = self.constant()
        self.assertEqual(self.bridge.raw_level(), 16384)
        self.bridge.seen(bytes((0x59, 0)))
        self.assertEqual(self.bridge.raw_level(), 0)
        self.bridge.seen(bytes((0x59, 255)))
        self.bridge.seen(bytes((0x5a, block, 3, 0)))
        self.assertEqual(self.bridge.raw_level(), 0)
        self.start(block)
        self.bridge.seen(bytes((0x50, 4)))
        self.assertEqual(self.bridge.effects, {})

    def test_mock_emergency_stop_and_slot_cleanup(self):
        motor, dev = self.motor()
        motor.tick(32767)
        self.assertTrue(motor.playing)
        motor.set_enabled(False)
        self.assertIn(('write', (0x15, 7, 0)), dev.actions)
        self.now += .1
        before = motor.writes
        motor.tick(32767)
        self.assertEqual(motor.writes, before)
        motor.close()
        self.assertIn(('erase', 7), dev.actions)

    def test_gap_infinite_effect_survives_host_silence(self):
        block = self.constant()
        self.effect(block)
        self.now += .201
        self.assertEqual(self.bridge.raw_level(), 16384)

    def test_gap_condition_coefficient_sign_is_preserved(self):
        block = self.spring()
        positive = self.bridge.raw_level(12000)
        self.bridge.seen(bytes((0x53, block, 0)) + struct.pack(
            # Full scale negativo é -32768, conforme Logical Minimum.
            '<hhhHHH', 0, -32768, -32768, 65535, 65535, 0))
        self.assertEqual(self.bridge.raw_level(12000), -positive)

    def test_gap_unsigned_saturation_is_normalized(self):
        self.spring(saturation=16384)  # 25% do máximo unsigned 65535
        self.assertAlmostEqual(abs(self.bridge.raw_level(16383)), 8192, delta=2)

    def test_gap_direction_changes_constant_projection(self):
        block = self.constant()
        self.effect(block, direction=9000)
        right = self.bridge.raw_level()
        self.effect(block, direction=27000)
        self.assertLess(right * self.bridge.raw_level(), 0)

    @unittest.expectedFailure
    def test_gap_pause_continue_preserves_running_effects(self):
        self.constant()
        self.bridge.seen(bytes((0x50, 5)))
        self.assertEqual(self.bridge.raw_level(), 0)
        self.bridge.seen(bytes((0x50, 6)))
        self.assertEqual(self.bridge.raw_level(), 16384)

    @unittest.expectedFailure
    def test_gap_output_report_works_via_set_report_transport(self):
        block = self.constant()
        self.bridge.set_report(bytes((0x55, block)) + struct.pack('<h', 8000), 0x55)
        self.assertEqual(self.bridge.raw_level(), 8000)

    @unittest.expectedFailure
    def test_gap_loop_count_repeats_finite_effect(self):
        block = self.constant()
        self.effect(block, duration=50)
        self.start(block, loops=3)
        self.now += .075
        self.assertEqual(self.bridge.raw_level(), 16384)

    @unittest.expectedFailure
    def test_gap_start_restarts_effect_timeline(self):
        block = self.constant()
        self.now += .05
        self.start(block)
        self.assertEqual(self.bridge.effects[block]['started_at'], self.now)

    def test_gap_envelope_attack_level_is_absolute(self):
        block = self.constant(level=16384)
        self.bridge.seen(bytes((0x52, block)) + struct.pack('<HHHH', 8192, 0, 100, 0))
        self.assertEqual(self.bridge.raw_level(), 8192)

    @unittest.expectedFailure
    def test_gap_pool_exhaustion_does_not_overwrite_live_blocks(self):
        for _ in range(40):
            self.create()
        self.create()
        self.assertEqual(self.bridge.get_report(0x56)[2], 2)  # Block Load Full

    @unittest.expectedFailure
    def test_gap_no_clipping_before_cross_family_cancellation(self):
        self.constant(32767)
        self.constant(32767)
        self.spring()
        self.assertEqual(self.bridge.raw_level(16383), 32767)

    def test_gap_reducing_intensity_obeys_new_cap_immediately(self):
        motor, _ = self.motor(strength=60)
        motor.tick(32767)
        self.now += .04
        motor.tick(32767)
        motor.set_strength(5)
        self.now += .04
        motor.tick(32767)
        self.assertLessEqual(abs(motor.last_sent), motor.cap)

    @unittest.expectedFailure
    def test_gap_unchanged_output_does_not_repeat_upload(self):
        motor, dev = self.motor()
        for _ in range(3):  # Deixa o slew inicial estabilizar.
            motor.tick(32767)
            self.now += .04
        before = sum(x[0] == 'upload' for x in dev.actions)
        motor.tick(32767)  # Dentro do replay de 160 ms; keepalive ainda é separável.
        self.assertEqual(sum(x[0] == 'upload' for x in dev.actions), before)


if __name__ == '__main__':
    unittest.main()
