import {
    ButtonItem,
    ConfirmModal,
    DialogButton,
    DropdownItem,
    Field,
    Focusable,
    Navigation,
    PanelSection,
    PanelSectionRow,
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
const getVersion = callable<[], string>("get_version");

const LATEST_RELEASE =
    "https://api.github.com/repos/jmedina21/deck-as-controller/releases/latest";

/** Compares dotted versions like "0.4.0": negative if a is older than b. */
function compareVersions(a: string, b: string): number {
    const pa = a.split(".").map((n) => parseInt(n, 10) || 0);
    const pb = b.split(".").map((n) => parseInt(n, 10) || 0);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
        const d = (pa[i] ?? 0) - (pb[i] ?? 0);
        if (d !== 0) return d;
    }
    return 0;
}

type UpdateCheck = { checking: boolean; message?: string; url?: string };

function AboutSection() {
    const [version, setVersion] = useState("");
    const [check, setCheck] = useState<UpdateCheck>({ checking: false });
    useEffect(() => {
        getVersion().then(setVersion);
    }, []);

    const checkForUpdates = async () => {
        setCheck({ checking: true });
        try {
            const res = await fetch(LATEST_RELEASE, {
                headers: { Accept: "application/vnd.github+json" },
            });
            if (!res.ok) throw new Error(`GitHub answered ${res.status}`);
            const release = await res.json();
            const latest = String(release.tag_name ?? "").replace(/^v/, "");
            const diff = compareVersions(version, latest);
            if (diff < 0) {
                setCheck({
                    checking: false,
                    message: `Version ${latest} is available.`,
                    url: release.html_url,
                });
            } else if (diff > 0) {
                setCheck({
                    checking: false,
                    message: `This version is newer than the latest release (${latest}).`,
                });
            } else {
                setCheck({ checking: false, message: "You're up to date." });
            }
        } catch (e) {
            setCheck({
                checking: false,
                message: `Couldn't check for updates: ${e instanceof Error ? e.message : e}`,
            });
        }
    };

    return (
        <PanelSection title="About">
            <PanelSectionRow>
                <Field label="Version" description={version || "unknown"} />
            </PanelSectionRow>
            <PanelSectionRow>
                <ButtonItem
                    layout="below"
                    disabled={check.checking || !version}
                    onClick={checkForUpdates}
                >
                    {check.checking ? "Checking…" : "Check for updates"}
                </ButtonItem>
            </PanelSectionRow>
            {check.message && (
                <PanelSectionRow>
                    <Field description={check.message} />
                </PanelSectionRow>
            )}
            {check.url && (
                <PanelSectionRow>
                    <ButtonItem
                        layout="below"
                        onClick={() => {
                            Navigation.CloseSideMenus();
                            Navigation.NavigateToExternalWeb(check.url!);
                        }}
                    >
                        Open release page
                    </ButtonItem>
                </PanelSectionRow>
            )}
        </PanelSection>
    );
}

/** The type's name without its "(back buttons)" note, for use in sentences. */
function profileName(s: State, id: string): string {
    const label = s.profiles.find((p) => p.id === id)?.label ?? id;
    return label.split(" (")[0];
}

/** A short headline, plus at most one line on what to do next. */
function status(s: State): { title: string; hint?: string } {
    const target = s.hosts.find((h) => h.address === s.target);
    switch (s.state) {
        case "off":
            return { title: "Off" };
        case "starting":
            return { title: "Starting…" };
        case "pairing":
            return {
                title: "Ready to pair",
                hint: `On your device, open Bluetooth settings and pick the Deck. It shows up as a ${profileName(s, s.options.profile)} controller.`,
            };
        case "reconnecting":
            return {
                title: `Connecting to ${target?.name ?? "your device"}…`,
                hint: "Make sure its Bluetooth is on.",
            };
        case "idle":
            return target
                ? { title: `Disconnected from ${target.name}` }
                : {
                      title: "No device paired yet",
                      hint: "Pair one below as this controller type.",
                  };
        case "waiting":
            return {
                title: "Waiting for the cable",
                hint: "Connect the Deck to your computer with a USB-C cable.",
            };
        case "connected":
            return { title: `Connected to ${s.host}` };
    }
}

