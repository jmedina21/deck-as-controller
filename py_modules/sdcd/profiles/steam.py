"""Valve Steam Controller (2026) in Bluetooth mode.

The Deck has the same controls, so Steam on the host gets everything: both
sticks and trackpads, the four back buttons, gyro and trackpad haptics.

Report layouts follow SDL's HIDAPI Steam Controller driver and the Linux
hid-steam driver: input report 0x45 for state and 0x43 for battery, output
reports 0x80.. for haptics, and feature report 0x01 for commands. A command is
written with SET_REPORT and its reply is read back with GET_REPORT.

The real controller is a Bluetooth LE device; here the same reports are served
over Bluetooth Classic like the other controller types.
"""
import struct
import time

from ..deck import (HAPTIC_SCRIPT, HAPTIC_SWEEP, HAPTIC_TONE, DeckInput, haptic_cmd,
                    haptic_pulse_cmd)
from .base import Battery, Encoder, Profile, radial_deadzone

REPORT_STATE = 0x45  # 45 bytes after the ID: state without the orientation quaternion
REPORT_BATTERY = 0x43
REPORT_FEATURE = 0x01
FEATURE_LEN = 63  # after the ID

OUT_RUMBLE, OUT_PULSE, OUT_COMMAND = 0x80, 0x81, 0x82
OUT_LFO_TONE, OUT_LOG_SWEEP, OUT_SCRIPT = 0x83, 0x84, 0x85

# Feature commands (the same family the Deck's own controller uses, see deck.py)
ID_GET_ATTRIBUTES_VALUES = 0x83
ID_SET_SETTINGS_VALUES = 0x87
ID_GET_SETTINGS_VALUES = 0x89
ID_TRIGGER_HAPTIC_PULSE = 0x8F
ID_GET_STRING_ATTRIBUTE = 0xAE
ID_TRIGGER_RUMBLE_CMD = 0xEB

# (tag, value) pairs for GET_ATTRIBUTES_VALUES, as a real controller answers.
# The firmware build time is far in the future so Steam never offers to update
# the "controller's" firmware.
ATTRIBUTES = (
    (1, 0x1302),  # product id
    (2, 0),  # capabilities
    (10, 0x68D2F92E),  # bootloader build time
    (4, 0x7FFFFFFF),  # firmware build time
    (9, 72),  # hardware id
)
SERIAL_LEN = 20
SERIAL_PREFIX = "FXDK"

# The real controller stops rumbling ~50 ms after the last rumble report, and
# hosts repeat the report while it should run. Allow for Bluetooth jitter.
RUMBLE_TIMEOUT = 0.5

# The haptic output reports mirror the Deck controller's own haptic commands, so
# they are passed on as such. Their "side" also counts the real controller's
# grip motors (3 left, 4 right, 5 both); the Deck only has the trackpads.
_PULSE_PADS = {0: 0, 1: 1, 2: 2, 3: 1, 4: 0, 5: 2}  # pulse: 0 right, 1 left, 2 both
_PADS = {0: 0, 1: 1, 2: 2, 3: 0, 4: 1, 5: 2}  # the others: 0 left, 1 right, 2 both

_BUTTONS = {
    "a": 0x00000001, "b": 0x00000002, "x": 0x00000004, "y": 0x00000008,
    "r3": 0x00000020, "menu": 0x00000040, "r4": 0x00000080,
    "r5": 0x00000100, "r1": 0x00000200, "down": 0x00000400, "right": 0x00000800,
    "left": 0x00001000, "up": 0x00002000, "view": 0x00004000, "l3": 0x00008000,
    "steam": 0x00010000, "l4": 0x00020000, "l5": 0x00040000, "l1": 0x00080000,
    "rstick_touch": 0x00100000, "rpad_touch": 0x00200000,
    "rpad_click": 0x00400000, "r2": 0x00800000,
    "lstick_touch": 0x01000000, "lpad_touch": 0x02000000,
    "lpad_click": 0x04000000, "l2": 0x08000000,
}


def _reports(item: int, *reports: tuple[int, int]) -> bytes:
    """Descriptor items declaring vendor reports as (report ID, length after the ID)."""
    return b"".join(bytes([0x85, rid, 0x09, 0x01, 0x95, length, item, 0x02]) for rid, length in reports)


