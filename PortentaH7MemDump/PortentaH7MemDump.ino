#include <Arduino.h>
#include "QSPIFBlockDevice.h"

using namespace mbed;

// On Portenta H7 the default QSPIFBlockDevice constructor maps to the onboard QSPI flash.
QSPIFBlockDevice qspi;

static const uint32_t SERIAL_BAUD = 921600;
static const size_t CHUNK_SIZE = 4096;

uint8_t buffer[CHUNK_SIZE];

bool readLine(String &line) {
  static String acc;

  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c == '\r') {
      continue;
    }

    if (c == '\n') {
      line = acc;
      acc = "";
      return true;
    }

    acc += c;
  }

  return false;
}

bool parseTwoNumbers(const String &s, uint32_t &a, uint32_t &b) {
  int firstSpace = s.indexOf(' ');
  if (firstSpace < 0) {
    return false;
  }

  int secondSpace = s.indexOf(' ', firstSpace + 1);
  if (secondSpace < 0) {
    return false;
  }

  String sa = s.substring(firstSpace + 1, secondSpace);
  String sb = s.substring(secondSpace + 1);

  a = (uint32_t)strtoul(sa.c_str(), nullptr, 0);
  b = (uint32_t)strtoul(sb.c_str(), nullptr, 0);
  return true;
}

void printInfo() {
  bd_size_t flashSize = qspi.size();
  bd_size_t readSize = qspi.get_read_size();
  bd_size_t progSize = qspi.get_program_size();
  bd_size_t eraseSize = qspi.get_erase_size();

  Serial.println();
  Serial.println("=== QSPI INFO ===");
  Serial.print("Size: ");
  Serial.print((uint32_t)flashSize);
  Serial.println(" bytes");

  Serial.print("Read size: ");
  Serial.print((uint32_t)readSize);
  Serial.println(" bytes");

  Serial.print("Program size: ");
  Serial.print((uint32_t)progSize);
  Serial.println(" bytes");

  Serial.print("Erase size: ");
  Serial.print((uint32_t)eraseSize);
  Serial.println(" bytes");
  Serial.println("=================");
  Serial.println();
}

void printHexLine(uint32_t base, const uint8_t *data, size_t len) {
  char ascii[17];
  ascii[16] = '\0';

  Serial.print("0x");
  if (base < 0x10000000UL) Serial.print("0");
  Serial.print(base, HEX);
  Serial.print("  ");

  for (size_t i = 0; i < 16; i++) {
    if (i < len) {
      if (data[i] < 16) Serial.print("0");
      Serial.print(data[i], HEX);
      Serial.print(" ");
      ascii[i] = (data[i] >= 32 && data[i] <= 126) ? (char)data[i] : '.';
    } else {
      Serial.print("   ");
      ascii[i] = ' ';
    }
  }

  Serial.print(" |");
  Serial.print(ascii);
  Serial.println("|");
}

void hexDumpRegion(uint32_t offset, uint32_t length) {
  bd_size_t flashSize = qspi.size();

  if ((bd_addr_t)offset >= flashSize) {
    Serial.println("ERR: offset out of range");
    return;
  }

  if ((uint64_t)offset + (uint64_t)length > (uint64_t)flashSize) {
    Serial.println("ERR: region out of range");
    return;
  }

  Serial.println();
  Serial.println("HEX DUMP BEGIN");

  uint32_t remaining = length;
  uint32_t current = offset;

  while (remaining > 0) {
    size_t toRead = remaining > 16 ? 16 : remaining;

    int rc = qspi.read(buffer, current, toRead);
    if (rc != 0) {
      Serial.print("ERR: read failed at 0x");
      Serial.println(current, HEX);
      return;
    }

    printHexLine(current, buffer, toRead);

    current += toRead;
    remaining -= toRead;
  }

  Serial.println("HEX DUMP END");
  Serial.println();
}

void rawDumpRegion(uint32_t offset, uint32_t length) {
  bd_size_t flashSize = qspi.size();

  if ((bd_addr_t)offset >= flashSize) {
    Serial.println("ERR: offset out of range");
    return;
  }

  if ((uint64_t)offset + (uint64_t)length > (uint64_t)flashSize) {
    Serial.println("ERR: region out of range");
    return;
  }

  // Small textual preamble.
  Serial.print("RAWBEGIN ");
  Serial.print(offset);
  Serial.print(" ");
  Serial.println(length);
  Serial.flush();
  delay(20);

  uint32_t remaining = length;
  uint32_t current = offset;

  while (remaining > 0) {
    size_t toRead = remaining > CHUNK_SIZE ? CHUNK_SIZE : remaining;

    int rc = qspi.read(buffer, current, toRead);
    if (rc != 0) {
      // Avoid mixing text into the binary stream as much as possible.
      break;
    }

    Serial.write(buffer, toRead);
    current += toRead;
    remaining -= toRead;
  }

  Serial.flush();
  delay(20);
  Serial.println();
  Serial.println("RAWEND");
}

void rawDumpAll() {
  rawDumpRegion(0, (uint32_t)qspi.size());
}

void printHelp() {
  Serial.println("Commands:");
  Serial.println("  i                  -> print QSPI info");
  Serial.println("  h <off> <len>      -> hex dump region");
  Serial.println("  r <off> <len>      -> raw binary dump region");
  Serial.println("  d                  -> raw binary dump full QSPI");
  Serial.println("  ?                  -> help");
  Serial.println();
  Serial.println("Numbers can be decimal or hex (example: 0x1000).");
  Serial.println();
}

void setup() {
  Serial.begin(SERIAL_BAUD);

  unsigned long t0 = millis();
  while (!Serial && (millis() - t0 < 5000)) {
    delay(10);
  }

  Serial.println();
  Serial.println("Portenta H7 QSPI dumper");

  int rc = qspi.init();
  if (rc != 0) {
    Serial.print("QSPI init failed, rc=");
    Serial.println(rc);
    while (true) {
      delay(1000);
    }
  }

  printInfo();
  printHelp();
}

void loop() {
  String line;

  if (!readLine(line)) {
    return;
  }

  line.trim();
  if (line.length() == 0) {
    return;
  }

  if (line == "i") {
    printInfo();
    return;
  }

  if (line == "d") {
    rawDumpAll();
    return;
  }

  if (line == "?") {
    printHelp();
    return;
  }

  if (line.startsWith("h ")) {
    uint32_t off = 0;
    uint32_t len = 0;

    if (!parseTwoNumbers(line, off, len)) {
      Serial.println("ERR: use h <offset> <len>");
      return;
    }

    hexDumpRegion(off, len);
    return;
  }

  if (line.startsWith("r ")) {
    uint32_t off = 0;
    uint32_t len = 0;

    if (!parseTwoNumbers(line, off, len)) {
      Serial.println("ERR: use r <offset> <len>");
      return;
    }

    rawDumpRegion(off, len);
    return;
  }

  Serial.println("ERR: unknown command");
  printHelp();
}