"""Wired mode: the Deck as a USB controller (USB gadget) instead of Bluetooth.

The Deck's USB-C port can only act as a USB device when the BIOS option "USB Dual
Role Device" is DRD. That option's form value (variable `Setup`) is write-protected,
but the firmware applies the copy in `AmdSetup` at boot, which is writable from the
OS. So the switch is: change one byte there, then restart. The byte's position is
specific to each BIOS version, so only known versions can be switched from here.
"""
import fcntl
import logging
import os
import select
import shutil
import struct
import threading
from pathlib import Path
from typing import Callable

from .profiles import Profile

log = logging.getLogger("sdcd.usb")

# ---- the DRD switch -------------------------------------------------------

AMDSETUP = Path("/sys/firmware/efi/efivars/AmdSetup-3a997502-647a-4c82-998e-52ef9486a247")
AMDSETUP_SIZE = 1474  # 4 attribute bytes + data
# "USB Dual Role Device" in AmdSetup's data, by BIOS version (0 = XHCI, 1 = DRD).
# Found by comparing the variable with the option set each way in the BIOS.
DRD_OFFSETS = {"F7G0114": 224}
BIOS_VERSION = Path("/sys/class/dmi/id/bios_version")
UDC = Path("/sys/class/udc")

FS_IOC_GETFLAGS, FS_IOC_SETFLAGS, FS_IMMUTABLE_FL = 0x80086601, 0x40086602, 0x10


def bios_version() -> str:
    try:
        return BIOS_VERSION.read_text().strip()
    except OSError:
        return ""


def udc_name() -> str | None:
    """The USB device controller, present only while the port is in DRD mode."""
    try:
        return next(iter(sorted(os.listdir(UDC))), None)
    except OSError:
        return None


def _drd_byte() -> tuple[bytes, int] | None:
    """(AmdSetup contents, index of the DRD byte), if this BIOS is known and the variable looks right."""
    offset = DRD_OFFSETS.get(bios_version())
    if offset is None:
        return None
    try:
        data = AMDSETUP.read_bytes()
    except OSError:
        return None
    index = 4 + offset
    if len(data) != AMDSETUP_SIZE or data[index] not in (0, 1):
        return None
    return data, index


def drd_status() -> dict:
    """active: the port is in DRD mode now. configured: what the next boot uses (None if unknown)."""
    found = _drd_byte()
    return {"bios": bios_version(), "active": udc_name() is not None,
            "configured": bool(found[0][found[1]]) if found else None}


def set_drd(enabled: bool, backup_dir: str) -> dict:
    """Set the DRD byte for the next boot. Keeps a copy of the original variable in backup_dir."""
    found = _drd_byte()
    if not found:
        raise RuntimeError(f"switching USB mode isn't supported on BIOS {bios_version() or 'unknown'}")
    data, index = found
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, "AmdSetup.original")
    if not os.path.exists(backup):
        shutil.copyfile(AMDSETUP, backup)
    new = bytearray(data)
    new[index] = 1 if enabled else 0
    if new != data:
        # efivarfs marks variables immutable; lift that for the write, then put it back.
        fd = os.open(AMDSETUP, os.O_RDONLY)
        try:
            flags = struct.unpack("i", fcntl.ioctl(fd, FS_IOC_GETFLAGS, b"\0" * 8)[:4])[0]
            fcntl.ioctl(fd, FS_IOC_SETFLAGS, struct.pack("i4x", flags & ~FS_IMMUTABLE_FL))
            try:
                with open(AMDSETUP, "wb", buffering=0) as f:
                    f.write(bytes(new))  # one write: efivarfs takes attributes + data at once
            finally:
                fcntl.ioctl(fd, FS_IOC_SETFLAGS, struct.pack("i4x", flags))
        finally:
            os.close(fd)
        log.info("USB mode %s for the next boot", "on" if enabled else "off")
    status = drd_status()
    if status["configured"] != enabled:
        raise RuntimeError("the firmware didn't keep the change")
    return status


# ---- the gadget -----------------------------------------------------------

GADGET = Path("/sys/kernel/config/usb_gadget/deck_as_controller")
HIDG = "/dev/hidg0"
REPORT_LENGTH = 64
INTERVAL = 4  # bInterval at high speed: 2^(4-1) x 125 us = 1 ms
# f_hid (Linux 6.12+): preload GET_REPORT answers. struct usb_hidg_report is report_id u8,
# userspace_req u8 (0 = answer every future request), length u16, data[64], pad[4].
GADGET_HID_WRITE_GET_REPORT = 0x40000000 | (72 << 16) | (ord("g") << 8) | 0x42
FEATURE_REPORTS = (0x05, 0x09, 0x20)


def _write(path: Path, value):
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(str(value))


