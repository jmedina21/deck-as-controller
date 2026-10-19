import collections
import errno
import pathlib
import struct
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "py_modules"))

from sdcd.deck import DeckController, DeckInput
from sdcd.hid import Link
from sdcd.lizard import LizardRelay
from sdcd.profiles.base import Battery
from sdcd.profiles.steam import DESCRIPTOR, SteamController


def feature(command, payload=b""):
    return bytes([1, command, len(payload)]) + payload


class LizardTests(unittest.TestCase):
    def test_host_commands_and_reconnect(self):
        profile = SteamController()
        for command, enabled in ((0x81, False), (0x85, True), (0x81, False), (0x8E, True)):
            profile.set_feature(feature(command))
            self.assertEqual(profile.lizard_mode, enabled)
        for enabled in (False, True, False):
            profile.set_feature(feature(0x87, struct.pack("<BH", 9, enabled)))
            self.assertEqual(profile.lizard_mode, enabled)
        profile.new_encoder()
        self.assertTrue(profile.lizard_mode)

    def test_malformed_settings_cannot_switch_mode(self):
        profile = SteamController()
        profile.set_feature(feature(0x81))
        for report in (b"", b"\x01", b"\x01\x87\x03\x09\x01", feature(0x87, b"\x09\x01"),
                       feature(0x87, b"\x09\x02\x00"), feature(0x87, b"\x30\x01\x00")):
            profile.set_feature(report)
            self.assertFalse(profile.lizard_mode)

    def test_native_usb_configuration_and_cleanup(self):
        deck = DeckController()
        deck._feature = Mock()
        deck.set_lizard(True)
        self.assertEqual(deck._feature.call_args_list[0].args, (b"\x8e",))
        self.assertEqual(deck._feature.call_args_list[1].args, (b"\x85",))
        self.assertIn(b"\x09\x01\x00", deck._feature.call_args_list[2].args[0])
        deck.configure()
        self.assertEqual(deck._feature.call_args_list[-2].args, (b"\x85",))
        deck.set_lizard(False)
        self.assertEqual(deck._feature.call_args_list[-2].args, (b"\x81",))
        self.assertIn(b"\x09\x00\x00", deck._feature.call_args_list[-1].args[0])
        deck.fd, deck.claimed = 123, [0, 1, 2]
        with patch("sdcd.deck.fcntl.ioctl") as ioctl, patch("sdcd.deck.os.close"), \
                patch("sdcd.deck.rebind_all"), patch.object(deck, "_send_rumble"):
            deck.release()
        self.assertEqual([call.args[2] for call in ioctl.call_args_list],
                         [struct.pack("I", i) for i in (2, 1, 0)])

    def test_mode_boundaries_release_everything(self):
        profile, deck, link = SteamController(), Mock(), Mock()
        relay = LizardRelay(deck, link, profile)
        relay.sync()
        self.assertEqual(deck.set_lizard.call_args.args, (True,))
        self.assertEqual([c.args[0] for c in link.send_native.call_args_list],
                         [b"\x40" + bytes(5), b"\x41" + bytes(8)])
        link.send_native.reset_mock()
        relay.sync()
        link.send_native.assert_not_called()
        profile.set_feature(feature(0x81))
        relay.sync()
        self.assertFalse(relay.enabled)
        self.assertEqual(link.send_native.call_count, 2)

    def test_native_report_is_unchanged_and_suppressed_outside_fallback(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                profile, deck, link = SteamController(), Mock(), Mock()
                link.alive = threading.Event()
                link.alive.set()
                report = b"\x01\x80\x7f\xff\x02"
                def read(*args):
                    link.alive.clear()
                    return report
                deck.read_endpoint.side_effect = read
                relay = LizardRelay(deck, link, profile)
                relay.enabled = enabled
                relay._read(0x81, 0x40, 5)
                if enabled:
                    link.send_native.assert_called_once_with(b"\x40" + report)
                else:
                    link.send_native.assert_not_called()

    def test_malformed_native_report_is_dropped(self):
        profile, deck, link = SteamController(), Mock(), Mock()
        link.alive = threading.Event()
        link.alive.set()
        reports = iter((b"\x01\x02", b"\x01\x80\x7f\xff\x02"))
        def read(*args):
            report = next(reports)
            if report[1:2] == b"\x80":
                link.alive.clear()
            return report
        deck.read_endpoint.side_effect = read
        relay = LizardRelay(deck, link, profile)
        relay.enabled = True
        with self.assertLogs("sdcd.lizard", level="WARNING"):
            relay._read(0x81, 0x40, 5)
        link.close.assert_not_called()
        link.send_native.assert_called_once_with(b"\x40\x01\x80\x7f\xff\x02")

    def test_key_release_retried_on_bluetooth_backpressure(self):
        link = object.__new__(Link)
        link.alive = threading.Event()
        link.alive.set()
        link.intr = Mock()
        link.intr.send.side_effect = [BlockingIOError(errno.EAGAIN, "busy"), 10]
        with patch("sdcd.hid.time.sleep"):
            link.send_native(b"\x41" + bytes(8))
        self.assertEqual(link.intr.send.call_count, 2)
        self.assertEqual(link.intr.send.call_args.args[0], b"\xa1\x41" + bytes(8))

    def test_native_read_failure_closes_connection(self):
        profile, deck, link = SteamController(), Mock(), Mock()
        link.alive = threading.Event()
        link.alive.set()
        deck.read_endpoint.side_effect = OSError(errno.ENODEV, "controller removed")
        relay = LizardRelay(deck, link, profile)
        with self.assertLogs("sdcd.lizard", level="ERROR"):
            relay._read(0x82, 0x41, 8)
        link.close.assert_called_once()
        link.send_native.assert_not_called()

    def test_original_steam_state_remains_intact(self):
        profile = SteamController()
        encoder = profile.new_encoder()
        report = encoder.encode(DeckInput(a=True, r5=True, rpad_touch=True,
                                          gx=123, gy=-456, rpad_x=567), Battery())
        self.assertEqual((len(report), report[0]), (46, 0x45))
        self.assertEqual(struct.unpack_from("<I", report, 2)[0], 1 | 0x100 | 0x200000)
        self.assertEqual(struct.unpack_from("<h", report, 24)[0], 567)
        self.assertEqual(struct.unpack_from("<hh", report, 40), (123, -456))

    def test_descriptor_sizes_match_native_and_vendor_reports(self):
        # Parse HID short items independently to check report lengths and layout.
        state, stack = {}, []
        sizes = collections.defaultdict(int)
        i, depth = 0, 0
        while i < len(DESCRIPTOR):
            prefix = DESCRIPTOR[i]
            length = (0, 1, 2, 4)[prefix & 3]
            value = int.from_bytes(DESCRIPTOR[i + 1:i + 1 + length], "little")
            kind, tag = (prefix >> 2) & 3, prefix >> 4
            i += 1 + length
            if kind == 1:
                if tag == 10:
                    stack.append(state.copy())
                elif tag == 11:
                    state = stack.pop()
                else:
                    state[tag] = value
            elif kind == 0:
                if tag == 10: depth += 1
                elif tag == 12: depth -= 1
                elif tag in (8, 9, 11):
                    sizes[tag, state.get(8, 0)] += state[7] * state[9]
        self.assertEqual((i, depth, stack), (len(DESCRIPTOR), 0, []))
        # The real controller's layout, which Windows accepts: mouse, keyboard,
        # vendor; report IDs inside the collections and no Push/Pop.
        self.assertEqual(DESCRIPTOR[:8], bytes.fromhex("05010902a1018540"))
        self.assertEqual(sum(DESCRIPTOR.count(bytes.fromhex(h)) for h in ("a4", "b4")), 0)
        self.assertEqual(sizes[8, 0x40], 5 * 8)
        self.assertEqual(sizes[8, 0x41], 8 * 8)
        self.assertEqual(sizes[8, 0x45], 45 * 8)
        self.assertEqual(sizes[11, 1], 63 * 8)


if __name__ == "__main__":
    unittest.main()
