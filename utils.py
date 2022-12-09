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
