# SPDX-License-Identifier: GPL-2.0-only
"""Layout copiado da v0.8; mudanças exigem equivalência byte a byte."""
import struct

VENDOR, PRODUCT = 0x046d, 0xc24f
NAME = "Logitech G29 Driving Force Racing Wheel"

BUTTON_CODES = list(range(288, 304)) + list(range(704, 713))


BUTTON_BITS = {code: bit for bit, code in enumerate(BUTTON_CODES)}


PID_DESCRIPTOR = bytes.fromhex("""
35004500050F0992A1028550099F09A0099415002501750195088102092215012528750795018102C00921A10285510922150125287508950191020925A10209260927092809300931093209330934094009410942094315012512750895019100C009500954095109A7150026FF7F66031055FD7510950491025500660000095215002664007508950191020953150026FF007508950191020955A1020B300001000B3100010015002501750195029102C009567501950191027505950191030957A1020B01000A000B02000A0066140055FE1500273C8C00007510950291025500660000C0C0050F095AA1028552092215012528750895019102095B095D150026FF7F461027751095029102095C095E66031055FD26FF7F75109502910245006600005500C0095FA102855309221501252875089501910209231500250175089501910209600961096216008026FF7F36F0D8461027751095039102096309640965150027FFFF000035004610277510950391024500C0096EA102855D0922150125287508950191020970150026FF7F3500461027751095019102096F16008026FF7F36F0D84610277510950191023500450009711500273C8C000066140055FE7510950191020972150026FF7F66031055FD75109501910265005500C00973A1028555092215012528750895019102097016008026FF7F36F0D846102775109501910235004500C0050F0977A102855A09221501252835014528750895019102350045000978A1020979097A097B15012503750895019100C0097C150026FF0046FF0091024500C00996A1028550099709980999099A099B099C15012506750895019100C009ABA10285540925A1020926092709280930093109320933093409400941094209431501251275089501B100C00501093B150026FF0146FF01750A9501B1027506B1014500C0050F0989A10285560922252815013501452875089501B102098BA102098C098D098E250315013501450375089501B100C009AC150027FFFF0000350047FFFF000075109501B1004500C0097FA1028557098075109501150027FFFF0000B102098326FF0075089501B10209A909AA7501950215002501B10275069501B103C0097DA1028559097E26FF00750895019102C00974A102855809221501252875089501910209750976150026FF00750895029102C0
""")


PID_BLOCK_FREE = bytes.fromhex('050F0990A102855B092215012528750895019102C0')


def descriptor():
    """v0.4 joystick layout + numbered input report + PID FFB outputs/features."""
    # Preserve all the v0.4 axis usages and bit layout. Report ID 1 added.
    original = bytes([
        0x05, 0x01, 0x09, 0x04, 0xA1, 0x01,  # Generic Desktop / Joystick App
        0x09, 0x01, 0xA1, 0x00, 0x09, 0x30,
        0x15, 0x00, 0x27, 0xFF, 0xFF, 0x00, 0x00,
        0x75, 0x10, 0x95, 0x01, 0x81, 0x02,
        0x09, 0x32, 0x09, 0x35, 0x09, 0x31,
        0x15, 0x00, 0x26, 0xFF, 0x00,
        0x75, 0x08, 0x95, 0x03, 0x81, 0x02,
        0xC0,
        0x09, 0x39, 0x15, 0x00, 0x25, 0x07,
        0x35, 0x00, 0x46, 0x3B, 0x01, 0x65, 0x14,
        0x75, 0x04, 0x95, 0x01, 0x81, 0x42,
        0x65, 0x00, 0x75, 0x04, 0x95, 0x01, 0x81, 0x03,
        0x05, 0x09, 0x19, 0x01, 0x29, 0x19,
        0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x19, 0x81, 0x02,
        0x75, 0x01, 0x95, 0x07, 0x81, 0x03,
        0xC0,  # End joystick Application
    ])
    # v0.8: Add Automobile Simulation Device as a nested HID logical
    # collection around the *original* axes. Wine checks this collection
    # usage to classify the DirectInput device as DI8DEVTYPE_DRIVING.
    # No Input/Output/Feature fields are added, and not a single input bit
    # changes position. v0.6 remains available for immediate rollback.
    sim_car = bytes((0x05, 0x02,  # Usage Page: Simulation Controls
                     0x09, 0x02,  # Usage: Automobile Simulation Device
                     0xA1, 0x02,  # Collection: Logical (nested in Joystick)
                     0x05, 0x01)) # Restore Generic Desktop axes page
    layout = original[6:-1]
    axis_collection_end = bytes((0xC0, 0x09, 0x39))
    pos = layout.find(axis_collection_end)
    assert pos >= 0, "Missing physical axes end + Hat Switch"
    # Place automobile collection around the existing pointer collection.
    # We close it immediately after the pointer collection, so the hat and
    # buttons (and all PID reports) retain the same scope as in v0.6.
    layout = sim_car + layout[:pos + 1] + b'\xC0' + layout[pos + 1:]
    return original[:6] + b'\x85\x01' + layout + PID_DESCRIPTOR + PID_BLOCK_FREE + original[-1:]


def report(state, gas_axis='z', invert_gas=False):
    steering, accel, brake, hatx, haty, pressed = state
    gas = 255 - accel if invert_gas else accel
    # G29-like report layout remains X, Z, Rz, Y. Test alternative
    # throttle axes while leaving the working brake Rz unchanged.
    z_gas = gas if gas_axis in ('z', 'both') else 255
    y_gas = gas if gas_axis in ('y', 'both') else 255
    x = min(65535, max(0, round(steering * 65535 / 16383)))
    hat = {
        (0, -1): 0, (1, -1): 1, (1, 0): 2, (1, 1): 3,
        (0, 1): 4, (-1, 1): 5, (-1, 0): 6, (-1, -1): 7,
    }.get((hatx, haty), 8)
    buttons = 0
    for code in pressed:
        if code in BUTTON_BITS:
            buttons |= 1 << BUTTON_BITS[code]
    return b'\x01' + struct.pack('<HBBBBI', x, z_gas, brake, y_gas, hat, buttons)


def create_event(*, phys=''):
    if not isinstance(phys, str) or '\0' in phys or len(phys.encode('ascii')) > 63:
        raise ValueError('Identificador UHID deve ser ASCII, sem NUL, até 63 bytes')
    rd = descriptor()
    body = NAME.encode()[:127].ljust(128, b"\0") + phys.encode('ascii').ljust(64, b'\0') + bytes(64)
    body += struct.pack("=HHIIII", len(rd), 3, VENDOR, PRODUCT, 0x0110, 0)
    return struct.pack("=I", 11) + body + rd


def input_event(data):
    if not 1 <= len(data) <= 4096:
        raise ValueError("Tamanho de Input inválido")
    return struct.pack("=IH", 12, len(data)) + data + bytes(4096-len(data))
