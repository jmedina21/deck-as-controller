"""Controller-mode daemon: makes the Deck a Bluetooth game controller for a paired host.

Talks to the Decky plugin over stdio using JSON lines:
  stdin  commands: {"cmd": "pair"} | {"cmd": "stop"} | {"cmd": "screen"}
                   {"cmd": "connect", "address": str (optional)}
                   {"cmd": "forget", "address": str}
                   {"cmd": "options", "screen_off": bool, "deadzone": float, "pad_haptics": bool}
  stdout events:   {"type": "state", ...} | {"type": "error", "message": str}
                   {"type": "stopped", "reason": str}
The controller type ("profile") sets the Bluetooth identity, so it's fixed for
the daemon's lifetime; the plugin restarts the daemon to change it.
"""
import json
import logging
import os
import signal
import socket
import sys
import threading
import time

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

from . import bluez, hid
from .deck import DeckController, DeckInput, SharedInput, rebind_all
from .profiles import DEFAULT_PROFILE, PROFILES, Battery, Encoder, get_profile

log = logging.getLogger("sdcd")

PAIRING_SECONDS = 180
RECONNECT_INTERVAL = 5.0
QAM_TAP_MAX = 0.6  # seconds: tap ⋯ toggles the screen
QAM_HOLD_STOP = 2.0  # seconds: hold ⋯ stops controller mode
BATTERY = "/sys/class/power_supply/BAT1"
RUNTIME_OPTIONS = ("screen_off", "deadzone", "pad_haptics")


class Hosts:
    """Remembered hosts, most recent first, each with the profile it was paired as."""

    def __init__(self, settings_dir: str):
        self.path = os.path.join(settings_dir, "hosts.json")
        try:
            with open(self.path) as f:
                self.items = json.load(f)
        except (OSError, ValueError):
            self.items = []
        for h in self.items:
            h.setdefault("profile", DEFAULT_PROFILE)  # hosts saved before profiles existed

    def get(self, address: str) -> dict | None:
        return next((h for h in self.items if h["address"] == address), None)

    def remember(self, address: str, name: str, profile: str):
        self.items = ([{"address": address, "name": name, "profile": profile}]
                      + [h for h in self.items if h["address"] != address])
        self._save()

    def forget(self, address: str):
        self.items = [h for h in self.items if h["address"] != address]
        self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.items, f)


def read_battery() -> Battery:
    try:
        with open(f"{BATTERY}/capacity") as f:
            percent = int(f.read())
        with open(f"{BATTERY}/status") as f:
            status = f.read().strip()
    except (OSError, ValueError):
        return Battery()
    return Battery(percent=percent, charging=status in ("Charging", "Full"))


