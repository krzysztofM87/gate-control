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

void setup() {
  Serial.begin(115200);
  delay(400);

  pinMode(BOOT_BUTTON_PIN, INPUT_PULLUP);

  setupDebugLed();
  setupGateOutputs();

  printStartupInfo();

  bool configButtonPressed = isBootButtonPressed();

  if (configButtonPressed) {
    Serial.println("[BOOT] BOOT pressed");
  }

  bool configLoaded = loadDeviceConfig(deviceConfig);
  setupTerminalConfig(&deviceConfig);

  if (configButtonPressed || !configLoaded || !deviceConfig.isComplete()) {
    Serial.println("[BOOT] Missing config or BOOT pressed");

    runTerminalConfigWindow(deviceConfig, 10000);

    configLoaded = loadDeviceConfig(deviceConfig);
    setupTerminalConfig(&deviceConfig);

    if (!configLoaded || !deviceConfig.isComplete()) {
      Serial.println("[BOOT] Config still incomplete, entering config portal");
      runConfigPortal(deviceConfig);
    }
  }

  bool wifiOk = connectToConfiguredWiFi(deviceConfig);

  if (!wifiOk) {
    Serial.println("[BOOT] WiFi failed");

    runTerminalConfigWindow(deviceConfig, 10000);

    configLoaded = loadDeviceConfig(deviceConfig);
    setupTerminalConfig(&deviceConfig);

    wifiOk = connectToConfiguredWiFi(deviceConfig);

    if (!wifiOk) {
      Serial.println("[BOOT] WiFi still failed, entering config portal");
      runConfigPortal(deviceConfig);
    }
  }

  setupApiClient(deviceConfig);

  Serial.println("[BOOT] Ready");
  Serial.println("[CLI] Type 'help' in Serial Monitor for terminal commands.");
  blinkDebugLed(5, 60, 60);
}

void loop() {
  handleTerminalConfig();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WIFI] Lost connection, reconnecting");
    connectToConfiguredWiFi(deviceConfig);
  }

  unsigned long now = millis();

  if (now - lastPollAt >= POLL_INTERVAL_MS) {
    lastPollAt = now;

    GateCommand command = pollGateCommand();

    if (command.shouldOpen) {
      Serial.print("[MAIN] OPEN command received, target=");
      Serial.println(command.target);

      triggerGate(command.target, command.relayTimeMs);

      if (command.commandId.length() > 0) {
        ackGateCommand(command.commandId, "done");
      }
    } else {
      blinkDebugLedOnce();
    }
  }

  delay(20);
}
