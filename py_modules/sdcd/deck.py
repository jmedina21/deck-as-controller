"""Steam Deck built-in controller over usbfs.

While grabbed, usbhid is unbound from the controller's HID interfaces so Steam
(and the desktop) stop seeing it, and we read the gamepad interface directly.
"""
import ctypes
import errno
import fcntl
import glob
import os
import struct
import threading
import time
from dataclasses import dataclass, fields, replace

VALVE_VID, DECK_PID = "28de", "1205"
HID_INTERFACES = (0, 1, 2)  # keyboard, mouse, gamepad
GAMEPAD_IFACE, GAMEPAD_EP = 2, 0x83
USBHID = "/sys/bus/usb/drivers/usbhid"

USBDEVFS_CONTROL = 0xC0185500
USBDEVFS_BULK = 0xC0185502
USBDEVFS_CLAIMINTERFACE = 0x8004550F
USBDEVFS_RELEASEINTERFACE = 0x80045510

# Feature commands and settings registers (see linux drivers/hid/hid-steam.c)
ID_CLEAR_DIGITAL_MAPPINGS = 0x81
ID_SET_SETTINGS_VALUES = 0x87
ID_TRIGGER_RUMBLE_CMD = 0xEB
ID_TRIGGER_HAPTIC_PULSE = 0x8F
ID_TRIGGER_HAPTIC_CMD = 0xEA
SETTING_LIZARD_MODE = 9
SETTING_IMU_MODE = 48
SETTING_STEAM_WATCHDOG_ENABLE = 71
IMU_SEND_RAW_ACCEL_GYRO = 0x18

# Button bits in the 0x09 state report (bytes 8..15)
_L = {
    "r2": 0x1, "l2": 0x2, "r1": 0x4, "l1": 0x8,
    "y": 0x10, "b": 0x20, "x": 0x40, "a": 0x80,
    "up": 0x100, "right": 0x200, "left": 0x400, "down": 0x800,
    "view": 0x1000, "steam": 0x2000, "menu": 0x4000,
    "l5": 0x8000, "r5": 0x10000,
    "lpad_click": 0x20000, "rpad_click": 0x40000,
    "lpad_touch": 0x80000, "rpad_touch": 0x100000,
    "l3": 0x400000, "r3": 0x4000000,
}
_H = {"l4": 0x200, "r4": 0x400, "lstick_touch": 0x4000, "rstick_touch": 0x8000, "qam": 0x40000}


@dataclass
class DeckInput:
    a: bool = False
    b: bool = False
    x: bool = False
    y: bool = False
    up: bool = False
    down: bool = False
    left: bool = False
    right: bool = False
    l1: bool = False
    r1: bool = False
    l2: bool = False
    r2: bool = False
    l3: bool = False
    r3: bool = False
    l4: bool = False
    r4: bool = False
    l5: bool = False
    r5: bool = False
    view: bool = False
    menu: bool = False
    steam: bool = False
    qam: bool = False
    lpad_click: bool = False
    rpad_click: bool = False
    lpad_touch: bool = False
    rpad_touch: bool = False
    lstick_touch: bool = False
    rstick_touch: bool = False
    lx: int = 0
    ly: int = 0
    rx: int = 0
    ry: int = 0
    lt: int = 0
    rt: int = 0
    lpad_x: int = 0
    lpad_y: int = 0
    rpad_x: int = 0
    rpad_y: int = 0
    lpad_pressure: int = 0
    rpad_pressure: int = 0
    ax: int = 0
    ay: int = 0
    az: int = 0
    gx: int = 0
    gy: int = 0
    gz: int = 0

    @classmethod
    def parse(cls, d: bytes) -> "DeckInput | None":
        if len(d) < 60 or d[0] != 0x01 or d[2] != 0x09:
            return None
        bl, bh = struct.unpack_from("<II", d, 8)
        s = cls(**{k: bool(bl & m) for k, m in _L.items()}, **{k: bool(bh & m) for k, m in _H.items()})
        s.lpad_x, s.lpad_y, s.rpad_x, s.rpad_y = struct.unpack_from("<4h", d, 16)
        s.ax, s.ay, s.az, s.gx, s.gy, s.gz = struct.unpack_from("<6h", d, 24)
        s.lt, s.rt = struct.unpack_from("<HH", d, 44)
        s.lx, s.ly, s.rx, s.ry = struct.unpack_from("<4h", d, 48)
        s.lpad_pressure, s.rpad_pressure = struct.unpack_from("<HH", d, 56)
        return s


DIGITAL = [f.name for f in fields(DeckInput) if f.type is bool]

