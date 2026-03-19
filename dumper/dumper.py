import json
import os
import time
import serial

BAUD = 921600
TIMEOUT = 2
OUTPUT_DIR = "portenta_qspi_dump"

BYTES_PER_LINE = 16
CHUNK_READ = 4096

# Partition map derived from Arduino's QSPIFormat sketch.
PARTITIONS = [
    {
        "name": "part1_wifi_fat.hex",
        "key": "part1_wifi_fat",
        "offset": 0x200,
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


def prompt_serial_port() -> str:
    print("Enter serial port (examples: COM48, COM7, /dev/ttyACM0, /dev/ttyUSB0)")
    port = input("Serial port: ").strip()
    if not port:
        raise RuntimeError("No serial port provided")
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


def write_partition_metadata(output_dir: str, port: str) -> None:
    json_path = os.path.join(output_dir, "partitions.json")
    txt_path = os.path.join(output_dir, "README.txt")

    metadata = {
        "board": "Arduino Portenta H7",
        "serial_port": port,
        "baud": BAUD,
        "format": "hexdump text",
        "bytes_per_line": BYTES_PER_LINE,
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
        f.write("=" * 40 + "\n\n")
        f.write(f"Serial port: {port}\n")
        f.write(f"Baud: {BAUD}\n")
        f.write(f"Output format: hexdump text\n\n")

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


def dump_region_as_hex(ser: serial.Serial, offset: int, size: int, outfile: str) -> None:
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

    remaining = rx_size
    received = 0
    current_addr = rx_offset
    line_buffer = bytearray()

    with open(outfile, "w", encoding="utf-8") as f:
        while remaining > 0:
            chunk = ser.read(min(CHUNK_READ, remaining))
            if not chunk:
                raise RuntimeError(
                    f"Timeout while receiving data at address 0x{current_addr:08X}"
                )

            for b in chunk:
                line_buffer.append(b)

                if len(line_buffer) == BYTES_PER_LINE:
                    f.write(format_hex_line(current_addr, line_buffer) + "\n")
                    current_addr += BYTES_PER_LINE
                    line_buffer.clear()

            received += len(chunk)
            remaining -= len(chunk)

            percent = (received * 100) // rx_size if rx_size else 100
            print(
                f"\rReceiving {os.path.basename(outfile)}: "
                f"{received}/{rx_size} bytes ({percent}%)",
                end="",
                flush=True,
            )

        if line_buffer:
            f.write(format_hex_line(current_addr, line_buffer) + "\n")

    print()
    tail = read_tail_line(ser, max_wait=5.0)
    if tail != "RAWEND":
        raise RuntimeError(f"Invalid tail: {tail!r}")
    
def dump_mbr(ser):
    print("\n=== Dumping MBR (first 512 bytes) ===")

    ser.reset_input_buffer()
    ser.write(b"r 0 512\n")
    ser.flush()

    header = read_header_line(ser)
    parts = header.split()

    size = int(parts[2])
    if size != 512:
        raise RuntimeError("Unexpected MBR size")

    data = bytearray()

    while len(data) < 512:
        chunk = ser.read(512 - len(data))
        if not chunk:
            raise RuntimeError("Timeout reading MBR")
        data.extend(chunk)

    tail = read_tail_line(ser)
    if tail != "RAWEND":
        raise RuntimeError("Invalid RAWEND after MBR")

    # Save raw
    with open(os.path.join(OUTPUT_DIR, "mbr.bin"), "wb") as f:
        f.write(data)

    # Save hex
    with open(os.path.join(OUTPUT_DIR, "mbr.hex"), "w") as f:
        for i in range(0, 512, 16):
            line = format_hex_line(i, data[i:i+16])
            f.write(line + "\n")

    return data    

def parse_partition_entry(entry):
    status = entry[0]
    part_type = entry[4]
    lba_start = int.from_bytes(entry[8:12], "little")
    sectors = int.from_bytes(entry[12:16], "little")

    offset = lba_start * 512
    size = sectors * 512

    return {
        "bootable": status == 0x80,
        "type": part_type,
        "lba_start": lba_start,
        "sectors": sectors,
        "offset_bytes": offset,
        "size_bytes": size,
    }

def decode_mbr(mbr_data):
    print("\n=== Decoding MBR ===")

    out_path = os.path.join(OUTPUT_DIR, "mbr.txt")

    with open(out_path, "w") as f:
        signature = mbr_data[510] | (mbr_data[511] << 8)

        f.write("MBR Analysis\n")
        f.write("=" * 40 + "\n\n")

        f.write(f"Signature: 0x{signature:04X}\n")
        if signature == 0xAA55:
            f.write("Valid MBR signature\n\n")
        else:
            f.write("INVALID MBR signature\n\n")

        f.write("Partitions:\n\n")

        for i in range(4):
            offset = 446 + i * 16
            entry = mbr_data[offset:offset+16]

            part = parse_partition_entry(entry)

            f.write(f"Partition {i+1}\n")
            f.write(f"  Bootable     : {part['bootable']}\n")
            f.write(f"  Type         : 0x{part['type']:02X}\n")
            f.write(f"  LBA start    : {part['lba_start']}\n")
            f.write(f"  Sectors      : {part['sectors']}\n")
            f.write(f"  Offset (byte): 0x{part['offset_bytes']:08X}\n")
            f.write(f"  Size (byte)  : {part['size_bytes']}\n")
            f.write("\n")

    print(f"MBR decoded → {out_path}")

def main() -> None:
    port = prompt_serial_port()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    write_partition_metadata(OUTPUT_DIR, port)

    print(f"\nOpening serial port {port} at {BAUD} baud...")
    ser = serial.Serial(port, BAUD, timeout=TIMEOUT)  

    try:
        wait_for_device_boot(ser, seconds=4.0)
        
        # Dump and decode MBR first
        mbr_data = dump_mbr(ser)
        decode_mbr(mbr_data)          

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
        for p in PARTITIONS:
            print(f" - {os.path.join(OUTPUT_DIR, p['name'])}")
        print(f" - {os.path.join(OUTPUT_DIR, 'partitions.json')}")
        print(f" - {os.path.join(OUTPUT_DIR, 'README.txt')}")

    finally:
        ser.close()


if __name__ == "__main__":
    main()