# One vendor-defined collection of byte arrays, like the real controller's.
DESCRIPTOR = (
    bytes([0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08])
    # Input: state with quaternion, battery, state, state with pad timestamp, wireless status
    + _reports(0x81, (0x42, 53), (REPORT_BATTERY, 14), (REPORT_STATE, 45), (0x47, 45), (0x79, 1))
    # Output: rumble, pulse, command, tone, sweep, script, haptic audio stream
    + _reports(0x91, (OUT_RUMBLE, 9), (OUT_PULSE, 7), (OUT_COMMAND, 3), (OUT_LFO_TONE, 9),
               (OUT_LOG_SWEEP, 8), (OUT_SCRIPT, 3), (0x86, 3), (0x87, 63), (0x88, 63), (0x89, 63))
    + _reports(0xB1, (REPORT_FEATURE, FEATURE_LEN))
    + bytes([0xC0])
)


def _stick(x: int, y: int, deadzone: float) -> tuple[int, int]:
    fx, fy = radial_deadzone(x, y, deadzone)
    return round(fx * 32767), round(fy * 32767)


class SteamEncoder(Encoder):
    def __init__(self, deadzone: float):
        self.deadzone = deadzone
        self.counter = 0

    def encode(self, d: DeckInput | None, battery: Battery) -> bytes:
        r = bytearray(46)
        r[0] = REPORT_STATE
        r[1] = self.counter
        self.counter = (self.counter + 1) & 0xFF
        struct.pack_into("<I", r, 30, int(time.monotonic() * 1_000_000) & 0xFFFFFFFF)
        if d is None:
            return bytes(r)

        buttons = 0
        for name, mask in _BUTTONS.items():
            if getattr(d, name):
                buttons |= mask
        # Sticks, pads and IMU use the Deck's own units and axes.
        struct.pack_into("<IhhhhhhhhHhhH", r, 2, buttons, min(32767, d.lt), min(32767, d.rt),
                         *_stick(d.lx, d.ly, self.deadzone), *_stick(d.rx, d.ry, self.deadzone),
                         d.lpad_x, d.lpad_y, d.lpad_pressure,
                         d.rpad_x, d.rpad_y, d.rpad_pressure)
        struct.pack_into("<6h", r, 34, d.ax, d.ay, d.az, d.gx, d.gy, d.gz)
        return bytes(r)


