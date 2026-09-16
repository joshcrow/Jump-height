"""Hash recorder source and the reused read-only bounded I2C driver."""
from pathlib import Path
import hashlib

Import("env")  # noqa: F821 - PlatformIO/SCons
root = Path(env["PROJECT_DIR"])
digest = hashlib.sha256()
inputs = sorted((root/"src").glob("*")) + sorted((root/"include").glob("*"))
inputs += [root/"platformio.ini", root/"build_identity.py",
           root/"../../firmware/src/platform/nrf52/twim_bounded.h"]
for path in inputs:
    digest.update(path.name.encode())
    digest.update(path.read_bytes())
env.Append(CPPDEFINES=[("JH6_BUILD", env.StringifyMacro(digest.hexdigest()[:16]))])
