"""Nintendo Switch Pro Controller over Bluetooth Classic.

Protocol per dekuNukem's Nintendo_Switch_Reverse_Engineering, SDL's HIDAPI Switch
driver and Linux hid-nintendo. Unlike the other types the host talks to the
controller: it sends subcommands in output report 0x01 and waits for a 0x21 reply
(device info, reads of the calibration in SPI flash, input mode, IMU on/off).
The controller starts in simple mode (0x3F) until the host asks for full 0x30
reports. Face buttons follow the Switch positions: Deck A (bottom) is Switch B,
unless the "switch_deck_layout" option keeps the Deck's labels (Deck A is Switch A).
"""
import struct
from collections import deque
from dataclasses import replace

from ..deck import DeckInput
from .base import Battery, Encoder, Profile, hat_direction, stick_unit

REPORT_LEN = 49  # 0x21 and 0x30 including the report ID

DESCRIPTOR = bytes([
    0x05, 0x01, 0x09, 0x05, 0xA1, 0x01,
    0x06, 0x01, 0xFF,
    0x85, 0x21, 0x09, 0x21, 0x75, 0x08, 0x95, 0x30, 0x81, 0x02,  # input 0x21: subcommand reply
    0x85, 0x30, 0x09, 0x30, 0x75, 0x08, 0x95, 0x30, 0x81, 0x02,  # input 0x30: full state
    0x85, 0x31, 0x09, 0x31, 0x75, 0x08, 0x96, 0x69, 0x01, 0x81, 0x02,  # input 0x31: state + NFC/IR
    # Input 0x3F: simple state. 16 buttons, hat, 4 sticks (u16, centre 0x8000)
    0x85, 0x3F,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x10, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x10, 0x81, 0x02,
    0x05, 0x01, 0x09, 0x39, 0x15, 0x00, 0x25, 0x07, 0x75, 0x04, 0x95, 0x01, 0x81, 0x42,
    0x05, 0x09, 0x75, 0x04, 0x95, 0x01, 0x81, 0x01,
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x33, 0x09, 0x34,
    0x16, 0x00, 0x00, 0x27, 0xFF, 0xFF, 0x00, 0x00, 0x75, 0x10, 0x95, 0x04, 0x81, 0x02,
    # Output 0x01: rumble + subcommand, 0x10: rumble only, 0x11/0x12: NFC/IR MCU
    0x06, 0x01, 0xFF,
    0x85, 0x01, 0x09, 0x01, 0x75, 0x08, 0x95, 0x30, 0x91, 0x02,
    0x85, 0x10, 0x09, 0x10, 0x75, 0x08, 0x95, 0x30, 0x91, 0x02,
    0x85, 0x11, 0x09, 0x11, 0x75, 0x08, 0x95, 0x30, 0x91, 0x02,
    0x85, 0x12, 0x09, 0x12, 0x75, 0x08, 0x95, 0x30, 0x91, 0x02,
    0xC0,
])

# Sticks are 12 bit.
STICK_CENTER, STICK_RANGE = 0x800, 0x700
# IMU calibration of real controllers, for hosts that read it (no IMU data is sent).
ACCEL_SENS, GYRO_SENS = 16384, 13371
# Stick deadzone/range parameters, as in real controllers' flash.
STICK_PARAMS = bytes.fromhex("0f30619630f3d41454411554c7799c333663")
COLOURS = bytes([0x32, 0x32, 0x32, 0xFF, 0xFF, 0xFF, 0x32, 0x32, 0x32, 0x32, 0x32, 0x32])  # body, buttons, grips

# Subcommands (output report 0x01, byte 10)
SUB_DEVICE_INFO, SUB_INPUT_MODE, SUB_TRIGGER_ELAPSED = 0x02, 0x03, 0x04
SUB_HCI_STATE, SUB_SPI_READ = 0x06, 0x10
HCI_DISCONNECT = 0x00  # SUB_HCI_STATE argument: disconnect, sent by a Switch going to sleep


def _pack12(a: int, b: int) -> bytes:
    return bytes([a & 0xFF, (a >> 8) | ((b & 0x0F) << 4), b >> 4])


