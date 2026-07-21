#pragma once

#include <Arduino.h>

// ===== Pins =====
#define DEBUG_LED_PIN 2
#define BOOT_BUTTON_PIN 0

#define GATE1_OUTPUT_PIN 26
#define GATE2_OUTPUT_PIN 27

// ===== Gate relay =====
#define GATE_ACTIVE_HIGH 1
#define DEFAULT_GATE_PULSE_MS 700
#define MIN_GATE_PULSE_MS 100
#define MAX_GATE_PULSE_MS 5000

// ===== WiFi =====
#define WIFI_CONNECT_TIMEOUT_MS 20000
#define WIFI_RECONNECT_MIN_INTERVAL_MS 15000
#define WIFI_RSSI_WARN_DBM -75

// ===== API =====
#define POLL_INTERVAL_MS 5000
#define API_TIMEOUT_MS 10000
#define HTTP_RETRY_COUNT 2
#define HTTP_RETRY_DELAY_MS 400

#define API_FAILS_BEFORE_WIFI_RECONNECT 5
#define API_FAILS_BEFORE_REBOOT 60
#define STATUS_PRINT_INTERVAL_MS 60000

// ===== Power save =====
// For gate controller stability, WiFi sleep is disabled.
// CPU can stay at 80 MHz.
#define WIFI_POWER_SAVE_ENABLED 0
#define CPU_POWER_SAVE_ENABLED 1
#define CPU_FREQUENCY_MHZ 80

// ===== Config portal =====
#define CONFIG_NAMESPACE "gatecfg"
#define CONFIG_AP_PREFIX "GateConfig-"
#define CONFIG_AP_PASSWORD "12345678"
#define CONFIG_PORTAL_DNS_PORT 53

// ===== Logging =====
#define LOG_LEVEL_NONE 0
#define LOG_LEVEL_ERROR 1
#define LOG_LEVEL_WARN 2
#define LOG_LEVEL_INFO 3
#define LOG_LEVEL_DEBUG 4

#define LOG_LEVEL LOG_LEVEL_INFO