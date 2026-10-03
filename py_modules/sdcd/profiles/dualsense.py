"""Sony DualSense / DualSense Edge over Bluetooth (full 0x31 reports)."""
import struct
import time
import zlib

from ..deck import DeckInput
from .base import Battery, Encoder, Profile, hat_direction, radial_deadzone

INPUT_REPORT_LEN = 78  # report 0x31 including report ID and CRC
TOUCH_W, TOUCH_H = 1920, 1080

# DualSense-like Bluetooth HID descriptor: simple report 0x01, full input/output
# report 0x31 and the vendor feature reports hosts read during initialization.
DESCRIPTOR = bytes([
    0x05, 0x01, 0x09, 0x05, 0xA1, 0x01,
    # Report 0x01: simple state (9 bytes)
    0x85, 0x01,
    0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x09, 0x35,
    0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x04, 0x81, 0x02,
    0x09, 0x39, 0x15, 0x00, 0x25, 0x07, 0x35, 0x00, 0x46, 0x3B, 0x01, 0x65, 0x14,
    0x75, 0x04, 0x95, 0x01, 0x81, 0x42, 0x65, 0x00,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x0E, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x0E, 0x81, 0x02,
    0x06, 0x00, 0xFF, 0x09, 0x20, 0x15, 0x00, 0x25, 0x3F, 0x75, 0x06, 0x95, 0x01, 0x81, 0x02,
    0x05, 0x01, 0x09, 0x33, 0x09, 0x34, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x02, 0x81, 0x02,
    # Vendor reports
    0x06, 0x00, 0xFF, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08,
    0x85, 0x31, 0x09, 0x31, 0x95, 0x4D, 0x81, 0x02,  # input 0x31, 77 bytes
    0x85, 0x31, 0x09, 0x3B, 0x95, 0x4D, 0x91, 0x02,  # output 0x31, 77 bytes
    0x85, 0x05, 0x09, 0x33, 0x95, 0x28, 0xB1, 0x02,  # feature 0x05 calibration
    0x85, 0x08, 0x09, 0x34, 0x95, 0x2F, 0xB1, 0x02,
    0x85, 0x09, 0x09, 0x24, 0x95, 0x13, 0xB1, 0x02,  # feature 0x09 pairing info
    0x85, 0x20, 0x09, 0x26, 0x95, 0x3F, 0xB1, 0x02,  # feature 0x20 firmware info
    0x85, 0x22, 0x09, 0x40, 0x95, 0x3F, 0xB1, 0x02,
    0xC0,
])

# A real DualSense's USB HID descriptor (github.com/nondebug/dualsense): input 0x01
# (64 bytes, the same state block as Bluetooth 0x31), output 0x02 and feature reports.
USB_DESCRIPTOR = bytes([
    0x05, 0x01, 0x09, 0x05, 0xA1, 0x01, 0x85, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x09, 0x35,
    0x09, 0x33, 0x09, 0x34, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x06, 0x81, 0x02, 0x06,
    0x00, 0xFF, 0x09, 0x20, 0x95, 0x01, 0x81, 0x02, 0x05, 0x01, 0x09, 0x39, 0x15, 0x00, 0x25, 0x07,
    0x35, 0x00, 0x46, 0x3B, 0x01, 0x65, 0x14, 0x75, 0x04, 0x95, 0x01, 0x81, 0x42, 0x65, 0x00, 0x05,
    0x09, 0x19, 0x01, 0x29, 0x0F, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x0F, 0x81, 0x02, 0x06,
    0x00, 0xFF, 0x09, 0x21, 0x95, 0x0D, 0x81, 0x02, 0x06, 0x00, 0xFF, 0x09, 0x22, 0x15, 0x00, 0x26,
    0xFF, 0x00, 0x75, 0x08, 0x95, 0x34, 0x81, 0x02, 0x85, 0x02, 0x09, 0x23, 0x95, 0x2F, 0x91, 0x02,
    0x85, 0x05, 0x09, 0x33, 0x95, 0x28, 0xB1, 0x02, 0x85, 0x08, 0x09, 0x34, 0x95, 0x2F, 0xB1, 0x02,
    0x85, 0x09, 0x09, 0x24, 0x95, 0x13, 0xB1, 0x02, 0x85, 0x0A, 0x09, 0x25, 0x95, 0x1A, 0xB1, 0x02,
    0x85, 0x20, 0x09, 0x26, 0x95, 0x3F, 0xB1, 0x02, 0x85, 0x21, 0x09, 0x27, 0x95, 0x04, 0xB1, 0x02,
    0x85, 0x22, 0x09, 0x40, 0x95, 0x3F, 0xB1, 0x02, 0x85, 0x80, 0x09, 0x28, 0x95, 0x3F, 0xB1, 0x02,
    0x85, 0x81, 0x09, 0x29, 0x95, 0x3F, 0xB1, 0x02, 0x85, 0x82, 0x09, 0x2A, 0x95, 0x09, 0xB1, 0x02,
    0x85, 0x83, 0x09, 0x2B, 0x95, 0x3F, 0xB1, 0x02, 0x85, 0x84, 0x09, 0x2C, 0x95, 0x3F, 0xB1, 0x02,
    0x85, 0x85, 0x09, 0x2D, 0x95, 0x02, 0xB1, 0x02, 0x85, 0xA0, 0x09, 0x2E, 0x95, 0x01, 0xB1, 0x02,
    0x85, 0xE0, 0x09, 0x2F, 0x95, 0x3F, 0xB1, 0x02, 0x85, 0xF0, 0x09, 0x30, 0x95, 0x3F, 0xB1, 0x02,
    0x85, 0xF1, 0x09, 0x31, 0x95, 0x3F, 0xB1, 0x02, 0x85, 0xF2, 0x09, 0x32, 0x95, 0x0F, 0xB1, 0x02,
    0x85, 0xF4, 0x09, 0x35, 0x95, 0x3F, 0xB1, 0x02, 0x85, 0xF5, 0x09, 0x36, 0x95, 0x03, 0xB1, 0x02,
    0xC0,
])

