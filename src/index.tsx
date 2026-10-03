import {
    ButtonItem,
    ConfirmModal,
    DropdownItem,
    Field,
    Navigation,
    PanelSection,
    PanelSectionRow,
    SliderField,
    ToggleField,
    showModal,
    staticClasses,
} from "@decky/ui";
import {
    addEventListener,
    callable,
    definePlugin,
    removeEventListener,
    routerHook,
    toaster,
} from "@decky/api";
import { useEffect, useState } from "react";
import { FaGamepad } from "react-icons/fa";
import { ScreenBlanker, disposeBlanker } from "./blanker";

type Host = { address: string; name: string; profile: string };
type ProfileInfo = { id: string; label: string; usb: boolean };
// USB mode = the BIOS option "USB Dual Role Device". active: on right now.
// configured: what the next boot uses, or null if it can't be switched from here.
type UsbStatus = {
    bios: string;
    active: boolean;
    configured: boolean | null;
    error?: string;
};

type State = {
    running: boolean;
    state:
        | "off"
        | "starting"
        | "reconnecting"
        | "idle"
        | "pairing"
        | "waiting"
        | "connected";
    host: string;
    target: string | null;
    hosts: Host[];
    screen_off: boolean;
    options: {
        screen_off: boolean;
        deadzone: number;
        pad_haptics: boolean;
        profile: string;
        connection: "bluetooth" | "usb";
    };
    profiles: ProfileInfo[];
    usb: UsbStatus | null;
    error: string | null;
};

const getState = callable<[], State>("get_state");
const setEnabled = callable<[enabled: boolean], State>("set_enabled");
const pair = callable<[], void>("pair");
const connect = callable<[address: string], void>("connect");
const forget = callable<[address: string], State>("forget");
const toggleScreen = callable<[], void>("toggle_screen");
const setOption = callable<
    [key: string, value: boolean | number | string],
    State
>("set_option");
const setUsbMode = callable<[enabled: boolean], State>("set_usb_mode");

function profileLabel(s: State, id: string): string {
    return s.profiles.find((p) => p.id === id)?.label ?? id;
}

function statusText(s: State): string {
    const target = s.hosts.find((h) => h.address === s.target);
    switch (s.state) {
        case "off":
            return "Off";
        case "starting":
            return "Starting…";
        case "pairing":
            return (
                "Ready to pair. On your device, open Bluetooth settings and connect to the Deck " +
                `(it shows up as a ${profileLabel(s, s.options.profile)} controller).`
            );
        case "reconnecting":
            return (
                `Connecting to ${target?.name ?? "your device"}… Make sure its Bluetooth is on. ` +
                "If it forgot the Deck, use “Pair a new device”."
            );
        case "idle":
            return target
                ? `Disconnected from ${target.name}.`
                : "No device paired as this controller type yet.";
        case "waiting":
            return "Connect the Deck to your computer with a USB-C cable.";
        case "connected":
            return `Connected to ${s.host}`;
    }
}

function UsbModeRows({
    s,
    setState,
}: {
    s: State;
    setState: (s: State) => void;
}) {
    const u = s.usb;
    if (!u) {
        return (
            <PanelSectionRow>
                <Field description="Checking USB mode…" />
            </PanelSectionRow>
        );
    }
    const switchable = u.configured !== null;
    const confirmSwitch = (enable: boolean) =>
        showModal(
            <ConfirmModal
                strTitle={enable ? "Turn on USB mode?" : "Turn off USB mode?"}
                strDescription={
                    enable
                        ? "To enable USB mode the Deck needs to restarts. While USB mode is on, USB drives, hubs and docks plugged into the " +
                          "Deck may not work, it can't boot from USB, and it charges slowly from a computer. " +
                          "You can turn it off here at any time."
                        : "The Deck restarts and its USB-C port works normally again."
                }
                strOKButtonText={
                    enable ? "Turn on and restart" : "Turn off and restart"
                }
                onOK={async () => {
                    const next = await setUsbMode(enable);
                    setState(next);
                    if (next.usb?.configured === enable)
                        SteamClient.System.RestartPC();
                }}
            />,
        );

    if (switchable && u.configured !== u.active) {
        return (
            <>
                <PanelSectionRow>
                    <Field
                        description={`USB mode turns ${u.configured ? "on" : "off"} after a restart.`}
                    />
                </PanelSectionRow>
                <PanelSectionRow>
                    <ButtonItem
                        layout="below"
                        onClick={() => SteamClient.System.RestartPC()}
                    >
                        Restart now
                    </ButtonItem>
                </PanelSectionRow>
            </>
        );
    }
    if (u.active) {
        return (
            <>
                <PanelSectionRow>
                    <Field description="USB mode is on. Connect the Deck to your computer with a USB-C cable." />
                </PanelSectionRow>
                {switchable && (
                    <PanelSectionRow>
                        <ButtonItem
                            layout="below"
                            onClick={() => confirmSwitch(false)}
                        >
                            Turn off USB mode
                        </ButtonItem>
                    </PanelSectionRow>
                )}
            </>
        );
    }
    if (switchable) {
        return (
            <>
                <PanelSectionRow>
                    <Field description="A wired connection needs USB mode, which lets the Deck's USB-C port act as a controller." />
                </PanelSectionRow>
                <PanelSectionRow>
                    <ButtonItem
                        layout="below"
                        onClick={() => confirmSwitch(true)}
                    >
                        Turn on USB mode (restarts the Deck)
                    </ButtonItem>
                </PanelSectionRow>
            </>
        );
    }
    return (
        <PanelSectionRow>
            <Field
                description={
                    `A wired connection needs USB mode, which can't be switched from here on BIOS ` +
                    `${u.bios || "(unknown)"}. Turn it on in the BIOS: with the Deck off, hold Volume + and ` +
                    "press Power, open Setup Utility, then Advanced → USB Configuration → USB Dual Role " +
                    "Device → DRD."
                }
            />
        </PanelSectionRow>
    );
}

