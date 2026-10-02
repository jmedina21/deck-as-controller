"""What every emulated controller type provides."""
import math
from dataclasses import dataclass

from ..deck import DeckInput


@dataclass
class Battery:
    percent: int = 100
    charging: bool = False


class Encoder:
    """Turns Deck state into the profile's input report (without the 0xA1 HIDP header)."""

    def encode(self, state: DeckInput | None, battery: Battery) -> bytes:
        raise NotImplementedError


class Profile:
    """An emulated controller type: its Bluetooth identity and report protocol."""

    id: str
    label: str
    vendor_id: int
    product_id: int
    version: int = 0x0100
    bt_name: str  # name shown in the host's Bluetooth settings
    service_name: str
    provider: str
    descriptor: bytes  # HID report descriptor
    # Longest wait before sending the next report when only the IMU changed
    # (button and stick changes always go out as soon as the link can take them).
    report_interval: float = 0.012
    # The host drives the trackpad haptics itself, so the Deck adds none of its own.
    host_haptics: bool = False

    def new_encoder(self, deadzone: float) -> Encoder:
        raise NotImplementedError

    def feature_report(self, report_id: int, mac: bytes) -> bytes | None:
        """Reply to a GET_REPORT(feature), or None if unsupported. `mac` is our BT address."""
        return None

    def parse_rumble(self, msg: bytes) -> tuple[int, int, float | None] | None:
        """(low_freq, high_freq, duration_s) from a host output message (0xA2 ...,
        or 0xA3 ... for a feature report the host wrote).

        Motor levels are 0..255. duration_s is how long the host asked for (the
        rumble stops by itself after it), or None to run until the host says stop.
        """
        return None

    def set_feature(self, report: bytes):
        """The host wrote a feature report (`report` starts with the report ID)."""

    def parse_haptic(self, msg: bytes) -> bytes | None:
        """The trackpad haptic a host output message asks for, as a command for the
        Deck's controller (see deck.haptic_pulse_cmd and deck.haptic_cmd)."""
        return None

    def side_reports(self, battery: Battery) -> list[bytes]:
        """Extra input reports (without the 0xA1 header) to send every few seconds."""
        return []

    def sdp_record(self) -> str:
        return f"""<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001"><sequence><uuid value="0x1124" /></sequence></attribute>
  <attribute id="0x0004"><sequence>
    <sequence><uuid value="0x0100" /><uint16 value="0x0011" /></sequence>
    <sequence><uuid value="0x0011" /></sequence>
  </sequence></attribute>
  <attribute id="0x0005"><sequence><uuid value="0x1002" /></sequence></attribute>
  <attribute id="0x0006"><sequence>
    <uint16 value="0x656e" /><uint16 value="0x006a" /><uint16 value="0x0100" />
  </sequence></attribute>
  <attribute id="0x0009"><sequence>
    <sequence><uuid value="0x1124" /><uint16 value="0x0101" /></sequence>
  </sequence></attribute>
  <attribute id="0x000d"><sequence><sequence>
    <sequence><uuid value="0x0100" /><uint16 value="0x0013" /></sequence>
    <sequence><uuid value="0x0011" /></sequence>
  </sequence></sequence></attribute>
  <attribute id="0x0100"><text value="{self.service_name}" /></attribute>
  <attribute id="0x0101"><text value="Game Controller" /></attribute>
  <attribute id="0x0102"><text value="{self.provider}" /></attribute>
  <attribute id="0x0201"><uint16 value="0x0111" /></attribute>
  <attribute id="0x0202"><uint8 value="0x08" /></attribute>
  <attribute id="0x0203"><uint8 value="0x00" /></attribute>
  <attribute id="0x0204"><boolean value="true" /></attribute>
  <attribute id="0x0205"><boolean value="true" /></attribute>
  <attribute id="0x0206"><sequence><sequence>
    <uint8 value="0x22" /><text encoding="hex" value="{self.descriptor.hex()}" />
  </sequence></sequence></attribute>
  <attribute id="0x0207"><sequence><sequence>
    <uint16 value="0x0409" /><uint16 value="0x0100" />
  </sequence></sequence></attribute>
  <attribute id="0x020b"><uint16 value="0x0100" /></attribute>
  <attribute id="0x020c"><uint16 value="0x0c80" /></attribute>
  <attribute id="0x020d"><boolean value="false" /></attribute>
  <attribute id="0x020e"><boolean value="false" /></attribute>
</record>
"""


def radial_deadzone(x: int, y: int, deadzone: float) -> tuple[float, float]:
    """Deck stick int16 values -> (-1..1, -1..1) with a radial deadzone, +y up."""
    fx, fy = max(-1.0, x / 32767.0), max(-1.0, y / 32767.0)
    mag = math.hypot(fx, fy)
    if mag <= deadzone:
        return 0.0, 0.0
    scale = min(1.0, (mag - deadzone) / (1.0 - deadzone)) / mag
    return fx * scale, fy * scale


_HAT8 = {
    (1, 0, 0, 0): 0, (1, 1, 0, 0): 1, (0, 1, 0, 0): 2, (0, 1, 1, 0): 3,
    (0, 0, 1, 0): 4, (0, 0, 1, 1): 5, (0, 0, 0, 1): 6, (1, 0, 0, 1): 7,
}


def hat_direction(d: DeckInput) -> int | None:
    """D-pad as 0..7 clockwise from up, or None when centered."""
    return _HAT8.get((d.up, d.right, d.down, d.left))
