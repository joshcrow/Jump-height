// GENERATED FILE — do not edit.
// Source of truth: config/params.json  (regenerate: ./tools/jump gen)
#pragma once

// --- detector ---
#define JH_G 9.80665f
#define JH_FREEFALL_ENTER_G 0.35f
#define JH_FREEFALL_CONFIRM_S 0.08f
#define JH_LANDING_THRESHOLD_G 2.5f
#define JH_MIN_AIRTIME_S 0.25f
#define JH_MAX_AIRTIME_S 8.0f
#define JH_AIRTIME_OFFSET_S 0.0f
#define JH_HEIGHT_SCALE 1.0f

// --- firmware ---
#define JH_SAMPLE_HZ 200
#define JH_LOG_HZ 50
#define JH_MOTION_THRESH_G 0.12f
#define JH_IDLE_TIMEOUT_S 20
#define JH_TRACE_MAX_BYTES 2000000
#define JH_I2C_SDA 21
#define JH_I2C_SCL 22

#define JH_PARAMS_SUMMARY "airtime_offset_s=0 freefall_confirm_s=0.08 freefall_enter_g=0.35 g=9.80665 height_scale=1 landing_threshold_g=2.5 max_airtime_s=8 min_airtime_s=0.25"