function useBackendState(): [State | null, (s: State) => void] {
    const [state, setState] = useState<State | null>(null);
    useEffect(() => {
        getState().then(setState);
        const listener = addEventListener<[State]>("state", setState);
        return () => {
            removeEventListener("state", listener);
        };
    }, []);
    return [state, setState];
}

function HostRow({ s, host }: { s: State; host: Host }) {
    const sameType = host.profile === s.options.profile;
    const connected = s.state === "connected" && s.target === host.address;
    const description = connected
        ? "Connected"
        : sameType
          ? `Paired as ${profileLabel(s, host.profile)}`
          : `Paired as ${profileLabel(s, host.profile)}. Switch the controller type to use it, ` +
            "or remove it from the device's Bluetooth settings and pair again.";
    return (
        <>
            <PanelSectionRow>
                <ButtonItem
                    layout="below"
                    label={host.name}
                    description={description}
                    disabled={!s.running || !sameType || connected}
                    onClick={() => connect(host.address)}
                >
                    Connect
                </ButtonItem>
            </PanelSectionRow>
            <PanelSectionRow>
                <ButtonItem layout="below" onClick={() => forget(host.address)}>
                    Forget {host.name}
                </ButtonItem>
            </PanelSectionRow>
        </>
    );
}

function Content() {
    const [s, setState] = useBackendState();
    const [busy, setBusy] = useState(false);
    if (!s) return null;

    const withBusy = (fn: () => Promise<State>) => async () => {
        setBusy(true);
        try {
            setState(await fn());
        } finally {
            setBusy(false);
        }
    };
    const wired = s.options.connection === "usb";
    const profiles = wired ? s.profiles.filter((p) => p.usb) : s.profiles;

    return (
        <>
            <PanelSection>
                <PanelSectionRow>
                    <ToggleField
                        label="Use as controller"
                        description={
                            wired
                                ? `The Deck appears as a wired ${profileLabel(s, s.options.profile)} controller over USB.`
                                : `The Deck appears as a wireless ${profileLabel(s, s.options.profile)} controller.`
                        }
                        checked={s.running}
                        disabled={
                            busy || (wired && !s.running && !s.usb?.active)
                        }
                        onChange={(enabled) =>
                            withBusy(() => setEnabled(enabled))()
                        }
                    />
                </PanelSectionRow>
                <PanelSectionRow>
                    <Field label="Status" description={statusText(s)} />
                </PanelSectionRow>
                {s.error && (
                    <PanelSectionRow>
                        <Field label="Last error" description={s.error} />
                    </PanelSectionRow>
                )}
                {s.state === "connected" && (
                    <PanelSectionRow>
                        <ButtonItem
                            layout="below"
                            onClick={() => toggleScreen()}
                        >
                            {s.screen_off
                                ? "Turn Deck screen on"
                                : "Turn Deck screen off"}
                        </ButtonItem>
                    </PanelSectionRow>
                )}
            </PanelSection>

            <PanelSection title="Connection">
                <PanelSectionRow>
                    <DropdownItem
                        label="Connect with"
                        disabled={busy}
                        rgOptions={[
                            {
                                data: "bluetooth",
                                label: "Wireless (Bluetooth)",
                            },
                            { data: "usb", label: "Wired (USB cable)" },
                        ]}
                        selectedOption={s.options.connection}
                        onChange={(o) =>
                            withBusy(() =>
                                setOption("connection", o.data as string),
                            )()
                        }
                    />
                </PanelSectionRow>
                {wired && <UsbModeRows s={s} setState={setState} />}
            </PanelSection>

            <PanelSection title="Controller type">
                <PanelSectionRow>
                    <DropdownItem
                        label="Appear as"
                        description={
                            wired
                                ? "Over USB the Deck can be a PS5 or PS5 Edge controller. Back buttons need the Edge."
                                : "Each type is paired separately. Back buttons need an Edge, Elite or Steam Controller type."
                        }
                        disabled={busy}
                        rgOptions={profiles.map((p) => ({
                            data: p.id,
                            label: p.label,
                        }))}
                        selectedOption={s.options.profile}
                        onChange={(o) =>
                            withBusy(() =>
                                setOption("profile", o.data as string),
                            )()
                        }
                    />
                </PanelSectionRow>
            </PanelSection>

            {!wired && (
                <PanelSection title="Devices">
                    {s.hosts.map((h) => (
                        <HostRow key={h.address} s={s} host={h} />
                    ))}
                    {s.running &&
                        s.state !== "connected" &&
                        s.state !== "pairing" && (
                            <PanelSectionRow>
                                <ButtonItem
                                    layout="below"
                                    onClick={() => pair()}
                                >
                                    Pair a new device
                                </ButtonItem>
                            </PanelSectionRow>
                        )}
                    {!s.running && s.hosts.length === 0 && (
                        <PanelSectionRow>
                            <Field description="Turn on “Use as controller” to pair your first device." />
                        </PanelSectionRow>
                    )}
                </PanelSection>
            )}

            <PanelSection title="Options">
                <PanelSectionRow>
                    <ToggleField
                        label="Turn off screen while connected"
                        checked={s.options.screen_off}
                        onChange={async (v) =>
                            setState(await setOption("screen_off", v))
                        }
                    />
                </PanelSectionRow>
                <PanelSectionRow>
                    <ToggleField
                        label="Trackpad click feedback"
                        description="A small haptic tick when you click a trackpad."
                        checked={s.options.pad_haptics}
                        onChange={async (v) =>
                            setState(await setOption("pad_haptics", v))
                        }
                    />
                </PanelSectionRow>
                <PanelSectionRow>
                    <SliderField
                        label="Stick deadzone"
                        value={Math.round(s.options.deadzone * 100)}
                        min={0}
                        max={25}
                        step={1}
                        showValue
                        valueSuffix="%"
                        onChange={async (v) =>
                            setState(await setOption("deadzone", v / 100))
                        }
                    />
                </PanelSectionRow>
            </PanelSection>

            <PanelSection title="While connected">
                <PanelSectionRow>
                    <Field
                        description={
                            "The Deck's controls go to your device. Tap ⋯ (or the black screen) to turn the " +
                            "Deck screen on or off. Hold ⋯ for 2 seconds to stop." +
                            (wired
                                ? ""
                                : " Bluetooth controllers and keyboards paired to the Deck are unavailable while this is on.")
                        }
                    />
                </PanelSectionRow>
            </PanelSection>
        </>
    );
}

export default definePlugin(() => {
    let lastState: State["state"] = "off";
    const stateListener = addEventListener<[State]>("state", (s) => {
        if (s.state === "connected" && lastState !== "connected") {
            // The Deck's controller now belongs to the host, so the menu can't be used anyway.
            Navigation.CloseSideMenus();
            toaster.toast({
                title: "Deck as Controller",
                body: `Connected to ${s.host}`,
            });
        } else if (lastState === "connected" && s.state !== "connected") {
            toaster.toast({
                title: "Deck as Controller",
                body: "Disconnected",
            });
        }
        lastState = s.state;
    });
    routerHook.addGlobalComponent("DeckAsControllerBlanker", ScreenBlanker);
    const errorListener = addEventListener<[string]>("error", (message) => {
        toaster.toast({ title: "Deck as Controller", body: message });
    });

    return {
        name: "Deck as Controller",
        titleView: (
            <div className={staticClasses.Title}>Deck as Controller</div>
        ),
        content: <Content />,
        icon: <FaGamepad />,
        onDismount() {
            routerHook.removeGlobalComponent("DeckAsControllerBlanker");
            disposeBlanker();
            removeEventListener("state", stateListener);
            removeEventListener("error", errorListener);
        },
    };
});