def _spi_flash() -> bytes:
    """The parts of the controller's SPI flash hosts read; unset bytes are 0xFF."""
    f = bytearray(b"\xFF" * 0x10000)
    f[0x6012] = 0x03  # device type: Pro Controller
    f[0x601B] = 0x01  # colours below are set
    f[0x6020:0x6038] = struct.pack("<12h", 0, 0, 0, ACCEL_SENS, ACCEL_SENS, ACCEL_SENS,
                                   0, 0, 0, GYRO_SENS, GYRO_SENS, GYRO_SENS)
    c, r = STICK_CENTER, STICK_RANGE
    # Left stick: above centre, centre, below centre. Right stick: centre, below, above.
    f[0x603D:0x6046] = _pack12(r, r) + _pack12(c, c) + _pack12(r, r)
    f[0x6046:0x604F] = _pack12(c, c) + _pack12(r, r) + _pack12(r, r)
    f[0x6050:0x605C] = COLOURS
    f[0x6080:0x6086] = bytes([0x50, 0xFD, 0x00, 0x00, 0xC6, 0x0F])  # IMU horizontal offsets
    f[0x6086:0x6098] = STICK_PARAMS
    f[0x6098:0x60AA] = STICK_PARAMS
    # 0x8010 and 0x8026 (user calibration) stay 0xFF: none, use the factory values.
    return bytes(f)


SPI_FLASH = _spi_flash()


def _stick(x: int, y: int) -> bytes:
    """Deck stick -> 3 packed bytes (12 bit x, y; +y up)."""
    fx, fy = stick_unit(x, y)
    return _pack12(max(0, min(0xFFF, round(STICK_CENTER + fx * STICK_RANGE))),
                   max(0, min(0xFFF, round(STICK_CENTER + fy * STICK_RANGE))))


def _zl_zr(d: DeckInput) -> tuple[bool, bool]:
    """The analog triggers as the Pro Controller's digital ZL/ZR."""
    return d.l2 or d.lt > 30 << 7, d.r2 or d.rt > 30 << 7


class SwitchProEncoder(Encoder):
    def __init__(self):
        self.timer = 0
        self.full = False  # host asked for full 0x30 reports
        self.replies: deque[tuple[int, int, bytes]] = deque()  # (ack, subcommand, data)
        self.deck_layout = False

    def set_options(self, options: dict):
        self.deck_layout = options.get("switch_deck_layout", False)

    def handle_output(self, msg: bytes, mac: bytes):
        # a2 01 <counter> <rumble x8> <subcommand> <args...>
        if len(msg) < 12 or msg[0] != 0xA2 or msg[1] != 0x01:
            return
        sub, args = msg[11], msg[12:]
        if sub == SUB_DEVICE_INFO:
            # firmware 4.33, Pro Controller, our address, colours from SPI
            self.replies.append((0x82, sub, bytes([0x04, 0x33, 0x03, 0x02]) + mac + bytes([0x01, 0x01])))
        elif sub == SUB_SPI_READ and len(args) >= 5:
            addr, size = struct.unpack_from("<IB", args)
            size = min(size, 0x1D)
            data = SPI_FLASH[addr:addr + size] if addr + size <= len(SPI_FLASH) else b"\xFF" * size
            self.replies.append((0x90, sub, args[:5] + data))
        elif sub == SUB_INPUT_MODE and args:
            self.full = args[0] != 0x3F
            self.replies.append((0x80, sub, b""))
        elif sub == SUB_TRIGGER_ELAPSED:
            self.replies.append((0x83, sub, b""))
        else:  # player/home lights, rumble enable, low power, ...: just acknowledge
            self.replies.append((0x80, sub, b""))

    def encode(self, d: DeckInput | None, battery: Battery) -> bytes:
        if d is not None and self.deck_layout:
            # swap so the position mapping below lands each Deck button on its own label
            d = replace(d, a=d.b, b=d.a, x=d.y, y=d.x)
        self.timer = (self.timer + 1) & 0xFF
        reply = self.replies.popleft() if self.replies else None
        if reply is None and not self.full:
            return self._simple(d)
        r = bytearray(REPORT_LEN)
        r[1] = self.timer
        # Battery: high nibble 8 full, 6 medium, 4 low, 2 critical, 0 empty; +1 charging
        p = battery.percent
        level = 8 if p > 75 else 6 if p > 50 else 4 if p > 25 else 2 if p > 10 else 0
        r[2] = (level | (1 if battery.charging else 0)) << 4
        r[6:9] = r[9:12] = _stick(0, 0)
        r[12] = 0x80  # vibrator report
        if d is not None:
            self._fill(r, d)
        if reply is not None:
            ack, sub, data = reply
            r[0] = 0x21
            r[13], r[14] = ack, sub
            r[15:15 + len(data)] = data
        else:
            r[0] = 0x30  # IMU samples stay zero: no gyro (hosts treat all-zero as no data)
        return bytes(r)

    def _fill(self, r: bytearray, d: DeckInput):
        zl, zr = _zl_zr(d)
        # Face buttons by position: Deck A/B/X/Y -> Switch B/A/Y/X
        r[3] = ((0x01 if d.x else 0) | (0x02 if d.y else 0) | (0x04 if d.a else 0) | (0x08 if d.b else 0)
                | (0x40 if d.r1 else 0) | (0x80 if zr else 0))
        r[4] = ((0x01 if d.view else 0) | (0x02 if d.menu else 0)
                | (0x04 if d.r3 else 0) | (0x08 if d.l3 else 0)
                | (0x10 if d.steam else 0) | (0x20 if d.l4 else 0))  # Home, Capture
        r[5] = ((0x01 if d.down else 0) | (0x02 if d.up else 0)
                | (0x04 if d.right else 0) | (0x08 if d.left else 0)
                | (0x40 if d.l1 else 0) | (0x80 if zl else 0))
        r[6:9] = _stick(d.lx, d.ly)
        r[9:12] = _stick(d.rx, d.ry)

    def _simple(self, d: DeckInput | None) -> bytes:
        """Report 0x3F, sent until the host switches to full reports."""
        r = bytearray(12)
        r[0] = 0x3F
        r[3] = 0x08  # hat centred
        struct.pack_into("<4H", r, 4, 0x8000, 0x8000, 0x8000, 0x8000)
        if d is None:
            return bytes(r)
        zl, zr = _zl_zr(d)
        buttons = ((0x0001 if d.a else 0) | (0x0002 if d.b else 0) | (0x0004 if d.x else 0)
                   | (0x0008 if d.y else 0) | (0x0010 if d.l1 else 0) | (0x0020 if d.r1 else 0)
                   | (0x0040 if zl else 0) | (0x0080 if zr else 0)
                   | (0x0100 if d.view else 0) | (0x0200 if d.menu else 0)
                   | (0x0400 if d.l3 else 0) | (0x0800 if d.r3 else 0)
                   | (0x1000 if d.steam else 0) | (0x2000 if d.l4 else 0))
        struct.pack_into("<H", r, 1, buttons)
        hat = hat_direction(d)
        r[3] = 8 if hat is None else hat
        lx, ly = stick_unit(d.lx, d.ly)
        rx, ry = stick_unit(d.rx, d.ry)
        axis = lambda v: max(0, min(0xFFFF, round(32768 + v * 32767)))
        struct.pack_into("<4H", r, 4, axis(lx), axis(-ly), axis(rx), axis(-ry))  # +y down
        return bytes(r)


