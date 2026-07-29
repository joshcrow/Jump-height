// host_paths.h — shared helper for the HOST platform's storage-shaped seams
// (jh_persist, jh_store): resolves the directory their files live in from
// the JH_HOST_DIR environment variable (default /tmp), per the host port's
// contract. Not one of the 5 named platform seams itself — just a small
// helper two of them both need, the same reason jh_clock.h exists alongside
// the 4 seams named in docs/sense.md §3.9 rather than duplicating logic.
//
// Every function is `inline`: this header is included from more than one
// translation unit (jh_persist.cpp AND jh_store.cpp), so anything with a
// body here must be inline (or static) to avoid multiple-definition link
// errors — same rule the rest of this platform's shim follows.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <cstdlib>
#include <string>

#include <sys/stat.h>

namespace jh_host {

// The directory host storage/persistence files live in: $JH_HOST_DIR, or
// /tmp if unset/empty.
inline std::string host_dir() {
  const char* d = std::getenv("JH_HOST_DIR");
  return (d && *d) ? std::string(d) : std::string("/tmp");
}

// host_dir()/filename, with exactly one separating slash either way.
inline std::string path(const char* filename) {
  std::string dir = host_dir();
  if (!dir.empty() && dir.back() != '/') dir += '/';
  dir += filename;
  return dir;
}

// Best-effort single-level create. JH_HOST_DIR's PARENT is expected to
// already exist (a test harness typically points this at its own tempdir,
// which it already created) — this just covers JH_HOST_DIR itself not yet
// existing. Failures (including EEXIST) are silently ignored: the fopen()
// that follows in the caller is the real, honest test of whether storage
// actually works.
inline void ensure_dir() {
  ::mkdir(host_dir().c_str(), 0755);
}

}  // namespace jh_host
