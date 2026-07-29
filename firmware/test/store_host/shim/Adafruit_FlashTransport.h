// shim/Adafruit_FlashTransport.h — stand-in for the real Adafruit_SPIFlash
// library header of the same name, picked up by jh_store.cpp's
// `#include <Adafruit_FlashTransport.h>` when this directory is put on the
// host g++ include path AHEAD of anywhere the real library might live (it
// isn't on the host build at all — see firmware/test/store_host/mock_flash.h
// for why this exists and exactly what it does and doesn't model).
//
// jh_store.cpp includes this header and Adafruit_SPIFlashBase.h in that
// order; both shims forward to the same mock_flash.h, guarded by that
// header's own #pragma once, so the include order here doesn't matter.
//
// SPDX-License-Identifier: MIT

#pragma once

#include "../mock_flash.h"
