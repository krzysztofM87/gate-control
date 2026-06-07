#include <Arduino.h>
#include <WiFi.h>

#include "../include/terminal_config.h"
#include "../include/wifi_manager.h"
#include "../include/config_portal.h"
#include "../include/api_client.h"
#include "../include/gate_control.h"

static DeviceConfig *activeConfig = nullptr;
static String cliBuffer = "";

static void printPrompt() {
  Serial.print("gate> ");
}

static void printHelp() {
  Serial.println();
  Serial.println("===== ESP32 Gate Terminal Config =====");
  Serial.println("Commands:");
  Serial.println("  help");
  Serial.println("  show");
  Serial.println("  wifi SSID|PASSWORD");
  Serial.println("  server https://tools.malmaz.com/gate");
  Serial.println("  device DEVICE_ID|DEVICE_SECRET");
  Serial.println("  save");
  Serial.println("  reconnect");
  Serial.println("  clear");
  Serial.println("  reboot");
  Serial.println("  portal");
  Serial.println("  open1");
  Serial.println("  open2");
  Serial.println("  openboth");
  Serial.println("  exit");
  Serial.println();
  Serial.println("Examples:");
  Serial.println("  wifi MojaSiec|tajnehaslo");
  Serial.println("  server https://tools.malmaz.com/gate");
  Serial.println("  device esp32-brama-1|sekret-urzadzenia");
  Serial.println("  save");
  Serial.println("  reboot");
  Serial.println("======================================");
  Serial.println();
}

static void printConfig() {
  if (activeConfig == nullptr) {
    Serial.println("[CLI] No active config pointer");
    return;
  }

  Serial.println();
  Serial.println("Current config:");
  Serial.print("  wifi_ssid: ");
  Serial.println(activeConfig->wifiSsid);

  Serial.print("  wifi_password: ");
  if (activeConfig->wifiPassword.length() > 0) {
    Serial.print("(hidden, len=");
    Serial.print(activeConfig->wifiPassword.length());
    Serial.println(")");
  } else {
    Serial.println("(empty)");
  }

  Serial.print("  server_url: ");
  Serial.println(activeConfig->serverUrl);

  Serial.print("  device_id: ");
  Serial.println(activeConfig->deviceId);

  Serial.print("  device_secret: ");
  if (activeConfig->deviceSecret.length() > 0) {
    Serial.print("(hidden, len=");
    Serial.print(activeConfig->deviceSecret.length());
    Serial.println(")");
  } else {
    Serial.println("(empty)");
  }

  Serial.print("  complete: ");
  Serial.println(activeConfig->isComplete() ? "yes" : "no");

  Serial.print("  wifi_status: ");
  Serial.println(WiFi.status() == WL_CONNECTED ? "connected" : "not connected");

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("  ip: ");
    Serial.println(WiFi.localIP());
  }

  Serial.println();
}

static bool readLineNonBlocking(String &lineOut) {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\r') {
      continue;
    }

    if (c == '\n') {
      cliBuffer.trim();
      lineOut = cliBuffer;
      cliBuffer = "";
      return true;
    }

    if (cliBuffer.length() < 512) {
      cliBuffer += c;
    }
  }

  return false;
}

static String getArgsAfterCommand(const String &line, const String &command) {
  String args = line.substring(command.length());
  args.trim();
  return args;
}

static bool parsePair(const String &args, String &left, String &right) {
  int sep = args.indexOf('|');

  if (sep < 0) {
    return false;
  }

  left = args.substring(0, sep);
  right = args.substring(sep + 1);

  left.trim();
  right.trim();

  return left.length() > 0;
}

