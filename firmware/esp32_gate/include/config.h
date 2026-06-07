#pragma once

#include <Arduino.h>

// ===== Piny =====
#define DEBUG_LED_PIN 2          // D2 na płytce, HIGH = świeci
#define BOOT_BUTTON_PIN 0        // przycisk BOOT, aktywny po zwarciu do GND

// Dwa wyjścia do dwóch przycisków pilota
#define GATE1_OUTPUT_PIN 26      // przycisk pilota nr 1 / szlaban 1
#define GATE2_OUTPUT_PIN 27      // przycisk pilota nr 2 / szlaban 2

// ===== Brama / pilot =====
#define GATE_ACTIVE_HIGH 1
#define DEFAULT_GATE_PULSE_MS 700
#define MIN_GATE_PULSE_MS 100
#define MAX_GATE_PULSE_MS 5000

// ===== WiFi =====
#define WIFI_CONNECT_TIMEOUT_MS 20000

// ===== API =====
#define POLL_INTERVAL_MS 2000
#define API_TIMEOUT_MS 5000

// ===== Portal konfiguracyjny =====
#define CONFIG_NAMESPACE "gatecfg"
#define CONFIG_AP_PREFIX "GateConfig-"
#define CONFIG_AP_PASSWORD "12345678"
#define CONFIG_PORTAL_DNS_PORT 53