# Deck IMU: gyro 16.384 LSB/(deg/s), accel 16384 LSB/g.
# The advertised calibration makes gyro pass through 1:1 and accel at half scale.
GYRO_PLUS, GYRO_SPEED, ACCEL_PLUS = 8847, 540, 8192


def _with_crc(seed: int, report: bytearray) -> bytes:
    """Fill the trailing 4 bytes with the DualSense Bluetooth CRC32."""
    crc = zlib.crc32(bytes([seed]) + bytes(report[:-4]))
    struct.pack_into("<I", report, len(report) - 4, crc)
    return bytes(report)


def _stick(x: int, y: int, deadzone: float) -> tuple[int, int]:
    """Deck stick -> DualSense bytes (0..255, +y down)."""
    fx, fy = radial_deadzone(x, y, deadzone)
    return (max(0, min(255, round(128 + fx * 127.5))),
            max(0, min(255, round(128 - fy * 127.5))))


def _touch(touching: bool, touch_id: int, x: int, y: int, left_half: bool) -> bytes:
    """One DualSense touch point from a Deck trackpad, mapped onto half the touchpad."""
    if not touching:
        return bytes([0x80 | touch_id, 0, 0, 0])
    half = TOUCH_W // 2
    tx = int((x + 32768) / 65536 * half) + (0 if left_half else half)
    ty = int((32767 - y) / 65536 * TOUCH_H)
    tx, ty = max(0, min(TOUCH_W - 1, tx)), max(0, min(TOUCH_H - 1, ty))
    return bytes([touch_id & 0x7F, tx & 0xFF, ((tx >> 8) & 0x0F) | ((ty & 0x0F) << 4), ty >> 4])


