#include <Arduino.h>
#include <WiFi.h>
#include <esp_system.h>

#include "include/config.h"
#include "include/debug_led.h"
#include "include/gate_control.h"
#include "include/wifi_manager.h"
#include "include/config_portal.h"
#include "include/api_client.h"
#include "include/terminal_config.h"

DeviceConfig deviceConfig;

unsigned long lastPollAt = 0;
unsigned long lastWifiReconnectAt = 0;
unsigned long lastStatusPrintAt = 0;
unsigned long lastGoodApiAt = 0;

uint16_t apiFailCount = 0;
uint16_t wifiReconnectCount = 0;

static bool isBootButtonPressed() {
  return digitalRead(BOOT_BUTTON_PIN) == LOW;
}

static String getHostFromUrl(const String &url) {
  String work = url;
  work.trim();

  work.replace("http://", "");
  work.replace("https://", "");

  int slash = work.indexOf('/');
  if (slash >= 0) {
    work = work.substring(0, slash);
  }

  int colon = work.indexOf(':');
  if (colon >= 0) {
    work = work.substring(0, colon);
  }

  return work;
}

static void setupPowerSave() {
#if CPU_POWER_SAVE_ENABLED
  setCpuFrequencyMhz(CPU_FREQUENCY_MHZ);

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.print("[POWER] CPU frequency MHz: ");
    Serial.println(getCpuFrequencyMhz());
  }
#endif
}

static void printStartupInfo() {
  Serial.println();
  Serial.println("=================================");
  Serial.println("ESP32 Gate Controller");
  Serial.println("=================================");
  Serial.print("Chip ID: ");
  Serial.println(getChipId());
  Serial.print("Debug LED GPIO: ");
  Serial.println(DEBUG_LED_PIN);
  Serial.print("Gate 1 output GPIO: ");
  Serial.println(GATE1_OUTPUT_PIN);
  Serial.print("Gate 2 output GPIO: ");
  Serial.println(GATE2_OUTPUT_PIN);
  Serial.println();
}

static void printWifiInfo() {
  if (LOG_LEVEL < LOG_LEVEL_INFO) {
    return;
  }

  Serial.println("[WIFI] Network info:");

  Serial.print("[WIFI] SSID: ");
  Serial.println(WiFi.SSID());

  Serial.print("[WIFI] IP: ");
  Serial.println(WiFi.localIP());

  Serial.print("[WIFI] Gateway: ");
  Serial.println(WiFi.gatewayIP());

  Serial.print("[WIFI] DNS: ");
  Serial.println(WiFi.dnsIP());

  Serial.print("[WIFI] RSSI: ");
  Serial.println(WiFi.RSSI());

  String host = getHostFromUrl(deviceConfig.serverUrl);

  if (host.length() > 0) {
    IPAddress resolvedIp;
    bool dnsOk = WiFi.hostByName(host.c_str(), resolvedIp);

    Serial.print("[DNS] ");
    Serial.print(host);
    Serial.print(": ");

    if (dnsOk) {
      Serial.println(resolvedIp);
    } else {
      Serial.println("FAILED");
    }
  }
}

static void printPeriodicStatus() {
  unsigned long now = millis();

  if (now - lastStatusPrintAt < STATUS_PRINT_INTERVAL_MS) {
    return;
  }

  lastStatusPrintAt = now;

  if (LOG_LEVEL < LOG_LEVEL_INFO) {
    return;
  }

  Serial.print("[STATUS] WiFi=");
  Serial.print(WiFi.status() == WL_CONNECTED ? "connected" : "disconnected");

  Serial.print(" RSSI=");
  Serial.print(WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0);

  Serial.print(" apiFailCount=");
  Serial.print(apiFailCount);

  Serial.print(" wifiReconnectCount=");
  Serial.print(wifiReconnectCount);

  Serial.print(" freeHeap=");
  Serial.print(ESP.getFreeHeap());

  Serial.print(" lastGoodApiMsAgo=");
  if (lastGoodApiAt == 0) {
    Serial.println("never");
  } else {
    Serial.println(now - lastGoodApiAt);
  }
}

static bool reconnectWifiAndApi(bool force) {
  unsigned long now = millis();

  if (!force && now - lastWifiReconnectAt < WIFI_RECONNECT_MIN_INTERVAL_MS) {
    if (LOG_LEVEL >= LOG_LEVEL_WARN) {
      Serial.println("[MAIN] Reconnect skipped, too soon");
    }

    return WiFi.status() == WL_CONNECTED;
  }

  lastWifiReconnectAt = now;
  wifiReconnectCount++;

  if (LOG_LEVEL >= LOG_LEVEL_WARN) {
    Serial.println("[MAIN] Reconnecting WiFi and API client");
  }

  WiFi.disconnect(false, false);
  delay(800);

  bool wifiOk = connectToConfiguredWiFi(deviceConfig);

  if (wifiOk) {
    printWifiInfo();
    setupApiClient(deviceConfig);

    if (LOG_LEVEL >= LOG_LEVEL_INFO) {
      Serial.println("[MAIN] WiFi/API reconnect OK");
    }

    return true;
  }

  if (LOG_LEVEL >= LOG_LEVEL_WARN) {
    Serial.println("[MAIN] WiFi reconnect failed");
  }

  return false;
}