def _rumble_amps(side: bytes) -> tuple[int, int]:
    """(low band, high band) amplitude codes 0..100 from one side's 4 HD Rumble bytes."""
    high = (side[1] & 0xFE) >> 1
    low = ((side[3] - 0x40) << 1) | (side[2] >> 7) if side[3] >= 0x40 else 0
    return min(100, low), min(100, high)


class SwitchPro(Profile):
    id = "switch_pro"
    label = "Switch Pro Controller"
    vendor_id = 0x057E
    product_id = 0x2009
    bt_name = "Pro Controller"
    service_name = "Wireless Gamepad"
    provider = "Nintendo"
    descriptor = DESCRIPTOR
    report_interval = 0.015  # real controllers send full reports every 15 ms
    host_sniff = True  # the switch sets 15 ms sniff right after connecting

    def new_encoder(self) -> Encoder:
        return SwitchProEncoder()

    def host_disconnect(self, msg: bytes) -> bool:
        # a2 01 <counter> <rumble x8> 06 00
        return (len(msg) >= 13 and msg[0] == 0xA2 and msg[1] == 0x01
                and msg[11] == SUB_HCI_STATE and msg[12] == HCI_DISCONNECT)

    def parse_rumble(self, msg: bytes) -> tuple[int, int, float | None] | None:
        # a2 01|10 <counter> <left rumble x4> <right rumble x4> ...
        # Low band -> the Deck's low frequency motor, high band -> high frequency.
        if len(msg) < 11 or msg[0] != 0xA2 or msg[1] not in (0x01, 0x10):
            return None
        (ll, lh), (rl, rh) = _rumble_amps(msg[3:7]), _rumble_amps(msg[7:11])
        return max(ll, rl) * 255 // 100, max(lh, rh) * 255 // 100, None