class SteamController(Profile):
    id = "steam_controller"
    label = "Steam Controller (trackpads, back buttons)"
    vendor_id = 0x28DE
    product_id = 0x1303
    bt_name = "Steam Controller"
    service_name = "Steam Controller"
    provider = "Valve"
    descriptor = DESCRIPTOR
    # Pass on every report of the Deck's controller (one per 4 ms), so trackpads
    # and gyro are as smooth as on the Deck itself.
    report_interval = 0.003
    host_haptics = True

    def __init__(self):
        self.command = bytes(2)  # last feature command from the host, without the report ID
        self.settings: dict[int, int] = {}

    def new_encoder(self, deadzone: float) -> Encoder:
        return SteamEncoder(deadzone)

    def set_feature(self, report: bytes):
        if len(report) < 3 or report[0] != REPORT_FEATURE:
            return
        self.command = bytes(report[1:])
        if self.command[0] == ID_SET_SETTINGS_VALUES:
            # <register> <value: u16>, repeated. Nothing to apply: the daemon
            # already runs the Deck's controller the way Steam asks for here.
            values = self.command[2:2 + self.command[1]]
            for i in range(0, len(values) - 2, 3):
                self.settings[values[i]] = values[i + 1] | values[i + 2] << 8

    def feature_report(self, report_id: int, mac: bytes) -> bytes | None:
        if report_id != REPORT_FEATURE:
            return None
        cmd, args = self.command[0], self.command[2:2 + self.command[1]]
        if cmd == ID_GET_ATTRIBUTES_VALUES:
            reply = b"".join(struct.pack("<BI", tag, value) for tag, value in ATTRIBUTES)
        elif cmd == ID_GET_STRING_ATTRIBUTE and args:
            # Board and unit serial numbers: stable per Deck, from its Bluetooth address.
            serial = f"{SERIAL_PREFIX}{int.from_bytes(mac, 'big') % 10 ** 8:08d}"
            reply = args[:1] + serial.encode().ljust(SERIAL_LEN, b"\0")
        elif cmd == ID_GET_SETTINGS_VALUES:
            reply = b"".join(struct.pack("<BH", reg, self.settings.get(reg, 0)) for reg in args[::3])
        else:
            return bytes([REPORT_FEATURE]) + self.command[:FEATURE_LEN].ljust(FEATURE_LEN, b"\0")
        return (bytes([REPORT_FEATURE, cmd, len(reply)]) + reply).ljust(1 + FEATURE_LEN, b"\0")

    def parse_rumble(self, msg: bytes) -> tuple[int, int, float | None] | None:
        # a2 80    <type> <intensity: u16> <left: u16> <gain> <right: u16> <gain>
        # a3 01 eb <len> <type> <intensity: u16> <left: u16> <right: u16> <gain> <gain>
        if len(msg) >= 10 and msg[0] == 0xA2 and msg[1] == OUT_RUMBLE:
            left, right = struct.unpack_from("<HxH", msg, 5)
        elif len(msg) >= 11 and msg[:3] == bytes([0xA3, REPORT_FEATURE, ID_TRIGGER_RUMBLE_CMD]):
            left, right = struct.unpack_from("<HH", msg, 7)
        else:
            return None
        return left >> 8, right >> 8, RUMBLE_TIMEOUT

    def parse_haptic(self, msg: bytes) -> bytes | None:
        if len(msg) >= 11 and msg[:3] == bytes([0xA3, REPORT_FEATURE, ID_TRIGGER_HAPTIC_PULSE]):
            # a3 01 8f <len> <pad> <on> <off> <count>: what the Deck's controller takes itself
            return haptic_pulse_cmd(min(msg[4], 2), *struct.unpack_from("<HHH", msg, 5))
        if len(msg) < 5 or msg[0] != 0xA2:
            return None
        report, pad = msg[1], _PADS.get(msg[2], 2)
        if report == OUT_PULSE and len(msg) >= 9:  # a2 81 <side> <on: u16> <off: u16> <count: u16>
            return haptic_pulse_cmd(_PULSE_PADS.get(msg[2], 2), *struct.unpack_from("<HHH", msg, 3))
        if report == OUT_COMMAND:  # a2 82 <side> <0 stop, 1 click, 2 strong click> <gain: dB>
            return haptic_cmd(pad, min(msg[3], 2), struct.unpack_from("<b", msg, 4)[0])
        if report == OUT_LFO_TONE and len(msg) >= 11:
            # a2 83 <side> <gain> <Hz: u16> <ms: u16> <lfo Hz: u16> <lfo depth>
            gain, freq, duration_ms, lfo_freq, lfo_depth = struct.unpack_from("<bHHHB", msg, 3)
            return haptic_cmd(pad, HAPTIC_TONE, gain, freq, duration_ms, lfo_freq, lfo_depth)
        if report == OUT_LOG_SWEEP and len(msg) >= 10:  # a2 84 <side> <gain> <ms> <from Hz> <to Hz>
            gain, duration_ms, start, end = struct.unpack_from("<bHHH", msg, 3)
            return haptic_cmd(pad, HAPTIC_SWEEP, gain, duration_ms=duration_ms,
                              sweep_start=start, sweep_end=end)
        if report == OUT_SCRIPT:  # a2 85 <side> <script> <gain>
            return haptic_cmd(pad, HAPTIC_SCRIPT, struct.unpack_from("<b", msg, 4)[0], script=msg[3])
        return None

    def side_reports(self, battery: Battery) -> list[bytes]:
        # Report 0x43: <charge state: 1 discharging, 2 charging, 4 full> <percent>
        # then voltages, currents and temperature, which we leave at zero.
        state = 4 if battery.charging and battery.percent >= 100 else 2 if battery.charging else 1
        return [bytes([REPORT_BATTERY, state, max(0, min(100, battery.percent))]).ljust(15, b"\0")]
