# Hardware — Muhammad

Responder device: an Arduino UNO R4 WiFi carried by a responder. It shows the
casualty a human approved and dispatched to that responder, alerts them, and
reports their explicit button presses back to the backend.

**Simulation only.** It follows Prince's simulation backend
(`/api/v1/simulations/{scenario}`), every event is `mode: "simulation"`, and
nothing reaches a real emergency service. The device never approves or
dispatches; pending, unapproved proposals are never shown.

## What the device does

| Moment | LED matrix | On-board LED | Responder does | Bridge reports |
| --- | --- | --- | --- | --- |
| Power-on | `AEGIS` scrolls once | off | — | — |
| Idle | one corner dot (`--` = not connected) | off | — | — |
| Self-test | `AEGIS 1 OK` scrolls (`--` instead of `OK` = not connected; the number is the device) | off | three quick taps while idle | nothing |
| Approved dispatch arrives | casualty label **flashes**, e.g. `A`, `C3` (fast = `immediate`) | blinks | — | `delivered` |
| Responder accepts | label steady | on | short press | `acknowledged` |
| Responder finishes | `OK`, then idle | off | hold 1.5 s | `completed` |

The device only uses the medic-confirmed priority for the fast flash, never a
camera suggestion. Its LED matrix is red: keep it facing away from the camera,
which detects fake blood by colour.

If the backend resets, completes, or replaces the task, the device clears. The
reports describe what the responder did, never the casualty's condition.

## Wiring

Only a Grove button is needed. Do **not** use a Grove Base Shield: it covers the
LED matrix. Connect the button with a **Grove-to-male-jumper** cable:

| Wire | Button pin | Arduino pin |
| --- | --- | --- |
| yellow | SIG | **2** |
| red | VCC | **5V** |
| black | GND | **GND** |
| white | NC | unused |

Power comes from a USB power bank (USB-C). A Grove LED on pin 4 is optional;
alerts use the on-board LED and the matrix. Never run the demo with nothing on
pin 2: a floating input can read as phantom presses.

## Enclosure

`enclosure/aegis_case.scad` is a parametric OpenSCAD case with AEGIS branding, a
window over the LED matrix (sized for both the UNO R4 WiFi and the UNO Q) and a
pocket for the Grove button. Two bodies share the same lid design:

- `part = "plate"`: straps onto the power bank (first device, Yoobao PDL20).
- `part = "plate2"`: a cradle the power bank slides into (second device, Rivacase VA2612).

Export with `openscad -D "part=\"plate\"" -o case.stl enclosure/aegis_case.scad`.
Print in PLA, no supports, with a 5-8 mm brim. Measure your power bank and set
`bank` / `bank2` first; STL files are not committed.

## Flash the firmware (once)

```sh
arduino-cli core install arduino:renesas_uno
arduino-cli lib install ArduinoGraphics
arduino-cli board list                      # find the port (Muhammad's board: COM13)
arduino-cli compile --upload --fqbn arduino:renesas_uno:unor4wifi -p COM13 hardware/firmware/aegis_responder
```

Always `compile --upload` together: a bare `upload` flashes whatever was compiled
last. If it says `No device found`, double-tap the board's RESET button and retry.

## Run it with the backend

From the repository root, in three terminals:

```sh
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000                 # 1. backend
uv run python -m scripts.live_demo --base-url http://127.0.0.1:8000 --prepare-only # 2. scene (once)
uv run --with pyserial python -m hardware.bridge --port COM13                     # 3. bridge (USB)
```

Without the board, replace step 3 with `uv run python -m hardware.bridge --sim`
and type `press` / `long` for the button. `--offline` runs with no backend and
manual `assign <casualty_id> [high|normal]` for testing. `--responder` picks
which responder the device belongs to (default `responder-001`, the one that
stays available in the two-actor demo). Type `status` for connection state,
delivery latency, posted/queued events and any rejections.

Do not run the full `scripts.live_demo --base-url` rehearsal while the bridge is
connected; it dispatches and acknowledges on its own.

## Wireless: the responder carries the device

The responder walks around with the Arduino, so it runs on a **USB power bank**
and talks to the bridge laptop over WiFi:

```
Arduino (responder, power bank) --WiFi--> bridge laptop's own hotspot (192.168.137.1:7777)
bridge laptop --Tailscale over campus WiFi--> backend laptop
```

The Arduino cannot join eduroam (WPA2-Enterprise) or click through a guest
sign-in page, and it only has 2.4 GHz. The bridge laptop therefore runs its own
2.4 GHz Windows Mobile Hotspot, which only the team's device joins, so campus
crowding does not matter and the laptop's address is always `192.168.137.1`.

