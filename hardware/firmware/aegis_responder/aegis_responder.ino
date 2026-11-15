// AEGIS responder device firmware — Arduino UNO R4 WiFi + Grove Base Shield.
//
// The device is carried by a responder. It only displays assignments the
// laptop bridge sends and reports button presses back. It never decides,
// approves, or dispatches anything.
//
// Links (both active; replies go to whichever link the host is using):
//   USB serial at 115200 baud, always.
//   WiFi + TCP to the bridge, when arduino_secrets.h exists (see
//   arduino_secrets.example.h). The bridge laptop runs a 2.4 GHz hotspot; the
//   device joins it and connects to BRIDGE_HOST:BRIDGE_PORT. It reconnects on
//   its own after walking out of range.
//
// Wiring: Grove button -> D2 (via a Grove Base Shield, or a Grove-to-jumper
// cable: yellow SIG -> D2, red VCC -> 5V, black GND -> GND).
// Alerts use the on-board LED and the LED matrix; a Grove LED on D4 is optional.
//
// Line protocol (one ASCII line per message, '\n' terminated):
//   Host -> device
//     HELLO                          -> HELLO aegis-responder fw=<ver> device=<id> link=<usb|wifi>
//     PING                           -> PONG
//     ASSIGN <cmd> <label> <urgency> -> DELIVERED <cmd>   (urgency: high|normal)
//     CANCEL <cmd>                   -> CANCELLED <cmd>
//     RESET                          -> CLEARED  (drop whatever is shown)
//     OK <kind> <cmd>                -> (stops re-sending that ACK/DONE report)
//   Device -> host
//     ACK <cmd>    short press while an assignment is shown
//     DONE <cmd>   long press after ACK
//     ERR <reason>
// ACK and DONE are re-sent every RETRY_MS until the host answers OK, including
// across a lost link, so a press made out of range is delivered on reconnect.

#include "ArduinoGraphics.h"
#include "Arduino_LED_Matrix.h"

#if __has_include("arduino_secrets.h")
#include "arduino_secrets.h"
#include <WiFiS3.h>
#define AEGIS_WIFI 1
#else
#define AEGIS_WIFI 0
#endif

// Several devices on one bridge laptop: build device N with
//   --build-property "compiler.cpp.extra_flags=-DAEGIS_DEVICE_NUM=N"
// Device N reports as "responder-device-0N" and connects to BRIDGE_PORT + N - 1,
// so each device talks to its own bridge. The default is device 1.
#ifndef AEGIS_DEVICE_NUM
#define AEGIS_DEVICE_NUM 1
#endif
#define AEGIS_STR2(x) #x
#define AEGIS_STR(x) AEGIS_STR2(x)

const char *FW_VERSION = "0.2.2";
const char *DEVICE_ID = "responder-device-0" AEGIS_STR(AEGIS_DEVICE_NUM);

const int BUTTON_PIN = 2;
const int LED_PIN = 4;                   // optional external Grove LED
const int ONBOARD_LED = LED_BUILTIN;     // the board's own "L" LED, always present

const unsigned long DEBOUNCE_MS = 30;
const unsigned long LONG_PRESS_MS = 1500;
const unsigned long RETRY_MS = 1000;
const unsigned long BLINK_FAST_MS = 150;
const unsigned long BLINK_SLOW_MS = 500;
const unsigned long WIFI_RETRY_MS = 15000;  // between WiFi join attempts (the join runs in the background)
const unsigned long WIFI_CHECK_MS = 500;    // how often to ask the WiFi chip for its status
const unsigned long TCP_RETRY_MS = 2000;    // between bridge connection attempts
const unsigned long HOST_SILENCE_MS = 8000; // bridge pings every 2 s; silence = dead link

const unsigned long BANNER_STEP_MS = 70;    // scroll speed of the "AEGIS" banner
const unsigned long TAP_WINDOW_MS = 600;    // three taps this close together = self-test

const int CMD_MAX = 40;
const int LABEL_MAX = 4;
const int LINE_MAX = 96;

ArduinoLEDMatrix matrix;

enum State { IDLE, ALERTING, ACKED };
State state = IDLE;