static void handleWifiHealth() {
  if (WiFi.status() != WL_CONNECTED) {
    if (LOG_LEVEL >= LOG_LEVEL_WARN) {
      Serial.println("[WIFI] Lost connection");
    }

    reconnectWifiAndApi(false);
    return;
  }

  int rssi = WiFi.RSSI();

  if (rssi != 0 && rssi < WIFI_RSSI_WARN_DBM && LOG_LEVEL >= LOG_LEVEL_WARN) {
    Serial.print("[WIFI] Weak signal RSSI=");
    Serial.println(rssi);
  }
}

static void handleApiFailure(int httpCode) {
  apiFailCount++;

  if (LOG_LEVEL >= LOG_LEVEL_WARN) {
    Serial.print("[MAIN] API fail count=");
    Serial.print(apiFailCount);
    Serial.print(" httpCode=");
    Serial.println(httpCode);
  }

  if (apiFailCount >= API_FAILS_BEFORE_REBOOT) {
    if (LOG_LEVEL >= LOG_LEVEL_ERROR) {
      Serial.println("[MAIN] Too many API failures, restarting ESP32");
    }

    delay(500);
    ESP.restart();
  }

  if (apiFailCount >= API_FAILS_BEFORE_WIFI_RECONNECT) {
    reconnectWifiAndApi(false);
  }
}

static void handleApiSuccess() {
  lastGoodApiAt = millis();

  if (apiFailCount > 0 && LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.println("[MAIN] API recovered");
  }

  apiFailCount = 0;
}

void setup() {
  Serial.begin(115200);
  delay(800);

  setupPowerSave();

  pinMode(BOOT_BUTTON_PIN, INPUT_PULLUP);

  setupDebugLed();
  setupGateOutputs();

  printStartupInfo();

  bool configButtonPressed = isBootButtonPressed();

  bool configLoaded = loadDeviceConfig(deviceConfig);
  setupTerminalConfig(&deviceConfig);

  Serial.println("[BOOT] Terminal config window always enabled");
  Serial.println("[BOOT] Type 'terminal' or 'help' within 10 seconds to enter config.");
  Serial.println("[BOOT] If you do nothing, device continues normal startup.");

  runTerminalConfigWindow(deviceConfig, 10000);

  configLoaded = loadDeviceConfig(deviceConfig);
  setupTerminalConfig(&deviceConfig);

  if (configButtonPressed) {
    Serial.println("[BOOT] BOOT button detected after sketch start");
    Serial.println("[BOOT] Entering config portal");
    runConfigPortal(deviceConfig);
  }

  if (!configLoaded || !deviceConfig.isComplete()) {
    Serial.println("[BOOT] Config incomplete, entering config portal");
    runConfigPortal(deviceConfig);
  }

  bool wifiOk = connectToConfiguredWiFi(deviceConfig);

  if (!wifiOk) {
    Serial.println("[BOOT] WiFi failed");

    Serial.println("[BOOT] You have 10 seconds to fix config by terminal.");
    runTerminalConfigWindow(deviceConfig, 10000);

    configLoaded = loadDeviceConfig(deviceConfig);
    setupTerminalConfig(&deviceConfig);

    wifiOk = connectToConfiguredWiFi(deviceConfig);

    if (!wifiOk) {
      Serial.println("[BOOT] WiFi still failed, entering config portal");
      runConfigPortal(deviceConfig);
    }
  }

  printWifiInfo();
  setupApiClient(deviceConfig);

  lastGoodApiAt = millis();

  Serial.println("[BOOT] Ready");
  Serial.println("[CLI] Type 'help' in Serial Monitor for terminal commands.");
  blinkDebugLed(5, 60, 60);
}

void loop() {
  handleTerminalConfig();
  handleWifiHealth();
  printPeriodicStatus();

  unsigned long now = millis();

  if (now - lastPollAt >= POLL_INTERVAL_MS) {
    lastPollAt = now;

    GateCommand command = pollGateCommand();

    // Błędy sieciowe i serwerowe.
    // Nie robimy reconnect dla 401/403/404, bo to zwykle konfiguracja/autoryzacja, a nie WiFi.
    if (command.httpCode < 0 || command.httpCode >= 500) {
      handleApiFailure(command.httpCode);
    } else {
      handleApiSuccess();
    }

    if (command.shouldOpen) {
      Serial.print("[MAIN] OPEN command received, target=");
      Serial.println(command.target);

      Serial.print("[MAIN] command_id=");
      Serial.println(command.commandId);

      Serial.print("[MAIN] relayTimeMs=");
      Serial.println(command.relayTimeMs);

      triggerGate(command.target, command.relayTimeMs);

      if (command.commandId[0] != '\0') {
        bool ackOk = ackGateCommand(command.commandId, "done");

        Serial.print("[MAIN] ACK ok=");
        Serial.println(ackOk ? "true" : "false");

        if (!ackOk) {
          handleApiFailure(-20);
        }
      }
    } else {
      blinkDebugLedOnce();
    }
  }

  // Dłuższe delay pozwala modem sleep realnie odpocząć.
  delay(50);
}