HAPTIC_OFF, HAPTIC_TICK, HAPTIC_CLICK, HAPTIC_TONE = 0, 1, 2, 3
HAPTIC_SCRIPT, HAPTIC_SWEEP = 6, 7


def haptic_pulse_cmd(pad: int, on_us: int, off_us: int, count: int) -> bytes:
    """Feature command: `count` pulses of `on_us` microseconds, `off_us` apart, on a trackpad.

    Pads are swapped on this report for legacy reasons: 1 = left, 0 = right, 2 = both.
    """
    return bytes([ID_TRIGGER_HAPTIC_PULSE, 8, pad]) + struct.pack("<HHHB", on_us, off_us, count, 0)


def haptic_cmd(pad: int, kind: int, gain_db: int = 0, freq: int = 0, duration_ms: int = 0,
               lfo_freq: int = 0, lfo_depth: int = 0, script: int = 0,
               sweep_start: int = 0, sweep_end: int = 0) -> bytes:
    """Feature command for the trackpad haptics' effects (HAPTIC_*), as Steam sends on the Deck.

    Here pad is 0 = left, 1 = right, 2 = both. gain_db is clamped to -24..6.
    """
    body = struct.pack("<BBBbHhHHBBBHH", pad, kind, 0, max(-24, min(6, gain_db)), freq,
                       min(0x7FFF, duration_ms), 0, lfo_freq, lfo_depth, 0, script,
                       sweep_start, sweep_end)
    return bytes([ID_TRIGGER_HAPTIC_CMD, len(body)]) + body


def _analog_key(s: DeckInput) -> tuple:
    """Non-IMU analog values at the resolution the host sees."""
    return (s.lx >> 8, s.ly >> 8, s.rx >> 8, s.ry >> 8, s.lt >> 7, s.rt >> 7,
            s.lpad_x >> 6, s.lpad_y >> 6, s.rpad_x >> 6, s.rpad_y >> 6)


