# Deck as Controller. (beta build)

https://github.com/user-attachments/assets/c8854f73-ec46-4d12-9bf5-65d40b09f82e

A [Decky Loader](https://decky.xyz) plugin that turns your Steam Deck into a wireless Bluetooth
controller for your Mac or iPad. Nothing needs to be installed on the other device: the Deck
pairs as a PS5, Xbox or Steam Controller.

**This is still a very early build, expect latency and connection issues, specially with untested devices.**

Developed on a Steam Deck OLED (SteamOS, Decky Loader 3.2) with a Mac running macOS 27 and an
iPad Mini 7 running iOS 27. See [Tested devices](#tested-devices) for what others have tried.

> **Independent project.** Deck as Controller is developed independently and is free. It isn't
> affiliated with, endorsed by, or sponsored by Valve Corporation.

## Features

- **Four controller types:** PS5 (DualSense), PS5 Edge, Xbox Elite Series 2, and Steam
  Controller (2026). PS5 Edge carries all four Deck back buttons; Steam Controller carries every
  Deck control to Steam on the other device.
- **Deck screen off while connected.** Tap **⋯** to toggle it; hold **⋯** for 2 seconds to stop
  and get the Deck's screen and controls back.
- **Reconnects automatically** to the last device when you turn the plugin on.
- **Remembers your devices:** pair more than one (e.g. a Mac and an iPad).
- **Rumble** (see the table below for where it works).
- **Trackpads:** the Deck trackpads act as the PS5 touchpad, with a small haptic tick when you
  click them.
- **Gyro** on the PS5 types.
- **Battery level** of the Deck shown on the Mac (PS5 types).

## Install

1. Install [Decky Loader](https://decky.xyz) on your Deck.
2. Download `deck-as-controller.zip` from the releases page.
3. In Game Mode, open **⋯ → Decky → ⚙ Settings → General**, turn on **Developer mode**, then go
   to **Developer → Install Plugin from ZIP File** and pick the zip.

## Use

1. Open **⋯ → Decky → Deck as Controller**, choose a **controller type**, and turn on
   **Use as controller**.
2. The first time, the Deck is ready to pair. On your Mac or iPad, open Bluetooth settings and
   connect to the controller that appears (e.g. "DualSense Edge Wireless Controller").
3. After that, turning the plugin on reconnects to the device automatically.

While connected, the Deck's controls go to your device:

| On the Deck                         | Does                                     |
| ----------------------------------- | ---------------------------------------- |
| Tap **⋯** (or tap the black screen) | Turn the Deck screen on or off           |
| Hold **⋯** for 2 seconds            | Stop and give the Deck its controls back |

**Switching controller types:** your device remembers the Deck as the type it was paired as.
To switch, remove the Deck from your device's Bluetooth settings, choose the new type, tap
**Pair a new device**, and pair again.

### Which type to pick

On a Mac, **PS5 Edge** is the best all-round choice. Tested results:

|                                                                         | PS5 / PS5 Edge | Xbox Elite      | Steam Controller |
| ----------------------------------------------------------------------- | -------------- | --------------- | ---------------- |
| Steam and games played through it (e.g. Baldur's Gate 3, Hades)         | ✅             | ✅              | ✅               |
| Mac games that read controllers directly (e.g. Hollow Knight: Silksong) | ✅             | ❌ not detected | ❌ not detected  |
| Rumble from Steam (Steam's test page, Steam Input games)                | ❌             | ✅              | ✅       |
| Rumble from Mac games using Apple's controller support                  | ✅             | ✅              | –                |
| Back buttons                                                            | PS5 Edge       | Steam only      | ✅               |


**Steam Controller** is for playing through Steam (including Steam on a Linux or Windows PC).
The Deck has the same controls as the 2026 Steam Controller, so Steam sees both sticks, both
trackpads as separate pads, the four back buttons, gyro and stick touch, and you configure them
with Steam Input like a real one. Rumble and trackpad haptics come from Steam. Games that don't
run through Steam won't recognize it: on a Mac that includes native games like Hollow Knight and
Silksong, because macOS has no support for the Steam Controller and Steam can't pass it on to
them. Tested with Steam on a Mac and on a Linux PC.

### Button mapping

| Deck           | PS5 / PS5 Edge           | Xbox Elite              |
| -------------- | ------------------------ | ----------------------- |
| A B X Y        | ✕ ○ □ △                  | A B X Y                 |
| View / Menu    | Create / Options         | View / Menu             |
| Steam          | PS                       | Xbox                    |
| Trackpad click | Touchpad click           | –                       |
| L4 / R4        | Edge left / right Fn     | Paddles P3 / P1         |
| L5 / R5        | Edge left / right paddle | Paddles P4 / P2         |
| ⋯              | reserved for the plugin  | reserved for the plugin |

As a Steam Controller every control keeps its own name (A B X Y, View / Menu, Steam, L4 / R4,
L5 / R5, both trackpads); only **⋯** stays reserved for the plugin.

### Options

- **Turn off screen while connected**
- **Trackpad click feedback:** the haptic tick when you click a trackpad. Not used as a Steam
  Controller, where Steam plays its own trackpad haptics on the Deck.
- **Stick deadzone**

## Known limitations

- **Steam doesn't rumble the PS5 types.** Steam never sends rumble to them over Bluetooth, so
  Steam's rumble test and games that rumble through Steam Input stay silent. Use Xbox Elite if
  you need rumble in those.
- **Some Mac games only work with the PS5 types.** Native Mac games that read controllers
  through Apple's support, such as Hollow Knight and Silksong, don't see the Steam Controller
  type at all, and may not detect Xbox Elite (below). Pick PS5 Edge for those.
- **Some Mac games don't detect Xbox Elite.** macOS keeps the name the Deck first paired with
  (e.g. "DualSense Wireless Controller") for its Bluetooth address, even after you forget it,
  and passes that name to games. Games that recognize controllers by name, like Silksong, then
  ignore the Deck. Renaming it in Bluetooth settings doesn't help.
- **Xbox Elite back buttons only reach Steam.** Mac games using Apple's controller support don't
  see the paddles; Steam and SDL-based games do.
- **The Steam Controller type connects over Bluetooth Classic.** A real Steam Controller uses
  Bluetooth LE. Steam on Linux doesn't tell them apart; other systems haven't been tried. Steam
  also registers the Deck to your account as a controller, with a serial number made up from the
  Deck's Bluetooth address, and its grip sensors and haptic audio aren't emulated.
- **The Deck's own Bluetooth devices are unavailable while the plugin is on.** Headphones,
  controllers and keyboards paired to the Deck can't be used, and your device never uses the
  Deck as a speaker.
- **Turning the plugin on or off restarts the Deck's Bluetooth**, which drops any Bluetooth
  devices connected to the Deck for a moment.

## Tested devices

Reports from the developer and from users. Something missing or different for you? Open an
[issue](https://github.com/jmedina21/deck-as-controller/issues).

| Deck | Connected to                 | Controller type  | Result                                                                                         | Source                                                         |
| ---- | ---------------------------- | ---------------- | ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| OLED | Mac (macOS 27), Steam        | all four         | ✅ Works, see [Which type to pick](#which-type-to-pick)                                        | developer                                                      |
| OLED | Mac (macOS 27), native games | PS5 / PS5 Edge   | ✅ Works                                                                                       | developer                                                      |
| OLED | iPad Mini 7 (iOS 27)         | Xbox Elite       | ✅ Works                                                                                       | developer                                                      |
| –    | Steam Frame                  | all              | ✅ Works                                                                                       | [#3](https://github.com/jmedina21/deck-as-controller/issues/3) |
| LCD  | Linux PC (NixOS)             | PS5 Edge         | ✅ Works, including trackpads and back buttons. Remove any old pairing on both sides first.    | [#4](https://github.com/jmedina21/deck-as-controller/issues/4) |
| –    | Linux PC (CachyOS), Steam    | Steam Controller | ⚠️ Works for one user; another's pairing failed with an authentication error                    | [@SrStylus](https://github.com/SrStylus), [#2](https://github.com/jmedina21/deck-as-controller/issues/2) |

## How it works

The plugin's backend runs a small daemon with the Deck's system Python:

- **Bluetooth:** restarts BlueZ with a runtime-only config (under `/run`) that frees the HID
  channels and gives the Deck a gamepad identity, then serves the Bluetooth HID profile itself.
  Stopping the plugin, or rebooting, restores the stock setup.
- **Input:** takes exclusive access to the built-in controller over USB, so Steam on the Deck
  stops reacting to it, and translates its reports into the chosen controller's format.
- **Profiles:** `py_modules/sdcd/profiles/` defines each controller type: its Bluetooth
  identity, HID descriptor, input encoding and rumble parsing.

## Development

```sh
pnpm install
scripts/deploy.sh deck@<deck-ip>   # build and install over SSH (needs passwordless sudo on the Deck)
scripts/package.sh                 # build out/deck-as-controller.zip
```

To log what the host sends (rumble, setup requests), run `sudo touch /run/sdcd-debug` on the
Deck before turning the plugin on; the output appears in Decky's plugin log.

## Contributing

Contributions are welcome: bug reports, pull requests, and test results on devices we haven't
tried yet (Windows, Android, other Linux distributions, other macOS and iPadOS versions).

When reporting a problem, please include your Deck model, the controller type you picked, the
device you connected to, and the plugin log from `~/homebrew/logs/deck-as-controller/` on the
Deck. To capture what your device sends to the Deck, see the debug tip under
[Development](#development).

### Contributors

- [@SrStylus](https://github.com/SrStylus): the Steam Controller Profile
  ([#5](https://github.com/jmedina21/deck-as-controller/pull/5)).

## License

Released under the [MIT License](LICENSE).

## Disclaimer

Steam and Steam Deck are trademarks of Valve Corporation. PlayStation, DualSense and PS5 are
trademarks of Sony Interactive Entertainment. Xbox and Xbox Elite are trademarks of Microsoft.
Mac and iPad are trademarks of Apple. These names are used only to describe compatibility; this
project isn't affiliated with or endorsed by any of these companies.

The plugin changes the Deck's Bluetooth setup and takes over its built-in controller while it
runs. Both are restored when you turn it off or reboot. Use it at your own risk.
