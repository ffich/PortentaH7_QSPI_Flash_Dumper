import json
import os
import time
import serial

BAUD = 921600
TIMEOUT = 2
OUTPUT_DIR = "portenta_qspi_dump"
CONFIG_FILE = "config.json"

BYTES_PER_LINE = 16
CHUNK_READ = 4096
SECTOR_SIZE = 512

# Partition map derived from Arduino's QSPIFormat sketch.
PARTITIONS = [
    {
        "name": "part1_wifi_fat.hex",
        "key": "part1_wifi_fat",
        "offset": 0 * 1024 * 1024,
        "size": 1 * 1024 * 1024,
        "type": "MBR partition",
        "filesystem": "FAT",
        "description": "Partition 1: WiFi firmware and certificates",
    },
    {
        "name": "part2_ota_fat.hex",
        "key": "part2_ota_fat",
        "offset": 1 * 1024 * 1024,
        "size": 5 * 1024 * 1024,
        "type": "MBR partition",
        "filesystem": "FAT",
        "description": "Partition 2: OTA",
    },
    {
        "name": "part3_kvstore.hex",
        "key": "part3_kvstore",
        "offset": 6 * 1024 * 1024,
        "size": 1 * 1024 * 1024,
        "type": "MBR partition",
        "filesystem": "raw / KVStore area",
        "description": "Partition 3: Provisioning KVStore",
    },
    {
        "name": "part4_user.hex",
        "key": "part4_user",
        "offset": 7 * 1024 * 1024,
        "size": 7 * 1024 * 1024,
        "type": "MBR partition",
        "filesystem": "LittleFS or FAT",
        "description": "Partition 4: User data",
    },
    {
        "name": "mapped_wifi_fw_15_5MB.hex",
        "key": "mapped_wifi_fw_15_5MB",
        "offset": 15 * 1024 * 1024 + 512 * 1024,
        "size": 421098,
        "type": "raw memory-mapped region",
        "filesystem": "none",
        "description": "Memory-mapped WiFi firmware copy",
    },
]


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {}

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def prompt_serial_port() -> str:
    config = load_config()
    saved_port = str(config.get("serial_port", "")).strip()

    print("Enter serial port (examples: COM48, COM7, /dev/ttyACM0, /dev/ttyUSB0)")

    if saved_port:
        port = input(f"Serial port [{saved_port}]: ").strip()
        if not port:
            port = saved_port
    else:
        port = input("Serial port: ").strip()

    if not port:
        raise RuntimeError("No serial port provided")

    if port != saved_port:
        config["serial_port"] = port
        save_config(config)

    return port


def format_hex_line(addr: int, data: bytes) -> str:
    hex_part = " ".join(f"{b:02X}" for b in data)
    ascii_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in data)
    return f"{addr:08X}  {hex_part:<48}  |{ascii_part}|"


def wait_for_device_boot(ser: serial.Serial, seconds: float = 4.0) -> None:
    """
    Give the board time to reboot after opening the serial port.
    Also discard any boot log already present in the RX buffer.
    """
    end_time = time.time() + seconds
    while time.time() < end_time:
        if ser.in_waiting:
            ser.read(ser.in_waiting)
        time.sleep(0.05)


def read_header_line(ser: serial.Serial, max_wait: float = 10.0) -> str:
    """
    Read lines until a valid RAWBEGIN header is found.
    Ignores empty lines and unrelated text.
    """
    deadline = time.time() + max_wait

    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue

        line = raw.decode(errors="ignore").strip()
        if not line:
            continue

        print(f"RX: {line}")

        if line.startswith("RAWBEGIN"):
            return line

    raise RuntimeError("Timeout waiting for RAWBEGIN header")


def read_tail_line(ser: serial.Serial, max_wait: float = 5.0) -> str:
    deadline = time.time() + max_wait

    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue

        line = raw.decode(errors="ignore").strip()
        if not line:
            continue

        print(f"RX: {line}")

        if line == "RAWEND":
            return line

    raise RuntimeError("Timeout waiting for RAWEND")


