// GENERATED FILE — do not edit.
// Source of truth: config/params.json  (regenerate: ./tools/jump gen)
#pragma once

// --- detector ---
#define JH_G 9.80665f
#define JH_FREEFALL_ENTER_G 0.35f
#define JH_FREEFALL_CONFIRM_S 0.08f
#define JH_LANDING_THRESHOLD_G 2.5f
#define JH_LANDING_SETTLE_S 0.5f
#define JH_MIN_AIRTIME_S 0.25f
#define JH_MAX_AIRTIME_S 3.0f
#define JH_AIRTIME_OFFSET_S 0.0257f
#define JH_HEIGHT_SCALE 1.0f
#define JH_SPIN_LEVER_M 0.0f

// --- firmware ---
#define JH_SAMPLE_HZ 200
#define JH_LOG_HZ 50
#define JH_MOTION_THRESH_G 0.12f
#define JH_IDLE_TIMEOUT_S 20
#define JH_TRACE_MAX_BYTES 2000000
#define JH_I2C_SDA 21
#define JH_I2C_SCL 22

// --- shared ---
#define JH_M_TO_FT 3.28084f
#define JH_BLE_SERVICE_UUID "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define JH_BLE_RX_UUID "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
#define JH_BLE_TX_UUID "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

#define JH_PARAMS_SUMMARY "airtime_offset_s=0.0257 freefall_confirm_s=0.08 freefall_enter_g=0.35 g=9.80665 height_scale=1 landing_settle_s=0.5 landing_threshold_g=2.5 max_airtime_s=3 min_airtime_s=0.25 spin_lever_m=0"