class Daemon:
    def __init__(self, settings_dir: str, options: dict):
        self.options = {"screen_off": True, "deadzone": 0.08, "pad_haptics": True,
                        "profile": DEFAULT_PROFILE, **options}
        self.profile = get_profile(self.options["profile"])
        self.hosts = Hosts(settings_dir)
        self.loop = GLib.MainLoop()
        self.out_lock = threading.Lock()
        self.link_lock = threading.Lock()
        self.link: hid.Link | None = None
        self.encoder: Encoder | None = None
        self.session_thread: threading.Thread | None = None
        self.link_name = ""
        self.stopping = threading.Event()
        self.reconnect_now = threading.Event()
        self.stop_reason = "stopped"
        self.adapter: bluez.Adapter | None = None
        self.last_state: dict | None = None
        # Deck screen blanking is drawn by the plugin frontend; we only own the flag.
        self.screen_off = False
        self.battery = read_battery()
        self.tick_count = 0
        # Reconnect automatically after link loss, but not after the host disconnected us.
        self.auto_reconnect = True
        # Host to reconnect to; defaults to the most recent one paired as this profile.
        self.target: str | None = next(
            (h["address"] for h in self.hosts.items if h["profile"] == self.profile.id), None)
        self.stats_since = time.monotonic()

    # ---- IPC -------------------------------------------------------------

    def emit(self, **event):
        with self.out_lock:
            sys.stdout.write(json.dumps(event) + "\n")
            sys.stdout.flush()

    def emit_state(self):
        if self.link:
            state = "connected"
        elif self.adapter and self.adapter.pairing_open():
            state = "pairing"
        elif self.auto_reconnect and self.target:
            state = "reconnecting"
        else:
            state = "idle"
        event = dict(type="state", state=state, host=self.link_name, target=self.target,
                     hosts=self.hosts.items, screen_off=self.screen_off, options=self.options,
                     profiles=[{"id": p.id, "label": p.label} for p in PROFILES.values()])
        if event != self.last_state:
            self.last_state = json.loads(json.dumps(event))  # deep copy
            self.emit(**event)

    def _stdin_loop(self):
        for line in sys.stdin:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            cmd = msg.get("cmd")
            if cmd == "stop":
                self.stop("stopped")
            elif cmd == "pair":
                GLib.idle_add(self._open_pairing)
            elif cmd == "connect":
                self._connect(msg.get("address"))
            elif cmd == "forget":
                self._forget(msg.get("address", ""))
            elif cmd == "screen":
                self.toggle_screen()
            elif cmd == "options":
                self.options.update({k: v for k, v in msg.items() if k in RUNTIME_OPTIONS})
                if self.encoder:
                    self.encoder.deadzone = self.options["deadzone"]
                self.emit_state()
        self.stop("plugin went away")

    def _connect(self, address: str | None):
        host = self.hosts.get(address) if address else self.hosts.get(self.target or "")
        if not host:
            return
        if host["profile"] != self.profile.id:
            self.emit(type="error", message=f"{host['name']} was paired as a different controller "
                                            "type. Switch the type, or pair it again.")
            return
        self.target = host["address"]
        self.auto_reconnect = True
        link = self.link
        if link and link.address != self.target:
            link.close()  # switching hosts; the session ends and we reconnect to the new one
        self.reconnect_now.set()
        self.emit_state()

    def _forget(self, address: str):
        if not self.hosts.get(address):
            return
        if self.link and self.link.address == address:
            self.link.close()
        self.hosts.forget(address)
        if self.target == address:
            self.target = next((h["address"] for h in self.hosts.items
                                if h["profile"] == self.profile.id), None)
        GLib.idle_add(lambda: self.adapter.remove_device(address) and False)
        self.emit_state()

    # ---- lifecycle -------------------------------------------------------

    def run(self):
        dbus.mainloop.glib.threads_init()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        signal.signal(signal.SIGTERM, lambda *_: self.stop("terminated"))
        self.emit(type="state", state="starting", host="", target=None, hosts=self.hosts.items,
                  screen_off=False, options=self.options)
        try:
            bluez.enter_gamepad_mode(self.profile)
            self.adapter = bluez.Adapter(dbus.SystemBus(), self.profile)
            if not self.target:
                self.adapter.open_pairing(PAIRING_SECONDS)
            for target in (self._stdin_loop, self._listen_loop, self._reconnect_loop):
                threading.Thread(target=target, daemon=True, name=target.__name__).start()
            GLib.timeout_add_seconds(2, self._tick)
            self.emit_state()
            self.loop.run()
        except Exception as e:
            log.exception("daemon failed")
            self.stop_reason = f"error: {e}"
        finally:
            self._cleanup()
            self.emit(type="stopped", reason=self.stop_reason)

    def toggle_screen(self):
        if self.link:
            self.screen_off = not self.screen_off
        self.emit_state()

    def stop(self, reason: str):
        if self.stopping.is_set():
            return
        self.stop_reason = reason
        self.stopping.set()
        if self.link:
            self.link.close()
        GLib.idle_add(self.loop.quit)

    def _cleanup(self):
        if self.link:
            self.link.close()
        if self.session_thread:
            self.session_thread.join(timeout=3)
        rebind_all()
        try:
            bluez.restore_stock()
        except Exception:
            log.exception("restoring bluetoothd failed")

    def _tick(self):
        """Every 2 s: expire the pairing window, refresh battery, log link stats, update UI."""
        if self.adapter and self.adapter.pairing_until and not self.adapter.pairing_open():
            self.adapter.close_pairing()
        self.tick_count += 1
        if self.tick_count % 5 == 0:  # every 10 s
            self.battery = read_battery()
            link = self.link
            if link:
                for report in self.profile.side_reports(self.battery):
                    link.send_extra(report)
                now = time.monotonic()
                log.info("link: %.0f reports/s sent, %d skipped (link busy)",
                         link.sent / (now - self.stats_since), link.skipped)
                link.sent = link.skipped = 0
                self.stats_since = now
        self.emit_state()
        return True

    def _open_pairing(self):
        self.adapter.open_pairing(PAIRING_SECONDS)
        self.emit_state()
        return False

    # ---- connections -----------------------------------------------------

    def _listen_loop(self):
        """Hosts connecting to us: first pairing, or a host that reconnects itself."""
        ctrl_srv, intr_srv = hid.listen(hid.PSM_CTRL), hid.listen(hid.PSM_INTR)
        while not self.stopping.is_set():
            ctrl, (address, _) = ctrl_srv.accept()
            intr_srv.settimeout(10)
            try:
                intr, (address2, _) = intr_srv.accept()
            except socket.timeout:
                ctrl.close()
                continue
            if address2 != address:
                ctrl.close()
                intr.close()
                continue
            self._start_session(ctrl, intr, address)

    def _reconnect_loop(self):
        """Reconnect to the target host while not connected (like a real controller)."""
        while not self.stopping.is_set():
            self.reconnect_now.wait(RECONNECT_INTERVAL)
            self.reconnect_now.clear()
            address = self.target
            if (self.stopping.is_set() or self.link or not self.auto_reconnect
                    or not address or self.adapter.pairing_open()):
                continue
            try:
                ctrl, intr = hid.connect(address)
            except OSError as e:
                log.info("reconnect to %s failed: %s", address, e)
                continue
            self._start_session(ctrl, intr, address)

    def _start_session(self, ctrl, intr, address: str):
        with self.link_lock:
            if self.link or self.stopping.is_set():
                ctrl.close()
                intr.close()
                return
            deck = DeckController()
            encoder = self.profile.new_encoder(self.options["deadzone"])
            latest = SharedInput()
            link = hid.Link(ctrl, intr, address, self.adapter.mac_bytes, self.profile,
                            get_report=lambda: encoder.encode(latest.take(), self.battery),
                            has_urgent=lambda: latest.urgent,
                            on_rumble=lambda low, high, duration: _safe(deck.rumble, low, high, duration),
                            on_haptic=lambda cmd: _safe(deck.haptic, cmd))
            self.link = link
            self.encoder = encoder
            self.session_thread = threading.Thread(target=self._session, args=(link, deck, latest),
                                                   daemon=True, name="session")
            self.session_thread.start()

    def _session(self, link: hid.Link, deck: DeckController, latest: SharedInput):
        name = self.adapter.device_name(link.address)
        self.link_name = name
        self.hosts.remember(link.address, name, self.profile.id)
        self.target = link.address
        self.auto_reconnect = True
        GLib.idle_add(self.adapter.close_pairing)
        log.info("connected to %s (%s) as %s", name, link.address, self.profile.id)
        try:
            deck.grab()
        except Exception as e:
            log.exception("could not grab the Deck controller")
            self.emit(type="error", message=f"Could not take over the Deck controller: {e}")
            link.close()
        else:
            self.screen_off = self.options["screen_off"]
            self.emit_state()
            threading.Thread(target=self._read_deck, args=(link, deck, latest), daemon=True,
                             name="deck-reader").start()
            self.stats_since = time.monotonic()
            for report in self.profile.side_reports(self.battery):
                link.send_extra(report)
            link.run()
        finally:
            deck.release()
            self.screen_off = False
            with self.link_lock:
                self.link = None
                self.encoder = None
                self.link_name = ""
            log.info("disconnected from %s%s", name,
                     " (host removed the pairing)" if link.unplugged
                     else " (by host)" if link.closed_by_host else "")
            if link.unplugged:
                self._forget(link.address)
                GLib.idle_add(self._open_pairing)
            elif link.closed_by_host:
                self.auto_reconnect = False
            self.emit_state()

    def _read_deck(self, link: hid.Link, deck: DeckController, latest: SharedInput):
        qam_down_at = None
        last_config = time.monotonic()
        pads_clicked = (False, False)
        while link.alive.is_set():
            try:
                raw = deck.read()
            except OSError as e:
                log.warning("Deck controller read failed: %s", e)
                link.close()
                return
            now = time.monotonic()
            if now - last_config > 5:  # re-assert settings in case the controller reset
                _safe(deck.configure)
                last_config = now
            state = DeckInput.parse(raw) if raw else None
            if state is None:
                continue
            latest.update(state)
            link.notify_input()

            # Steam normally gives a haptic tick when a trackpad is clicked; we own the pads now.
            clicked = (state.lpad_click, state.rpad_click)
            if self.options["pad_haptics"] and not self.profile.host_haptics:
                for left, (was, now_down) in ((True, (pads_clicked[0], clicked[0])),
                                              (False, (pads_clicked[1], clicked[1]))):
                    if now_down and not was:
                        log.debug("trackpad click (%s): haptic tick", "left" if left else "right")
                        _safe(deck.click_pulse, left)
            pads_clicked = clicked

            # ⋯ (QAM) is reserved for us: tap toggles the screen, hold stops.
            if state.qam and qam_down_at is None:
                qam_down_at = now
            elif state.qam and now - qam_down_at >= QAM_HOLD_STOP:
                _safe(deck.rumble, 0, 200)
                time.sleep(0.15)
                self.stop("stopped from the Deck")
                return
            elif not state.qam and qam_down_at is not None:
                if now - qam_down_at <= QAM_TAP_MAX:
                    self.toggle_screen()
                qam_down_at = None


def _safe(fn, *args):
    try:
        fn(*args)
    except OSError as e:
        log.debug("%s failed: %s", getattr(fn, "__name__", fn), e)