class DualSenseEncoder(Encoder):
    def __init__(self, deadzone: float, edge: bool):
        self.deadzone = deadzone
        self.edge = edge
        self.counter = 0
        self.touch_ids = [0, 1]  # incremented on each new contact
        self.was_touching = [False, False]

    def encode(self, d: DeckInput | None, battery: Battery) -> bytes:
        r = bytearray(INPUT_REPORT_LEN)
        r[0] = 0x31
        r[1] = (self.counter << 4) & 0xF0
        st = memoryview(r)[2:65]  # the 63-byte common state block
        st[0:4] = bytes([128, 128, 128, 128])
        st[7] = 0x08  # hat neutral
        st[6] = self.counter
        st[32:40] = bytes([0x80, 0, 0, 0, 0x81, 0, 0, 0])  # no touches
        # Battery: low nibble level 0..10, high nibble 0 discharging / 1 charging / 2 full
        level = min(10, battery.percent // 10)
        status = 2 if battery.charging and battery.percent >= 100 else 1 if battery.charging else 0
        st[52] = (status << 4) | level
        struct.pack_into("<I", st, 27, int(time.monotonic() * 3_000_000) & 0xFFFFFFFF)
        self.counter = (self.counter + 1) & 0xFF

        if d is not None:
            self._fill(st, d)
        return _with_crc(0xA1, r)

    def _fill(self, st: memoryview, d: DeckInput):
        st[0], st[1] = _stick(d.lx, d.ly, self.deadzone)
        st[2], st[3] = _stick(d.rx, d.ry, self.deadzone)
        st[4], st[5] = min(255, d.lt >> 7), min(255, d.rt >> 7)

        hat = hat_direction(d)
        st[7] = ((8 if hat is None else hat)
                 | (0x10 if d.x else 0) | (0x20 if d.a else 0)
                 | (0x40 if d.b else 0) | (0x80 if d.y else 0))
        st[8] = ((0x01 if d.l1 else 0) | (0x02 if d.r1 else 0)
                 | (0x04 if d.l2 or st[4] > 30 else 0) | (0x08 if d.r2 or st[5] > 30 else 0)
                 | (0x10 if d.view else 0) | (0x20 if d.menu else 0)
                 | (0x40 if d.l3 else 0) | (0x80 if d.r3 else 0))
        st[9] = ((0x01 if d.steam else 0)
                 | (0x02 if d.lpad_click or d.rpad_click else 0))
        if self.edge:
            # Upper back buttons -> function buttons, lower back buttons -> paddles
            st[9] |= ((0x10 if d.l4 else 0) | (0x20 if d.r4 else 0)
                      | (0x40 if d.l5 else 0) | (0x80 if d.r5 else 0))

        # Deck axes -> DualSense axes: (x, z, -y)
        struct.pack_into("<3h", st, 15, d.gx, d.gz, max(-32768, min(32767, -d.gy)))
        struct.pack_into("<3h", st, 21, d.ax // 2, d.az // 2, max(-32768, min(32767, -d.ay // 2)))

        for i, (touching, x, y) in enumerate(((d.lpad_touch, d.lpad_x, d.lpad_y),
                                              (d.rpad_touch, d.rpad_x, d.rpad_y))):
            if touching and not self.was_touching[i]:
                self.touch_ids[i] = (self.touch_ids[i] + 2) & 0x7F
            self.was_touching[i] = touching
            st[32 + 4 * i:36 + 4 * i] = _touch(touching, self.touch_ids[i], x, y, left_half=(i == 0))


class DualSense(Profile):
    vendor_id = 0x054C
    bt_name = "DualSense Wireless Controller"
    service_name = "Wireless Controller"
    provider = "Sony Interactive Entertainment"
    descriptor = DESCRIPTOR
    usb_descriptor = USB_DESCRIPTOR

    def __init__(self, edge: bool = False):
        self.edge = edge
        self.id = "dualsense_edge" if edge else "dualsense"
        self.label = "PS5 Edge (back buttons)" if edge else "PS5"
        self.product_id = 0x0DF2 if edge else 0x0CE6
        if edge:
            self.bt_name = "DualSense Edge Wireless Controller"

    def new_encoder(self, deadzone: float) -> Encoder:
        return DualSenseEncoder(deadzone, self.edge)

    def feature_report(self, report_id: int, mac: bytes) -> bytes | None:
        if report_id == 0x05:
            r = bytearray(41)
            r[0] = 0x05
            for axis in range(3):
                struct.pack_into("<hh", r, 7 + axis * 4, GYRO_PLUS, -GYRO_PLUS)
            struct.pack_into("<hh", r, 19, GYRO_SPEED, GYRO_SPEED)
            for axis in range(3):
                struct.pack_into("<hh", r, 23 + axis * 4, ACCEL_PLUS, -ACCEL_PLUS)
        elif report_id == 0x09:
            r = bytearray(20)
            r[0] = 0x09
            r[1:7] = mac[::-1]  # little-endian
        elif report_id == 0x20:
            r = bytearray(64)
            r[0] = 0x20
            r[1:20] = b"Jun 19 202314:47:34"
            r[24:32] = bytes([0x03, 0x00, 0x04, 0x00, 0x03, 0x06, 0x01, 0x01])
            r[44:46] = bytes([0x30, 0x06])
        else:
            return None
        return _with_crc(0xA3, r)

    def parse_rumble(self, msg: bytes) -> tuple[int, int, float | None] | None:
        # Two layouts are in use for the common block after a2 31:
        #   macOS, Linux, SDL3: <seq_tag: seq << 4> <tag 0x10> <common...>
        #   SDL2:               <0x02>                         <common...>
        # common = <valid_flag0> <valid_flag1> <motor_right> <motor_left> ...
        if len(msg) < 8 or msg[0] != 0xA2 or msg[1] != 0x31:
            return None
        sdl2 = bool(msg[2] & 0x0F)
        common = 3 if sdl2 else 4
        if not msg[common] & 0x03:  # neither compatible vibration nor haptics select
            # SDL2 clears the flags when it stops rumble; others mean "not about rumble".
            return (0, 0, None) if sdl2 else None
        return msg[common + 3], msg[common + 2], None

    def usb_report(self, report: bytes) -> bytes:
        # USB report 0x01 is the Bluetooth 0x31 state block without the sequence byte and CRC.
        return b"\x01" + report[2:65]

    def parse_usb_rumble(self, msg: bytes) -> tuple[int, int, float | None] | None:
        # 02 <valid_flag0> <valid_flag1> <motor_right> <motor_left> ...
        if len(msg) < 5 or msg[0] != 0x02 or not msg[1] & 0x03:
            return None
        return msg[4], msg[3], None
