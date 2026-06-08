#include <Arduino.h>
#include <WiFi.h>

#include "include/config.h"
#include "include/debug_led.h"
#include "include/gate_control.h"
#include "include/wifi_manager.h"
#include "include/config_portal.h"
#include "include/api_client.h"
#include "include/terminal_config.h"

DeviceConfig deviceConfig;

unsigned long lastPollAt = 0;
uint8_t apiFailCount = 0;

static bool isBootButtonPressed() {
  return digitalRead(BOOT_BUTTON_PIN) == LOW;
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

  IPAddress resolvedIp;
  bool dnsOk = WiFi.hostByName("tools.malmaz.com", resolvedIp);

  Serial.print("[DNS] tools.malmaz.com: ");
  if (dnsOk) {
    Serial.println(resolvedIp);
  } else {
    Serial.println("FAILED");
  }
}

static void reconnectWifiAndApi() {
  Serial.println("[MAIN] Reconnecting WiFi and API client");

  WiFi.disconnect();
  delay(1200);

  bool wifiOk = connectToConfiguredWiFi(deviceConfig);

  if (wifiOk) {
    printWifiInfo();
    setupApiClient(deviceConfig);
    apiFailCount = 0;
    Serial.println("[MAIN] WiFi/API reconnect OK");
  } else {
    Serial.println("[MAIN] WiFi reconnect failed");
  }
}

void setup() {
  Serial.begin(115200);
  delay(800);

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

  Serial.println("[BOOT] Ready");
  Serial.println("[CLI] Type 'help' in Serial Monitor for terminal commands.");
  blinkDebugLed(5, 60, 60);
}

void loop() {
  handleTerminalConfig();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WIFI] Lost connection, reconnecting");
    reconnectWifiAndApi();
  }

  unsigned long now = millis();

  if (now - lastPollAt >= POLL_INTERVAL_MS) {
    lastPollAt = now;

    GateCommand command = pollGateCommand();

    if (command.httpCode < 0 || command.httpCode >= 500) {
      apiFailCount++;

      Serial.print("[MAIN] API fail count=");
      Serial.println(apiFailCount);

      if (apiFailCount >= 5) {
        Serial.println("[MAIN] Too many API failures");
        reconnectWifiAndApi();
      }
    } else {
      if (apiFailCount > 0) {
        Serial.println("[MAIN] API recovered");
      }

      apiFailCount = 0;
    }

    if (command.shouldOpen) {
      Serial.print("[MAIN] OPEN command received, target=");
      Serial.println(command.target);

      Serial.print("[MAIN] command_id=");
      Serial.println(command.commandId);

      Serial.print("[MAIN] relayTimeMs=");
      Serial.println(command.relayTimeMs);

      Serial.println("[MAIN] BEFORE triggerGate");
      delay(100);

      triggerGate(command.target, command.relayTimeMs);

      Serial.println("[MAIN] AFTER triggerGate");
      delay(100);

      if (command.commandId[0] != '\0') {
        Serial.println("[MAIN] BEFORE ackGateCommand");

        bool ackOk = ackGateCommand(command.commandId, "done");

        Serial.print("[MAIN] AFTER ackGateCommand, ok=");
        Serial.println(ackOk ? "true" : "false");
      }
    } else {
      blinkDebugLedOnce();
    }
  }

  delay(20);
}
