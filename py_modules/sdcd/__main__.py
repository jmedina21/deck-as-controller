"""Entry point: python3 -m sdcd --settings-dir DIR [--options JSON] | --restore | --drd ACTION"""
import argparse
import json
import logging
import os
import sys

parser = argparse.ArgumentParser(prog="sdcd")
parser.add_argument("--settings-dir", default="/tmp/sdcd")
parser.add_argument("--options", default="{}", help="initial options as JSON")
parser.add_argument("--restore", action="store_true",
                    help="undo everything controller mode changes (bluetoothd, controller, USB gadget)")
parser.add_argument("--drd", choices=("status", "on", "off"),
                    help="USB mode (BIOS USB Dual Role Device) for the next boot; prints the status as JSON")
args = parser.parse_args()

# Touch /run/sdcd-debug on the Deck to log host output reports and control messages.
level = logging.DEBUG if os.path.exists("/run/sdcd-debug") else logging.INFO
logging.basicConfig(stream=sys.stderr, level=level, format="%(name)s: %(message)s")

if args.restore:
    from . import bluez, deck, usb
    deck.rebind_all()
    usb.remove_gadget()
    bluez.restore_stock()
elif args.drd:
    from . import usb
    try:
        status = usb.drd_status() if args.drd == "status" else usb.set_drd(args.drd == "on", args.settings_dir)
    except Exception as e:
        status = {**usb.drd_status(), "error": str(e)}
    print(json.dumps(status))
else:
    from .daemon import Daemon
    Daemon(args.settings_dir, json.loads(args.options)).run()