class SharedInput:
    """Latest Deck state shared between the USB reader and the Bluetooth sender.

    Presses are latched until sent, so a tap shorter than a report interval
    still reaches the host. `urgent` marks changes other than IMU noise.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.latest: DeckInput | None = None
        self.latched: set[str] = set()
        self.urgent = False

    def update(self, s: DeckInput):
        with self.lock:
            prev = self.latest
            self.latest = s
            pressed = {n for n in DIGITAL if getattr(s, n)}
            if (prev is None or pressed != {n for n in DIGITAL if getattr(prev, n)}
                    or _analog_key(s) != _analog_key(prev)):
                self.urgent = True
            self.latched |= pressed

    def take(self) -> DeckInput | None:
        with self.lock:
            s = self.latest
            if s is None:
                return None
            out = replace(s, **{n: True for n in self.latched})
            self.latched = {n for n in DIGITAL if getattr(s, n)}
            self.urgent = False
            return out


def find_device() -> str | None:
    """sysfs path of the Deck controller USB device, e.g. /sys/bus/usb/devices/3-3."""
    for dev in glob.glob("/sys/bus/usb/devices/*"):
        try:
            if (open(f"{dev}/idVendor").read().strip() == VALVE_VID
                    and open(f"{dev}/idProduct").read().strip() == DECK_PID):
                return dev
        except OSError:
            continue
    return None


def rebind_all():
    """Give every Deck controller HID interface back to usbhid. Safe to call anytime."""
    dev = find_device()
    if not dev:
        return
    name = os.path.basename(dev)
    for i in HID_INTERFACES:
        iface = f"{name}:1.{i}"
        if os.path.exists(f"/sys/bus/usb/devices/{iface}") and not os.path.exists(f"{USBHID}/{iface}"):
            try:
                with open(f"{USBHID}/bind", "w") as f:
                    f.write(iface)
            except OSError:
                pass


class DeckController:
    """Exclusive access to the Deck controller. Use grab()/release()."""

    # The controller gets overloaded by rapid rumble commands; Steam and the kernel
    # driver throttle them to 20 Hz, always applying the latest values.
    RUMBLE_INTERVAL = 0.05

    def __init__(self):
        self.fd = None
        self.dev = None
        self.lock = threading.Lock()  # serializes control transfers
        self.rumble_wanted = (0, 0)
        self.rumble_applied = (0, 0)
        self.rumble_until: float | None = None  # monotonic time a timed rumble ends
        self.rumble_event = threading.Event()

    def grab(self):
        self.dev = find_device()
        if not self.dev:
            raise RuntimeError("Steam Deck controller not found")
        name = os.path.basename(self.dev)
        for i in HID_INTERFACES:
            iface = f"{name}:1.{i}"
            if os.path.exists(f"{USBHID}/{iface}"):
                with open(f"{USBHID}/unbind", "w") as f:
                    f.write(iface)
        bus = int(open(f"{self.dev}/busnum").read())
        devnum = int(open(f"{self.dev}/devnum").read())
        try:
            self.fd = os.open(f"/dev/bus/usb/{bus:03d}/{devnum:03d}", os.O_RDWR)
            fcntl.ioctl(self.fd, USBDEVFS_CLAIMINTERFACE, struct.pack("I", GAMEPAD_IFACE))
            self.configure()
            threading.Thread(target=self._rumble_loop, daemon=True, name="deck-rumble").start()
        except Exception:
            self.release()
            raise

    def configure(self):
        """Turn off keyboard/mouse emulation and the Steam watchdog; enable raw IMU."""
        self._feature(bytes([ID_CLEAR_DIGITAL_MAPPINGS]))
        self._settings((SETTING_LIZARD_MODE, 0),
                       (SETTING_STEAM_WATCHDOG_ENABLE, 0),
                       (SETTING_IMU_MODE, IMU_SEND_RAW_ACCEL_GYRO))

    def release(self):
        if self.fd is not None:
            try:
                self._send_rumble(0, 0)
            except OSError:
                pass
            try:
                fcntl.ioctl(self.fd, USBDEVFS_RELEASEINTERFACE, struct.pack("I", GAMEPAD_IFACE))
            except OSError:
                pass
            os.close(self.fd)
            self.fd = None
        rebind_all()

    def read(self, timeout_ms: int = 100) -> bytes | None:
        """One raw 64-byte report, or None on timeout. Raises OSError if the device is gone."""
        data = bytearray(64)
        buf = (ctypes.c_uint8 * 64).from_buffer(data)
        arg = bytearray(struct.pack("III4xQ", GAMEPAD_EP, 64, timeout_ms, ctypes.addressof(buf)))
        try:
            n = fcntl.ioctl(self.fd, USBDEVFS_BULK, arg)
        except OSError as e:
            if e.errno == errno.ETIMEDOUT:
                return None
            raise
        return bytes(data[:n])

    def rumble(self, low: int, high: int, duration: float | None = None):
        """Request motor levels 0..255 (low frequency / left, high frequency / right),
        optionally stopping by themselves after `duration` seconds."""
        self.rumble_wanted = (low, high)
        self.rumble_until = time.monotonic() + duration if duration and (low or high) else None
        self.rumble_event.set()

    def _rumble_loop(self):
        while self.fd is not None:
            until = self.rumble_until
            self.rumble_event.wait(timeout=max(0.0, until - time.monotonic()) if until else 1)
            self.rumble_event.clear()
            if self.rumble_until and time.monotonic() >= self.rumble_until:
                self.rumble_wanted, self.rumble_until = (0, 0), None
            wanted = self.rumble_wanted
            if wanted != self.rumble_applied:
                try:
                    self._send_rumble(*wanted)
                    self.rumble_applied = wanted
                except OSError:
                    pass
            time.sleep(self.RUMBLE_INTERVAL)

    def click_pulse(self, left: bool):
        """Short haptic tick on a trackpad, like Steam gives when a pad is clicked."""
        self.haptic(haptic_pulse_cmd(1 if left else 0, 1200, 0, 1))

    def haptic(self, cmd: bytes):
        """Play a trackpad haptic built by haptic_pulse_cmd() or haptic_cmd()."""
        self._feature(cmd)

    def _send_rumble(self, low: int, high: int):
        cmd = bytes([ID_TRIGGER_RUMBLE_CMD, 9, 0, 0, 0]) + struct.pack("<HHbb", low * 257, high * 257, 2, 0)
        self._feature(cmd)

    def _settings(self, *pairs):
        cmd = bytearray([ID_SET_SETTINGS_VALUES, 3 * len(pairs)])
        for reg, val in pairs:
            cmd += bytes([reg, val & 0xFF, val >> 8])
        self._feature(bytes(cmd))

    def _feature(self, cmd: bytes):
        data = bytearray(64)
        data[:len(cmd)] = cmd
        buf = (ctypes.c_uint8 * 64).from_buffer(data)
        # SET_REPORT, feature report 0, gamepad interface
        arg = struct.pack("BBHHHI4xQ", 0x21, 0x09, 0x0300, GAMEPAD_IFACE, 64, 500, ctypes.addressof(buf))
        with self.lock:
            if self.fd is None:
                return
            fcntl.ioctl(self.fd, USBDEVFS_CONTROL, arg)