/** Separate lines of description text, for steps and tips. */
function Lines({ lines }: { lines: string[] }) {
    return (
        <>
            {lines.map((line) => (
                <div key={line} style={{ marginTop: "4px" }}>
                    {line}
                </div>
            ))}
        </>
    );
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
                        ? "The Deck restarts to turn it on. While USB mode is on, USB drives, hubs and docks " +
                          "may not work, the Deck can't boot from USB, and it charges slowly from a computer. " +
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
                    <Field description="USB mode is on." />
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
                    <Field description="A cable needs USB mode, which lets the USB-C port act as a controller. Turning it on restarts the Deck." />
                </PanelSectionRow>
                <PanelSectionRow>
                    <ButtonItem
                        layout="below"
                        onClick={() => confirmSwitch(true)}
                    >
                        Turn on USB mode
                    </ButtonItem>
                </PanelSectionRow>
            </>
        );
    }
    return (
        <PanelSectionRow>
            <Field
                label="Turn on USB mode in the BIOS"
                description={
                    <>
                        {`It can't be switched from here on BIOS ${u.bios || "(unknown)"}.`}
                        <Lines
                            lines={[
                                "1. Turn the Deck off.",
                                "2. Hold Volume + and press Power.",
                                "3. Setup Utility → Advanced → USB Configuration.",
                                "4. Set USB Dual Role Device to DRD.",
                            ]}
                        />
                    </>
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
          ? `Paired as ${profileName(s, host.profile)}`
          : `Paired as ${profileName(s, host.profile)}. Switch to that type to use it.`;
    return (
        <PanelSectionRow>
            <Field
                label={host.name}
                description={description}
                childrenLayout="below"
            >
                <Focusable
                    flow-children="horizontal"
                    style={{ display: "flex", gap: "8px", width: "100%" }}
                >
                    <DialogButton
                        style={{ flex: 1, minWidth: 0 }}
                        disabled={!s.running || !sameType || connected}
                        onClick={() => connect(host.address)}
                    >
                        Connect
                    </DialogButton>
                    <DialogButton
                        style={{ flex: 1, minWidth: 0 }}
                        onClick={() => forget(host.address)}
                    >
                        Forget
                    </DialogButton>
                </Focusable>
            </Field>
        </PanelSectionRow>
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
    const needsUsbMode = wired && !s.running && !s.usb?.active;
    const st = status(s);

    return (
        <>
            <PanelSection>
                <PanelSectionRow>
                    <ToggleField
                        label="Use as controller"
                        description={
                            needsUsbMode
                                ? "Turn on USB mode below first."
                                : undefined
                        }
                        checked={s.running}
                        disabled={busy || needsUsbMode}
                        onChange={(enabled) =>
                            withBusy(() => setEnabled(enabled))()
                        }
                    />
                </PanelSectionRow>
                {s.running && (
                    <PanelSectionRow>
                        <Field label={st.title} description={st.hint} />
                    </PanelSectionRow>
                )}
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
                            { data: "bluetooth", label: "Bluetooth" },
                            { data: "usb", label: "USB cable" },
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
                        layout="below"
                        description={
                            wired
                                ? "Only the PS5 types work over USB."
                                : "Each type is paired separately."
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

            <PanelSection title="While connected">
                <PanelSectionRow>
                    <Field
                        description={
                            <Lines
                                lines={[
                                    "Tap ⋯ (or screen) to turn the screen on or off.",
                                    "Hold ⋯ for 2 seconds to stop.",
                                    ...(wired
                                        ? []
                                        : [
                                              "Bluetooth controllers and keyboards paired to the Deck are paused.",
                                          ]),
                                ]}
                            />
                        }
                    />
                </PanelSectionRow>

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
                                <Field description="Turn on “Use as controller” to pair a device." />
                            </PanelSectionRow>
                        )}
                    </PanelSection>
                )}

                <PanelSection title="Options">
                    <PanelSectionRow>
                        <ToggleField
                            label="Screen off while connected"
                            checked={s.options.screen_off}
                            onChange={async (v) =>
                                setState(await setOption("screen_off", v))
                            }
                        />
                    </PanelSectionRow>
                </PanelSection>
            </PanelSection>

            <AboutSection />
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
