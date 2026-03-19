# Portenta H7 QSPI Flash Dumper
A simple skecth + Python script for making dumps of the Portenta H7 QSPIO Flash.

## Overview
This project provides a complete toolchain to **dump and analyze the external QSPI flash memory** of the **Arduino Portenta H7**.

It consists of:

* An **Arduino sketch** running on the Portenta H7 that exposes a simple serial protocol
* A **Python script** that retrieves memory regions and saves them as readable hexdumps
* Automatic extraction of **QSPI partitions** based on the official Arduino memory layout

The goal is to enable:

* QSPI Flash inspection
* Data recovery
* Debugging of OTA / filesystem issues

---

## Features

* Dump **entire QSPI flash** or selected regions
* Partition-aware dumping based on Arduino's `QSPIFormat` layout
* Output in **human-readable hex format**
* One file per partition
* Automatic generation of:
  * `partitions.json` (machine-readable layout)
  * `README.txt` (human-readable summary)
* Robust serial communication handling (boot delays, noise filtering)

---

## QSPI Memory Layout

The partition scheme follows the official Arduino example:

```
0 MB   →  1 MB   : Partition 1 (WiFi firmware + certificates, FAT)
1 MB   →  6 MB   : Partition 2 (OTA storage, FAT)
6 MB   →  7 MB   : Partition 3 (KVStore / provisioning)
7 MB   → 14 MB   : Partition 4 (User data, LittleFS or FAT)
15.5 MB → ~16 MB : Memory-mapped WiFi firmware
```
---

## Requirements

### Hardware

* Arduino Portenta H7

### Software

* Python 3.8+
* `pyserial`

Install dependency:

```bash
python -m pip install pyserial
```

---

## Usage

### 1. Flash the Arduino Sketch

Upload the QSPI dumper sketch to the Portenta H7.

The sketch must support the following serial command:

```
r <offset> <length>
```

and respond with:

```
RAWBEGIN <offset> <length>
<binary data>
RAWEND
```

---

### 2. Run the Python Dumper

```bash
python dump_qspi.py
```

You will be prompted to enter the serial port:

```
Serial port: COMXX (enter your serial port number)
```

---

### 3. Output

The script creates a folder:

```
portenta_qspi_dump/
```

Containing:

```
part1_wifi_fat.hex
part2_ota_fat.hex
part3_kvstore.hex
part4_user.hex
mapped_wifi_fw_15_5MB.hex

partitions.json
README.txt
```
Example:

<img width="600" alt="image" src="https://github.com/user-attachments/assets/1a05f5c3-1b31-4117-9387-b5ba94e39287" />


---

## Output Format

Each dump file is a **hexdump**, formatted as:

```
00000000  FF FF FF FF FF FF FF FF 00 20 00 08 ...  |..... ...|
```

This makes it easy to:

* Inspect memory visually
* Compare dumps
* Search for patterns

---

## Limitations

* Output is **not Intel HEX format**, but a readable hexdump
* Filesystems (FAT / LittleFS) are **not parsed automatically**
* Dump speed depends on serial baud rate (large dumps may take time)

---

## Future Improvements

* Automatic filesystem detection (FAT / LittleFS)
* File extraction from FAT partitions
* Full QSPI dump including unused regions
* CRC / integrity checks
* Binary + HEX dual output
* Integration with reverse engineering tools (e.g., Ghidra)

---

## Disclaimer

This tool is intended for debugging, development, and educational purposes.
Use responsibly when working with firmware and proprietary data.