def request_raw_region(ser: serial.Serial, offset: int, size: int) -> bytes:
    """
    Request a raw region through the serial dumper and return the bytes.
    """
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    cmd = f"r {offset} {size}\n".encode("ascii")
    print(f"TX: {cmd.decode().strip()}")
    ser.write(cmd)
    ser.flush()

    header = read_header_line(ser, max_wait=10.0)
    parts = header.split()

    if len(parts) < 3:
        raise RuntimeError(f"Malformed RAWBEGIN header: {header!r}")

    rx_offset = int(parts[1])
    rx_size = int(parts[2])

    if rx_offset != offset or rx_size != size:
        raise RuntimeError(
            f"Unexpected region returned: offset={rx_offset}, size={rx_size}, "
            f"expected offset={offset}, size={size}"
        )

    data = bytearray()
    remaining = rx_size

    while remaining > 0:
        chunk = ser.read(min(CHUNK_READ, remaining))
        if not chunk:
            raise RuntimeError(
                f"Timeout while receiving data at offset 0x{offset + len(data):08X}"
            )

        data.extend(chunk)
        remaining -= len(chunk)

        percent = (len(data) * 100) // rx_size if rx_size else 100
        print(
            f"\rReceiving raw region: {len(data)}/{rx_size} bytes ({percent}%)",
            end="",
            flush=True,
        )

    print()

    tail = read_tail_line(ser, max_wait=5.0)
    if tail != "RAWEND":
        raise RuntimeError(f"Invalid tail: {tail!r}")

    return bytes(data)


def dump_region_as_hex(ser: serial.Serial, offset: int, size: int, outfile: str) -> None:
    data = request_raw_region(ser, offset, size)

    with open(outfile, "w", encoding="utf-8") as f:
        for i in range(0, len(data), BYTES_PER_LINE):
            chunk = data[i:i + BYTES_PER_LINE]
            f.write(format_hex_line(offset + i, chunk) + "\n")


def dump_mbr(ser: serial.Serial, output_dir: str) -> bytes:
    print("\n=== Dumping MBR (first 512 bytes) ===")
    data = request_raw_region(ser, 0, 512)

    mbr_bin = os.path.join(output_dir, "mbr.bin")
    mbr_hex = os.path.join(output_dir, "mbr.hex")

    with open(mbr_bin, "wb") as f:
        f.write(data)

    with open(mbr_hex, "w", encoding="utf-8") as f:
        for i in range(0, len(data), BYTES_PER_LINE):
            chunk = data[i:i + BYTES_PER_LINE]
            f.write(format_hex_line(i, chunk) + "\n")

    return data


def parse_partition_entry(entry: bytes) -> dict:
    if len(entry) != 16:
        raise ValueError("MBR partition entry must be 16 bytes long")

    status = entry[0]
    part_type = entry[4]
    lba_start = int.from_bytes(entry[8:12], "little")
    sectors = int.from_bytes(entry[12:16], "little")

    offset_bytes = lba_start * SECTOR_SIZE
    size_bytes = sectors * SECTOR_SIZE

    return {
        "bootable": status == 0x80,
        "status_raw": status,
        "type": part_type,
        "lba_start": lba_start,
        "sectors": sectors,
        "offset_bytes": offset_bytes,
        "size_bytes": size_bytes,
        "end_exclusive_bytes": offset_bytes + size_bytes,
        "is_empty": entry == b"\x00" * 16,
        "raw_entry_hex": " ".join(f"{b:02X}" for b in entry),
    }


def decode_mbr_type(part_type: int) -> str:
    known_types = {
        0x00: "Empty",
        0x01: "FAT12",
        0x04: "FAT16 <32M",
        0x06: "FAT16",
        0x0B: "FAT32 CHS",
        0x0C: "FAT32 LBA",
        0x0E: "FAT16 LBA",
        0x83: "Linux native",
    }
    return known_types.get(part_type, "Unknown / vendor-specific")


def decode_mbr(mbr_data: bytes, output_dir: str) -> None:
    if len(mbr_data) != 512:
        raise RuntimeError(f"Invalid MBR size: {len(mbr_data)} bytes")

    out_path = os.path.join(output_dir, "mbr.txt")
    signature_le = int.from_bytes(mbr_data[510:512], "little")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("MBR Analysis\n")
        f.write("=" * 60 + "\n\n")

        f.write("General information\n")
        f.write("-" * 60 + "\n")
        f.write(f"MBR size              : {len(mbr_data)} bytes\n")
        f.write(f"Signature (LE)        : 0x{signature_le:04X}\n")
        f.write(
            f"Signature valid       : {'yes' if signature_le == 0xAA55 else 'no'}\n\n"
        )

        f.write("Partition entries\n")
        f.write("-" * 60 + "\n\n")

        for i in range(4):
            entry_offset = 0x1BE + i * 16
            entry = mbr_data[entry_offset:entry_offset + 16]
            part = parse_partition_entry(entry)

            f.write(f"Partition {i + 1}\n")
            f.write(f"  Entry offset        : 0x{entry_offset:03X}\n")
            f.write(f"  Raw entry           : {part['raw_entry_hex']}\n")
            f.write(f"  Empty entry         : {part['is_empty']}\n")
            f.write(f"  Bootable            : {part['bootable']}\n")
            f.write(f"  Status raw          : 0x{part['status_raw']:02X}\n")
            f.write(f"  Type raw            : 0x{part['type']:02X}\n")
            f.write(f"  Type decoded        : {decode_mbr_type(part['type'])}\n")
            f.write(f"  LBA start           : {part['lba_start']}\n")
            f.write(f"  Sector count        : {part['sectors']}\n")
            f.write(f"  Offset bytes        : 0x{part['offset_bytes']:08X} ({part['offset_bytes']})\n")
            f.write(f"  Size bytes          : 0x{part['size_bytes']:08X} ({part['size_bytes']})\n")
            f.write(
                f"  End exclusive       : 0x{part['end_exclusive_bytes']:08X} "
                f"({part['end_exclusive_bytes']})\n"
            )
            f.write("\n")


