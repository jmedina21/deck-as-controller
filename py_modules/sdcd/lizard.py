"""Relay the Deck firmware's native mouse/keyboard while Steam requests fallback."""
import logging
import threading

from .deck import DeckController
from .hid import Link
from .profiles.steam import REPORT_KEYBOARD, REPORT_MOUSE, SteamController

log = logging.getLogger("sdcd.lizard")

# USB endpoint, Bluetooth report ID, native body size.
INTERFACES = ((0x81, REPORT_MOUSE, 5), (0x82, REPORT_KEYBOARD, 8))


class LizardRelay:
    def __init__(self, deck: DeckController, link: Link, profile: SteamController):
        self.deck, self.link, self.profile = deck, link, profile
        self.lock = threading.Lock()
        self.enabled = False
        self.threads: list[threading.Thread] = []

    def start(self):
        self.sync()
        for endpoint, report_id, size in INTERFACES:
            thread = threading.Thread(target=self._read, args=(endpoint, report_id, size),
                                      daemon=True, name=f"native-hid-{endpoint:02x}")
            self.threads.append(thread)
            thread.start()

    def sync(self):
        with self.lock:
            enabled = self.profile.lizard_mode
            if enabled == self.enabled:
                return
            self.deck.set_lizard(enabled)
            self.enabled = enabled
            # Clear pending keys/buttons at either boundary; motion is relative.
            for _, report_id, size in INTERFACES:
                self.link.send_native(bytes([report_id]) + bytes(size))
            log.info("firmware mouse/keyboard relay: %s", "on" if enabled else "off")

    def stop(self):
        # Link.close() runs before stop(), unblocking send_native().
        for thread in self.threads:
            thread.join(timeout=1)

    def _read(self, endpoint: int, report_id: int, size: int):
        sent = 0
        try:
            while self.link.alive.is_set():
                report = self.deck.read_endpoint(endpoint, 8)
                if report is None:
                    continue
                if len(report) != size:
                    log.warning("native endpoint %02x: dropped %d-byte report", endpoint, len(report))
                    continue
                with self.lock:
                    if self.enabled:
                        self.link.send_native(bytes([report_id]) + report)
                        sent += 1
                        if sent == 1:
                            log.info("native endpoint %02x: forwarding firmware input", endpoint)
        except OSError:
            if self.link.alive.is_set():
                log.exception("native HID relay failed")
                self.link.close()
        finally:
            log.info("native endpoint %02x: relayed %d reports", endpoint, sent)
