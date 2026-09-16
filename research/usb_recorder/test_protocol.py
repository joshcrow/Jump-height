"""Compile device's actual codec helpers and compare with independent host values."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zlib


class DeviceProtocolTests(unittest.TestCase):
    def test_crc_fifo_timestamp_endianness_and_rails(self):
        compiler = shutil.which("c++")
        self.assertIsNotNone(compiler, "C++ compiler required to test actual device helpers")
        source = r'''
#include <cstdio>
#include "protocol.h"
int main() {
  const uint8_t standard[] = {'1','2','3','4','5','6','7','8','9'};
  uint8_t raw[18] = {};
  raw[12] = 0xAB; raw[13] = 0xCD; raw[15] = 0xEF;
  jh6::u16(raw, uint16_t(-32768));
  jh6::u16(raw+8, 32767);
  uint8_t encoded[4]; jh6::u32(encoded, 0xDEADBEEF);
  printf("%u %u %d %u %02x%02x%02x%02x\n",
    jh6::crc32(standard,9), jh6::fifo_timestamp(raw),
    int(jh6::signed_word(raw)), unsigned(jh6::rail_flags(raw)),
    encoded[0],encoded[1],encoded[2],encoded[3]);
}
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path/"test.cpp").write_text(source)
            subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(Path(__file__).parent/"include"),
                            str(path/"test.cpp"), "-o", str(path/"test")], check=True)
            values = subprocess.check_output([str(path/"test")], text=True).split()
        self.assertEqual(int(values[0]), zlib.crc32(b"123456789"))
        self.assertEqual(int(values[1]), 0xCDABEF)
        self.assertEqual(int(values[2]), -32768)
        self.assertEqual(int(values[3]), (1 << 3) | (1 << 1))
        self.assertEqual(values[4], "efbeadde")


if __name__ == "__main__":
    unittest.main()
