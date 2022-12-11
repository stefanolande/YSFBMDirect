import socket
from datetime import datetime


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