1. Start the hotspot. The first run creates the git-ignored
   `firmware/aegis_responder/arduino_secrets.h` with the hotspot name and a random password:
   ```powershell
   .\hardware\scripts\start-hotspot.ps1          # -Status to inspect, -Stop to turn off
   ```
2. Flash the WiFi build (it is chosen automatically when `arduino_secrets.h` exists).
   Always compile and upload together so a stale build is never flashed:
   ```powershell
   arduino-cli compile --upload --fqbn arduino:renesas_uno:unor4wifi -p COM13 hardware/firmware/aegis_responder
   ```
   If the upload says `No device found`, **double-tap the board's RESET button**
   (the LED near it pulses) and run the command again within a few seconds.
3. Unplug the Arduino from the laptop, power it from a power bank, and start the bridge:
   ```powershell
   .\hardware\scripts\start-bridge.ps1 -Wifi -Backend <backend-machine-name>
   ```

Device behaviour on WiFi: the matrix shows `--` while it is not connected to the
bridge. The bridge pings every 2 s and treats 7 s of silence as "out of range".
The device rejoins on its own when back in range, keeps the assignment it was
showing, and re-sends a button press made out of range until the bridge confirms
it. While searching for WiFi the device stays responsive (the join runs in the
background), and USB keeps working as a fallback.

**Network rules:** Windows Mobile Hotspot shares the laptop's connection (eduroam).
Check the university allows it. The alternative is a phone hotspot that both the
Arduino and the bridge laptop join: set `BRIDGE_HOST` in `arduino_secrets.h` to the
laptop's address on it and run `start-bridge.ps1 -Wifi -WifiListen <that-ip>:7777`.
The device port is unauthenticated, so the bridge only listens on the hotspot
address, never on the campus network.

## Two responders

Each Arduino gets its own bridge on the same laptop. Device N reports as
`responder-device-0N`, connects to port `7777 + N - 1`, and follows `responder-00N`.

Flash the second board with its device number (the first board needs no flag):

```powershell
arduino-cli compile --upload --fqbn arduino:renesas_uno:unor4wifi -p <its COM port> `
  --build-property "compiler.cpp.extra_flags=-DAEGIS_DEVICE_NUM=2" hardware/firmware/aegis_responder
```

Then run one bridge per device, each in its own terminal:

```powershell
.\hardware\scripts\start-bridge.ps1 -Wifi -Local              # device 1 -> responder-001, port 7777
.\hardware\scripts\start-bridge.ps1 -Wifi -Local -Device 2    # device 2 -> responder-002, port 7778
```

### Second board is an Arduino UNO Q

The same sketch builds for the UNO Q (blue 13x8 matrix). Its microcontroller
reaches the bridge through the board's Linux side (`Arduino_RouterBridge`), so
there is no `arduino_secrets.h` WiFi join: put the Q's **Linux side** on the
hotspot instead (App Lab, or `nmcli device wifi connect <hotspot>` in its shell).

```powershell
arduino-cli core install arduino:zephyr
arduino-cli lib install Arduino_RouterBridge ArduinoGraphics
arduino-cli compile --upload --fqbn arduino:zephyr:unoq -p <its COM port> `
  --build-property "compiler.cpp.extra_flags=-DAEGIS_DEVICE_NUM=2" hardware/firmware/aegis_responder
```

Wiring is the same (button SIG on pin 2, 5V, GND). On the Q, `AEGIS` scrolls
continuously while it is linked and idle; `--` means it cannot reach the bridge.
After a power cycle the Q may rejoin another saved network (e.g. eduroam) and
show `--`: move its Linux side back to the hotspot.

Both boards join the same hotspot. The full `scripts.live_demo` rehearsal takes
`responder-002` offline, so use `--prepare-only` when both devices should get tasks.

## Host the whole demo on this laptop (hotspot)

For the rehearsal and demo, the Windows laptop that runs the hotspot also hosts
the backend, dashboard, Voice and both bridges. Teammates join the
`AEGIS-RESPONDER` hotspot; nothing listens on the campus network.

```powershell
.\hardware\scripts\start-demo-host.ps1            # start everything, then health checks
.\hardware\scripts\start-demo-host.ps1 -Status    # health checks only
.\hardware\scripts\start-demo-host.ps1 -Stop      # stop what it started
.\hardware\scripts\start-demo-host.ps1 -Prepare   # also load the two-actor scene (resets the scenario)
```

