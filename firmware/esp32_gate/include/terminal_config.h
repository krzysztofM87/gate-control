#pragma once

#include <Arduino.h>
#include "wifi_manager.h"

void setupTerminalConfig(DeviceConfig *config);
void handleTerminalConfig();

// Okno startowe: pozwala wejść w konfigurację terminalową przed portalem AP.
bool runTerminalConfigWindow(DeviceConfig &config, uint32_t timeoutMs);