char commandId[CMD_MAX + 1] = "";
char label[LABEL_MAX + 1] = "";
bool urgent = false;

char pendingKind[5] = "";  // "ACK" or "DONE" awaiting host OK
unsigned long pendingSentAt = 0;

struct LineReader {
  char buf[LINE_MAX + 1];
  int len = 0;
  bool overflow = false;
};
LineReader serialReader;

bool buttonStable = HIGH;
bool buttonLastRead = HIGH;
unsigned long buttonChangedAt = 0;
unsigned long pressStartedAt = 0;
bool longPressFired = false;

bool ledOn = false;
unsigned long ledToggledAt = 0;

char bannerText[16] = "";
bool bannerActive = false;
int bannerX = 0;
unsigned long bannerStepAt = 0;
int tapCount = 0;
unsigned long lastTapAt = 0;

#if AEGIS_WIFI
WiFiClient bridge;
LineReader tcpReader;
unsigned long lastWifiAttempt = 0;
unsigned long lastWifiCheck = 0;
unsigned long lastTcpAttempt = 0;
unsigned long lastHostMessage = 0;
bool wifiJoinStarted = false;
bool wifiUp = false;
bool tcpUp = false;
#endif

void showIdle();

// ---------------------------------------------------------------- output

// Every reply goes to USB and, when connected, to the bridge over WiFi.
void emitLine(const char *a, const char *b = nullptr, const char *c = nullptr) {
  Serial.print(a);
  if (b) { Serial.print(' '); Serial.print(b); }
  if (c) { Serial.print(' '); Serial.print(c); }
  Serial.println();
#if AEGIS_WIFI
  if (tcpUp) {
    bridge.print(a);
    if (b) { bridge.print(' '); bridge.print(b); }
    if (c) { bridge.print(' '); bridge.print(c); }
    bridge.print('\n');
  }
#endif
}

bool linkUp() {
#if AEGIS_WIFI
  return tcpUp;
#else
  return true;  // USB-only build: the host is assumed present
#endif
}

// ---------------------------------------------------------------- display

void showText(const char *text) {
  matrix.beginDraw();
  matrix.clear();
  matrix.stroke(0xFFFFFFFF);
  matrix.textFont(Font_4x6);
  matrix.beginText(0, 1, 0xFFFFFF);
  matrix.print(text);
  matrix.endText();
  matrix.endDraw();
}

void showIdle() {
  if (bannerActive) return;  // the banner returns here when it finishes
  if (!linkUp()) {
    showText("--");  // not connected to the bridge
    return;
  }
  matrix.beginDraw();
  matrix.clear();
  matrix.point(11, 7);  // single "alive and linked" pixel
  matrix.endDraw();
}

// "AEGIS" scrolls across the matrix at power-on. Three quick taps while idle
// scroll it again with the device number and link state ("AEGIS 1 OK" or
// "AEGIS 1 --") as a self-test. It never blocks, only runs while idle, and a
// new task cancels it at once. It sends nothing to the host.
void startBanner(bool withStatus) {
  if (withStatus) {
    snprintf(bannerText, sizeof(bannerText), "AEGIS %d %s", AEGIS_DEVICE_NUM, linkUp() ? "OK" : "--");
  } else {
    strcpy(bannerText, "AEGIS");
  }
  bannerActive = true;
  bannerX = 12;
  bannerStepAt = 0;
}

void updateBanner() {
  if (!bannerActive) return;
  if (state != IDLE) { bannerActive = false; return; }
  if (millis() - bannerStepAt < BANNER_STEP_MS) return;
  bannerStepAt = millis();
  if (bannerX < -4 * (int)strlen(bannerText)) {
    bannerActive = false;
    showIdle();
    return;
  }
  matrix.beginDraw();
  matrix.clear();
  matrix.stroke(0xFFFFFFFF);
  matrix.textFont(Font_4x6);
  matrix.beginText(bannerX, 1, 0xFFFFFF);
  matrix.print(bannerText);
  matrix.endText();
  matrix.endDraw();
  bannerX--;
}

// ---------------------------------------------------------------- reports

void sendReport() {
  emitLine(pendingKind, commandId);
  pendingSentAt = millis();
}