| Service | Address | Who uses it |
| --- | --- | --- |
| backend | `http://192.168.137.1:8000` | bridges, dashboard, camera (`--base-url`) |
| dashboard | `http://192.168.137.1:3000/command` | teammates on the hotspot |
| dashboard | `http://localhost:3000/command` | this laptop (browser microphone needs localhost) |
| Voice | `http://127.0.0.1:8010` | reached through the dashboard only |
| bridges | `192.168.137.1:7777`, `:7778` | the two Arduinos |

Logs are in `hardware/logs/demo-host/`. Run it **once from an Administrator
PowerShell** so it can add the firewall rule that lets hotspot devices (and only
those) reach ports 8000 and 3000. `voice/.env` and the neural weights are not in
git; without them Voice is text-only and `-Neural` is skipped. Close any old
backend or bridge windows first: the script refuses to start on a busy port.

## On university WiFi: Tailscale

Campus WiFi is crowded and usually blocks laptops from reaching each other.
The Arduino also cannot join eduroam (its WiFi library has no WPA2-Enterprise)
or click through the `puvisitor` sign-in page. So:

- The **Arduino stays on USB** to the bridge laptop: no WiFi involved at all.
- **Laptops talk over Tailscale**, a private team network that works on campus
  WiFi, a phone hotspot, or a mix, and survives roaming between access points.

One-time setup on **every** laptop (backend, bridge, dashboard, camera):

```powershell
winget install --id tailscale.tailscale
tailscale up        # log in with the same team account
tailscale status    # everyone should see everyone
```

Backend laptop (binds to its Tailscale address only, never the whole campus network):

```powershell
.\hardware\scripts\host-backend.ps1
```

Bridge laptop (checks Tailscale, waits for the backend, finds the Arduino, and
restarts the bridge if it ever exits with an error):

```powershell
.\hardware\scripts\start-bridge.ps1 -Backend <backend-machine-name>   # name from `tailscale status`
.\hardware\scripts\start-bridge.ps1 -Backend <name> -CheckOnly        # just run the checks
.\hardware\scripts\start-bridge.ps1 -Local                            # backend on this laptop
```

The backend listens on port 8000. If the team reaches it through the private
proxy instead, that is port 8011: add `-BackendPort 8011` (the proxy must pass
`/health` and `/api/v1/...` through unchanged). Device ports 7777-7780 are
separate and never go through the proxy.

If PowerShell refuses to run scripts, use
`powershell -ExecutionPolicy Bypass -File hardware\scripts\start-bridge.ps1 ...`.

On a bad network the bridge never crashes: an HTML sign-in page or a 502/503/504
counts as "backend unreachable", polling backs off to at most every 5 s, device
events queue in order and are delivered once the backend is back, and the
Arduino keeps working (button presses are kept, not lost).

## How it connects

The bridge polls `GET /state` about every 0.4 s and shows the newest active
assignment for its responder. Device reports are posted to
`POST /api/v1/simulations/{scenario}/device-events` with every correlation field
(`device_id`, `dispatch_id`, `command_id`, `casualty_id`, `responder_id`)
copied from that assignment, and a deterministic event ID used as the
`Idempotency-Key`, so retries resend the identical payload. If the backend is
down, events queue in order and retry every second. A 4xx rejection is logged
and never retried blindly. Events are also logged to
`hardware/logs/device-events.jsonl` (git-ignored).

## Serial protocol (115200 baud, one line per message)

| Direction | Line | Reply |
| --- | --- | --- |
| host → device | `HELLO` | `HELLO aegis-responder fw=<v> device=<id>` |
| host → device | `ASSIGN <cmd> <label> <high\|normal>` | `DELIVERED <cmd>` |
| host → device | `CANCEL <cmd>` | `CANCELLED <cmd>` |
| host → device | `RESET` | `CLEARED` |
| host → device | `PING` | `PONG` |
| device → host | `ACK <cmd>` / `DONE <cmd>` | `OK ACK <cmd>` / `OK DONE <cmd>` |
| device → host | `ERR <reason>` | — |

`<cmd>` is `e<epoch>-<backend command_id>`, so a scenario reset can never be
confused with an earlier task. `ASSIGN` is resent until `DELIVERED`; `ACK`/`DONE`
are resent every second until `OK`. On reconnect the bridge re-shows an
unfinished task, or sends `RESET` if nothing should be shown.

## Tests

```sh
uv run pytest hardware/tests
```

These run the bridge against the real backend app in-process, with a simulated device.