// Zwraca true, gdy należy wyjść z trybu blokującego.
static bool processTerminalCommand(String line, bool blockingMode) {
  line.trim();

  if (line.length() == 0) {
    return false;
  }

  if (activeConfig == nullptr) {
    Serial.println("[CLI] Config not initialized");
    return false;
  }

  String lower = line;
  lower.toLowerCase();

  if (lower == "help" || lower == "?") {
    printHelp();
    return false;
  }

  if (lower == "show") {
    printConfig();
    return false;
  }

  if (lower.startsWith("wifi ")) {
    String args = getArgsAfterCommand(line, "wifi");
    String ssid;
    String password;

    if (!parsePair(args, ssid, password)) {
      Serial.println("[CLI] Invalid format. Use:");
      Serial.println("  wifi SSID|PASSWORD");
      return false;
    }

    activeConfig->wifiSsid = ssid;
    activeConfig->wifiPassword = password;

    Serial.println("[CLI] WiFi config set in RAM. Use 'save' to store.");
    return false;
  }

  if (lower.startsWith("server ")) {
    String url = getArgsAfterCommand(line, "server");

    if (url.length() == 0) {
      Serial.println("[CLI] Missing server URL");
      return false;
    }

    while (url.endsWith("/")) {
      url.remove(url.length() - 1);
    }

    activeConfig->serverUrl = url;

    Serial.println("[CLI] Server URL set in RAM. Use 'save' to store.");
    return false;
  }

  if (lower.startsWith("device ")) {
    String args = getArgsAfterCommand(line, "device");
    String deviceId;
    String deviceSecret;

    if (!parsePair(args, deviceId, deviceSecret)) {
      Serial.println("[CLI] Invalid format. Use:");
      Serial.println("  device DEVICE_ID|DEVICE_SECRET");
      return false;
    }

    activeConfig->deviceId = deviceId;
    activeConfig->deviceSecret = deviceSecret;

    Serial.println("[CLI] Device config set in RAM. Use 'save' to store.");
    return false;
  }

  if (lower == "save") {
    if (!activeConfig->isComplete()) {
      Serial.println("[CLI] Config is incomplete. Required:");
      Serial.println("  wifi SSID|PASSWORD");
      Serial.println("  server URL");
      Serial.println("  device ID|SECRET");
      return false;
    }

    bool ok = saveDeviceConfig(*activeConfig);

    if (ok) {
      Serial.println("[CLI] Config saved to NVS");
    } else {
      Serial.println("[CLI] Save failed");
    }

    return false;
  }

  if (lower == "reconnect") {
    Serial.println("[CLI] Reconnecting WiFi...");

    bool wifiOk = connectToConfiguredWiFi(*activeConfig);

    if (wifiOk) {
      setupApiClient(*activeConfig);
      Serial.println("[CLI] Reconnected");
    } else {
      Serial.println("[CLI] Reconnect failed");
    }

    return false;
  }

  if (lower == "clear") {
    clearDeviceConfig();
    activeConfig->wifiSsid = "";
    activeConfig->wifiPassword = "";
    activeConfig->serverUrl = "";
    activeConfig->deviceId = "";
    activeConfig->deviceSecret = "";
    Serial.println("[CLI] Config cleared from NVS");
    return false;
  }

  if (lower == "reboot" || lower == "restart") {
    Serial.println("[CLI] Rebooting...");
    delay(500);
    ESP.restart();
    return true;
  }

  if (lower == "portal") {
    Serial.println("[CLI] Starting config portal...");
    delay(300);
    runConfigPortal(*activeConfig);
    return true;
  }

  if (lower == "open1") {
    Serial.println("[CLI] Test pulse gate 1");
    triggerGate(GATE_TARGET_1);
    return false;
  }

  if (lower == "open2") {
    Serial.println("[CLI] Test pulse gate 2");
    triggerGate(GATE_TARGET_2);
    return false;
  }

  if (lower == "openboth") {
    Serial.println("[CLI] Test pulse both gates");
    triggerGate(GATE_TARGET_BOTH);
    return false;
  }

  if (lower == "exit" || lower == "continue") {
    if (blockingMode) {
      Serial.println("[CLI] Leaving terminal config");
      return true;
    }

    Serial.println("[CLI] Not in blocking terminal mode");
    return false;
  }

  if (lower == "terminal" || lower == "cli") {
    printHelp();
    return false;
  }

  Serial.print("[CLI] Unknown command: ");
  Serial.println(line);
  Serial.println("[CLI] Type 'help'");
  return false;
}

void setupTerminalConfig(DeviceConfig *config) {
  activeConfig = config;

  Serial.println("[CLI] Terminal config ready. Type 'help' in Serial Monitor.");
}

void handleTerminalConfig() {
  String line;

  if (readLineNonBlocking(line)) {
    processTerminalCommand(line, false);
    printPrompt();
  }
}

bool runTerminalConfigWindow(DeviceConfig &config, uint32_t timeoutMs) {
  activeConfig = &config;

  Serial.println();
  Serial.println("[CLI] Serial configuration window");
  Serial.print("[CLI] Type 'terminal' or 'help' within ");
  Serial.print(timeoutMs / 1000);
  Serial.println(" seconds to configure by terminal.");
  Serial.println("[CLI] Otherwise ESP32 will start WiFi config portal.");
  Serial.println();

  unsigned long start = millis();

  while (millis() - start < timeoutMs) {
    String line;

    if (readLineNonBlocking(line)) {
      Serial.println("[CLI] Entering terminal configuration");
      printHelp();

      if (line.length() > 0 && line != "terminal" && line != "cli" && line != "help") {
        processTerminalCommand(line, true);
      }

      printPrompt();

      while (true) {
        String commandLine;

        if (readLineNonBlocking(commandLine)) {
          bool shouldExit = processTerminalCommand(commandLine, true);
          if (shouldExit) {
            return true;
          }

          printPrompt();
        }

        delay(10);
      }
    }

    delay(10);
  }

  Serial.println("[CLI] No terminal input");
  return false;
}