def create_gadget(profile: Profile):
    """Present the profile's controller on the USB port (bound to the device controller)."""
    remove_gadget()
    os.system("modprobe libcomposite")
    if not Path("/sys/kernel/config/usb_gadget").exists():
        os.system("mount -t configfs none /sys/kernel/config")
    GADGET.mkdir()
    _write(GADGET / "idVendor", hex(profile.vendor_id))
    _write(GADGET / "idProduct", hex(profile.product_id))
    _write(GADGET / "bcdDevice", "0x0100")
    _write(GADGET / "bcdUSB", "0x0200")
    _write(GADGET / "max_speed", "high-speed")
    strings = GADGET / "strings/0x409"
    strings.mkdir(parents=True)
    _write(strings / "manufacturer", profile.provider)
    _write(strings / "product", profile.bt_name)
    config = GADGET / "configs/c.1"
    config.mkdir(parents=True)
    _write(config / "MaxPower", 500)
    hid = GADGET / "functions/hid.usb0"
    hid.mkdir(parents=True)
    _write(hid / "protocol", 0)
    _write(hid / "subclass", 0)
    _write(hid / "report_length", REPORT_LENGTH)
    _write(hid / "interval", INTERVAL)
    _write(hid / "report_desc", profile.usb_descriptor)
    (config / "hid.usb0").symlink_to(hid)
    _write(GADGET / "UDC", udc_name())
    log.info("USB gadget ready as %s", profile.bt_name)


def remove_gadget():
    if not GADGET.exists():
        return
    try:
        _write(GADGET / "UDC", "\n")
    except OSError:
        pass
    link = GADGET / "configs/c.1/hid.usb0"
    if link.is_symlink():
        link.unlink()
    for d in ("configs/c.1/strings/0x409", "configs/c.1", "functions/hid.usb0", "strings/0x409"):
        if (GADGET / d).exists():
            (GADGET / d).rmdir()
    GADGET.rmdir()
    log.info("USB gadget removed")


def host_connected() -> bool:
    """A host has enumerated and configured the gadget."""
    name = udc_name()
    try:
        return name is not None and (UDC / name / "state").read_text().strip() == "configured"
    except OSError:
        return False


def open_hidg(profile: Profile, mac: bytes) -> int:
    fd = os.open(HIDG, os.O_RDWR | os.O_NONBLOCK)
    for report_id in FEATURE_REPORTS:
        data = profile.feature_report(report_id, mac)
        if data:
            fcntl.ioctl(fd, GADGET_HID_WRITE_GET_REPORT,
                        struct.pack("<BBH64s4x", report_id, 0, len(data), data))
    return fd


class Link:
    """The USB host, with the same interface as hid.Link. run() blocks until it goes away."""

    address = "usb"
    unplugged = False
    closed_by_host = False

    def __init__(self, fd: int, profile: Profile, get_report: Callable[[], bytes],
                 on_rumble: Callable[[int, int, float | None], None]):
        self.fd, self.profile = fd, profile
        self.get_report, self.on_rumble = get_report, on_rumble
        self.alive = threading.Event()
        self.alive.set()
        self.new_input = threading.Condition()
        self.sent = self.skipped = 0

    def notify_input(self):
        with self.new_input:
            self.new_input.notify()

    def close(self):
        self.alive.clear()
        self.notify_input()

    def send_extra(self, report: bytes):
        pass  # the USB types have no side reports

    def run(self):
        threading.Thread(target=self._rx_loop, daemon=True, name="usb-rx").start()
        try:
            self._send_loop()
        finally:
            self.close()

    def _send_loop(self):
        # USB has no queue to keep short: the host polls every 1 ms and f_hid holds one
        # report, so send each new Deck report (and the IMU at least every report_interval).
        while self.alive.is_set():
            with self.new_input:
                self.new_input.wait(timeout=self.profile.report_interval)
            if not self.alive.is_set():
                break
            _, writable, _ = select.select([], [self.fd], [], 0.5)
            if not writable:
                self.skipped += 1
                if not host_connected():
                    break
                continue
            try:
                os.write(self.fd, self.profile.usb_report(self.get_report()))
                self.sent += 1
            except BlockingIOError:
                self.skipped += 1
            except OSError as e:  # host gone (cable out, host asleep)
                log.info("USB host went away: %s", e)
                break

    def _rx_loop(self):
        while self.alive.is_set():
            readable, _, _ = select.select([self.fd], [], [], 0.2)
            if not readable:
                continue
            try:
                msg = os.read(self.fd, REPORT_LENGTH)
            except BlockingIOError:
                continue
            except OSError:
                break
            rumble = self.profile.parse_usb_rumble(msg)
            if rumble:
                self.on_rumble(*rumble)
