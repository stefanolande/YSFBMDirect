import logging
from hashlib import sha256

from utils import pad


def send_login_message(call: str, bm_sock):
    message = "YSFL".encode() + pad(call.encode(), 10)
    logging.debug("sending: %s" % message)
    bm_sock.send(message)


def send_challenge_message(call: str, salt: bytes, password: str, bm_sock):
    secret = salt + password.encode()
    secret_hash = sha256(secret).digest()
    message = "YSFK".encode() + pad(call.encode(), 10) + secret_hash
    logging.debug("sending: %s" % message)
    bm_sock.send(message)


def send_tg_message(call: str, tg: int, bm_sock):
    message = "YSFO".encode() + pad(call.encode(), 10) + f"group={tg}".encode()
    logging.debug("sending: %s" % message)
    bm_sock.send(message)


def login_and_set_tg(callsign, bm_password, tg, bm_sock, is_salt_received, maybe_salt: list):
    send_login_message(callsign, bm_sock)
    is_salt_received.wait()
    is_salt_received.clear()
    salt = maybe_salt[0]
    maybe_salt.clear()
    send_challenge_message(callsign, salt, bm_password, bm_sock)
    send_tg_message(callsign, tg, bm_sock)
