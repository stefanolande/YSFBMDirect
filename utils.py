import socket
from datetime import datetime

from ysf import ysfpayload


def now():
    dt = datetime.now()
    return datetime.timestamp(dt)


def pad(data: bytes, length: int) -> bytes:
    padding_length = length - len(data)
    return data + b'\x20' * padding_length


def validate_dg_id_map(dgid_to_tg_map: dict) -> bool:
    # checks that a TG appears only one
    return len(set(dgid_to_tg_map.values())) == len(dgid_to_tg_map)


def close_socket(sock):
    try:
        sock.shutdown(socket.SHUT_RD)
    except OSError as e:
        pass
    sock.close()


def consume_tail(sock):
    sock.settimeout(0.11)
    for i in range(5):
        try:
            sock.recvfrom(1024)
        except TimeoutError:
            pass
        except socket.timeout:
            pass
    sock.setblocking(True)


def send_tg_change_tx(callsign, tg: int, sock, client_addr):
    # Prepare TG string (e.g., "TG222     ")
    tg_str = f"TG{tg}".ljust(10)

    # Prepare network header (40 bytes fixed)
    # Header must contain proper data for the server
    header = "YSFD".encode() + pad(callsign.encode(), 10) + \
             pad(callsign.encode(), 10) + pad("ALL".encode(), 10)

    # Generate C4FM payload (120 bytes)
    # Pass callsign as destination and TG as source_override
    payload_c4fm = generate_c4fm_packet(tg_str)

    # Convert list of integers to bytearray for transmission
    payload_bytes = bytearray(payload_c4fm)

    # Send sequence of 16 frames
    for i in range(16):
        # Combine header (40) + payload (120) = 160 bytes total
        sock.sendto(header + payload_bytes, client_addr)


def generate_c4fm_packet(source_override):
    # Normalization (10 characters fixed)
    call_src = source_override.ljust(10)[:10]
    call_dst = "ALL".ljust(10)[:10]

    # Prepare CSD1 and CSD2
    # CSD1: Destination + Source (10+10 = 20 bytes)
    # CSD2: Source + Source (for redundancy)
    csd1 = (call_dst + call_src).encode('ascii')
    csd2 = (call_src + call_src).encode('ascii')

    payload = [0] * 120

    # Call Golay + CRC function
    ysfpayload.writeHeader(payload, csd1, csd2)

    return payload