void queueReport(const char *kind) {
  strncpy(pendingKind, kind, sizeof(pendingKind) - 1);
  pendingKind[sizeof(pendingKind) - 1] = '\0';
  sendReport();
}

void retryPendingReport() {
  // Keep trying until the host confirms; a press made out of range is not lost.
  if (pendingKind[0] != '\0' && millis() - pendingSentAt >= RETRY_MS) sendReport();
}

void setLed(bool on) {
  ledOn = on;
  digitalWrite(LED_PIN, on ? HIGH : LOW);
  digitalWrite(ONBOARD_LED, on ? HIGH : LOW);
}

void clearAssignment() {
  state = IDLE;
  commandId[0] = '\0';
  label[0] = '\0';
  urgent = false;
  setLed(false);
  showIdle();
}

// ---------------------------------------------------------------- commands

int tokenize(char *buf, char **tokens, int maxTokens) {
  int count = 0;
  char *p = buf;
  while (*p && count < maxTokens) {
    while (*p == ' ') p++;
    if (!*p) break;
    tokens[count++] = p;
    while (*p && *p != ' ') p++;
    if (*p) *p++ = '\0';
  }
  return count;
}

void handleLine(char *line, const char *link) {
  char *t[5];
  int n = tokenize(line, t, 5);
  if (n == 0) return;

  if (strcmp(t[0], "HELLO") == 0) {
    char info[80];
    snprintf(info, sizeof(info), "fw=%s device=%s link=%s", FW_VERSION, DEVICE_ID, link);
    emitLine("HELLO aegis-responder", info);
  } else if (strcmp(t[0], "PING") == 0) {
    emitLine("PONG");
  } else if (strcmp(t[0], "ASSIGN") == 0) {
    if (n != 4 || strlen(t[1]) > CMD_MAX || strlen(t[2]) > LABEL_MAX) {
      emitLine("ERR bad_assign");
      return;
    }
    // A repeated ASSIGN for the command already shown is a host retry or a
    // reconnect: confirm again without resetting the responder's progress.
    if (strcmp(t[1], commandId) != 0) {
      strcpy(commandId, t[1]);
      strcpy(label, t[2]);
      urgent = strcmp(t[3], "high") == 0;
      state = ALERTING;
      pendingKind[0] = '\0';
      showText(label);
    }
    emitLine("DELIVERED", commandId);
  } else if (strcmp(t[0], "CANCEL") == 0) {
    if (n == 2 && strcmp(t[1], commandId) == 0) {
      emitLine("CANCELLED", t[1]);
      pendingKind[0] = '\0';
      clearAssignment();
    } else {
      emitLine("ERR unknown_command");
    }
  } else if (strcmp(t[0], "RESET") == 0) {
    pendingKind[0] = '\0';
    clearAssignment();
    emitLine("CLEARED");
  } else if (strcmp(t[0], "OK") == 0) {
    if (n == 3 && strcmp(t[1], pendingKind) == 0 && strcmp(t[2], commandId) == 0) {
      bool wasDone = strcmp(pendingKind, "DONE") == 0;
      pendingKind[0] = '\0';
      if (wasDone) clearAssignment();
    }
  } else {
    emitLine("ERR unknown_verb");
  }
}

void feed(LineReader &r, char c, const char *link) {
  if (c == '\r') return;
  if (c == '\n') {
    if (!r.overflow) {
      r.buf[r.len] = '\0';
      handleLine(r.buf, link);
    } else {
      emitLine("ERR line_too_long");
    }
    r.len = 0;
    r.overflow = false;
  } else if (r.len < LINE_MAX) {
    r.buf[r.len++] = c;
  } else {
    r.overflow = true;
  }
}

void readSerial() {
  while (Serial.available() > 0) feed(serialReader, (char)Serial.read(), "usb");
}

// ---------------------------------------------------------------- WiFi link

#if AEGIS_WIFI
void setTcpUp(bool up) {
  if (up == tcpUp) return;
  tcpUp = up;
  if (state == IDLE) showIdle();
}

