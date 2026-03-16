import serial

BUFFER_SIZE = 14   # 1 head + 10 data + 2 checksum + 1 tail
DATA_SIZE = 10
DATA_VERSION_SIZE = 2
DATA_TAG_SIZE = 8
CHECKSUM_SIZE = 2

# відкриваємо порт (заміни 'COM3' на свій порт, або '/dev/ttyUSB0' у Linux)
ser = serial.Serial(port='COM9', baudrate=9600, timeout=0.1)

buffer = bytearray()
print("INIT DONE")

def hexstr_to_value(data: bytes) -> int:
    """Конвертує ASCII-рядок з hex у число"""
    return int(data.decode('ascii'), 16)

def extract_tag(buf: bytearray) -> int:
    msg_head = buf[0]
    msg_data = buf[1:11]
    msg_data_version = msg_data[:DATA_VERSION_SIZE]
    msg_data_tag = msg_data[DATA_VERSION_SIZE:DATA_VERSION_SIZE+DATA_TAG_SIZE]
    msg_checksum = buf[11:13]
    msg_tail = buf[13]

    print("--------")
    print("Message-Head:", msg_head)
    print("Message-Data (HEX):")
    print(msg_data_version.decode('ascii'), "(version)")
    print(msg_data_tag.decode('ascii'), "(tag)")
    print("Message-Checksum (HEX):", msg_checksum.decode('ascii'))
    print("Message-Tail:", msg_tail)

    tag = hexstr_to_value(msg_data_tag)
    print("Extracted Tag:", tag)

    checksum = 0
    for i in range(0, DATA_SIZE, CHECKSUM_SIZE):
        val = hexstr_to_value(msg_data[i:i+CHECKSUM_SIZE])
        checksum ^= val

    print("Extracted Checksum (HEX):", hex(checksum), end="")
    if checksum == hexstr_to_value(msg_checksum):
        print(" (OK)")
    else:
        print(" (NOT OK)")
    print("--------")
    return tag

while True:
    if ser.in_waiting > 0:
        ssvalue = ser.read(1)
        if not ssvalue:
            continue

        val = ssvalue[0]

        if val == 2:  # head
            buffer = bytearray()
            buffer.append(val)
        elif val == 3:  # tail
            buffer.append(val)
            if len(buffer) == BUFFER_SIZE:
                extract_tag(buffer)
            buffer = bytearray()
        else:
            buffer.append(val)
            if len(buffer) > BUFFER_SIZE:
                print("Error: Buffer overflow detected!")
                buffer = bytearray()