def write_partition_metadata(output_dir: str, port: str) -> None:
    json_path = os.path.join(output_dir, "partitions.json")
    txt_path = os.path.join(output_dir, "README.txt")

    metadata = {
        "board": "Arduino Portenta H7",
        "serial_port": port,
        "baud": BAUD,
        "format": "hexdump text",
        "bytes_per_line": BYTES_PER_LINE,
        "sector_size": SECTOR_SIZE,
        "source_layout": "ArduinoCore-mbed QSPIFormat",
        "regions": [],
    }

    for p in PARTITIONS:
        metadata["regions"].append({
            "key": p["key"],
            "file": p["name"],
            "offset_dec": p["offset"],
            "offset_hex": f"0x{p['offset']:08X}",
            "size_dec": p["size"],
            "size_hex": f"0x{p['size']:X}",
            "end_exclusive_dec": p["offset"] + p["size"],
            "end_exclusive_hex": f"0x{p['offset'] + p['size']:08X}",
            "type": p["type"],
            "filesystem": p["filesystem"],
            "description": p["description"],
        })

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("Portenta H7 QSPI dump layout\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Serial port: {port}\n")
        f.write(f"Baud: {BAUD}\n")
        f.write("Output format: hexdump text\n\n")

        f.write("Files generated by this script:\n")
        f.write("  - mbr.bin\n")
        f.write("  - mbr.hex\n")
        f.write("  - mbr.txt\n")
        for p in PARTITIONS:
            f.write(f"  - {p['name']}\n")
        f.write("  - partitions.json\n")
        f.write("  - README.txt\n\n")

        f.write("Region layout:\n")
        f.write("-" * 60 + "\n\n")

        for p in PARTITIONS:
            start = p["offset"]
            end = p["offset"] + p["size"]
            f.write(f"{p['key']}\n")
            f.write(f"  file        : {p['name']}\n")
            f.write(f"  description : {p['description']}\n")
            f.write(f"  type        : {p['type']}\n")
            f.write(f"  filesystem  : {p['filesystem']}\n")
            f.write(f"  offset      : 0x{start:08X} ({start})\n")
            f.write(f"  size        : 0x{p['size']:X} ({p['size']})\n")
            f.write(f"  end excl.   : 0x{end:08X} ({end})\n\n")


def main() -> None:
    port = prompt_serial_port()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    write_partition_metadata(OUTPUT_DIR, port)

    print(f"\nOpening serial port {port} at {BAUD} baud...")
    ser = serial.Serial(port, BAUD, timeout=TIMEOUT)

    try:
        wait_for_device_boot(ser, seconds=4.0)

        # Dump and decode MBR first.
        mbr_data = dump_mbr(ser, OUTPUT_DIR)
        decode_mbr(mbr_data, OUTPUT_DIR)

        # Dump all defined regions.
        for p in PARTITIONS:
            outfile = os.path.join(OUTPUT_DIR, p["name"])

            print()
            print("=" * 72)
            print(p["description"])
            print(f"Offset: 0x{p['offset']:08X}")
            print(f"Size:   {p['size']} bytes")
            print(f"File:   {outfile}")

            dump_region_as_hex(ser, p["offset"], p["size"], outfile)

        print()
        print("Done. Files created:")
        print(f" - {os.path.join(OUTPUT_DIR, 'mbr.bin')}")
        print(f" - {os.path.join(OUTPUT_DIR, 'mbr.hex')}")
        print(f" - {os.path.join(OUTPUT_DIR, 'mbr.txt')}")
        for p in PARTITIONS:
            print(f" - {os.path.join(OUTPUT_DIR, p['name'])}")
        print(f" - {os.path.join(OUTPUT_DIR, 'partitions.json')}")
        print(f" - {os.path.join(OUTPUT_DIR, 'README.txt')}")
        print(f" - {CONFIG_FILE}")

    finally:
        ser.close()


if __name__ == "__main__":
    main()