// Never blocks for long: USB, the button and the LED must keep working while
// the device searches for the hotspot (e.g. while the responder is out of range).
void maintainWifi() {
  unsigned long now = millis();
  if (now - lastWifiCheck < WIFI_CHECK_MS) return;
  lastWifiCheck = now;

  wifiUp = WiFi.status() == WL_CONNECTED;
  if (!wifiUp) {
    if (tcpUp) { bridge.stop(); setTcpUp(false); }
    if (!wifiJoinStarted || now - lastWifiAttempt >= WIFI_RETRY_MS) {
      wifiJoinStarted = true;
      lastWifiAttempt = now;
      // setTimeout(0) in setup(): begin() starts the join and returns at once;
      // the WiFi chip keeps joining in the background and status() reports it.
      WiFi.begin(SECRET_SSID, SECRET_PASS);
    }
    return;
  }
  if (tcpUp && (!bridge.connected() || now - lastHostMessage > HOST_SILENCE_MS)) {
    bridge.stop();  // half-open link after roaming or a laptop sleep
    setTcpUp(false);
  }
  if (!tcpUp && now - lastTcpAttempt >= TCP_RETRY_MS) {
    lastTcpAttempt = now;
    if (bridge.connect(BRIDGE_HOST, BRIDGE_PORT + AEGIS_DEVICE_NUM - 1)) {
      tcpReader.len = 0;
      tcpReader.overflow = false;
      lastHostMessage = millis();
      setTcpUp(true);
      // The bridge sends HELLO next; any unconfirmed ACK/DONE is re-sent by retryPendingReport.
    }
  }
}

void readTcp() {
  if (!tcpUp) return;
  while (bridge.available() > 0) {
    lastHostMessage = millis();
    feed(tcpReader, (char)bridge.read(), "wifi");
  }
}
#endif

// ---------------------------------------------------------------- button

void onShortPress() {
  if (state == ALERTING) {
    state = ACKED;
    setLed(true);
    showText(label);  // stop flashing: the label stays on while the responder is on the way
    queueReport("ACK");
  } else if (state == IDLE) {
    unsigned long now = millis();
    tapCount = (now - lastTapAt <= TAP_WINDOW_MS) ? tapCount + 1 : 1;
    lastTapAt = now;
    if (tapCount >= 3) {
      tapCount = 0;
      startBanner(true);
    }
  }
}

void onLongPress() {
  if (state == ACKED && pendingKind[0] == '\0') {
    queueReport("DONE");
    showText("OK");
  }
}

void readButton() {
  // Grove button reads HIGH when pressed; normalise so LOW means pressed.
  bool reading = !digitalRead(BUTTON_PIN);
  unsigned long now = millis();

  if (reading != buttonLastRead) {
    buttonChangedAt = now;
    buttonLastRead = reading;
  }
  if (now - buttonChangedAt < DEBOUNCE_MS || reading == buttonStable) {
    if (buttonStable == LOW && !longPressFired && now - pressStartedAt >= LONG_PRESS_MS) {
      longPressFired = true;
      onLongPress();
    }
    return;
  }

  buttonStable = reading;
  if (buttonStable == LOW) {
    pressStartedAt = now;
    longPressFired = false;
  } else if (!longPressFired) {
    onShortPress();
  }
}

// ---------------------------------------------------------------- LED

// While a new task is waiting for acknowledgement, the LEDs blink and the
// matrix label flashes with them, so the alert is visible with no external LED.
void updateLed() {
  if (state != ALERTING) return;
  unsigned long period = urgent ? BLINK_FAST_MS : BLINK_SLOW_MS;
  if (millis() - ledToggledAt >= period) {
    ledToggledAt = millis();
    setLed(!ledOn);
    if (ledOn) {
      showText(label);
    } else {
      matrix.beginDraw();
      matrix.clear();
      matrix.endDraw();
    }
  }
}

// ---------------------------------------------------------------- main

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  pinMode(ONBOARD_LED, OUTPUT);
  matrix.begin();
#if AEGIS_WIFI
  WiFi.setTimeout(0);  // make WiFi.begin() non-blocking (see maintainWifi)
#endif
  clearAssignment();
  startBanner(false);
}

void loop() {
  readSerial();
#if AEGIS_WIFI
  maintainWifi();
  readTcp();
#endif
  readButton();
  updateLed();
  updateBanner();
  retryPendingReport();
}
