from datetime import datetime

def now():
    dt = datetime.now()
    return datetime.timestamp(dt)


def pad(data: bytes, length: int) -> bytes:
    padding_length = length - len(data)
    return data + b'\x20' * padding_length
