import logging
import sys
import time
from hashlib import sha256

from utils import pad, now


def send_login_message(call: str, bm_sock):
    message = "YSFL".encode() + pad(call.encode(), 10)
    logging.debug("sending: %s" % message)
    bm_sock.send(message)


def receive_salt(bm_sock) -> bytes:
    data = bm_sock.recv(20)
    logging.debug("received message: %s" % data)
    salt = data[16:]
    logging.debug("salt: %s" % salt)
    return salt


def send_challenge_message(call: str, salt: bytes, password: str, bm_sock):
    secret = salt + password.encode()
    secret_hash = sha256(secret).digest()
    message = "YSFK".encode() + pad(call.encode(), 10) + secret_hash
    logging.debug("sending: %s" % message)
    bm_sock.send(message)


def send_group_message(call: str, tg: int, bm_sock):
    message = "YSFO".encode() + pad(call.encode(), 10) + f"group={tg}".encode()
    logging.debug("sending: %s" % message)
    bm_sock.send(message)


def login_and_set_tg(callsign, bm_password, tg, bm_sock):
    send_login_message(callsign, bm_sock)
    salt = receive_salt(bm_sock)
    send_challenge_message(callsign, salt, bm_password, bm_sock)
    send_group_message(callsign, tg, bm_sock)

    data = bm_sock.recv(1024)  # buffer size is 1024 bytes

    if "YSFNAK" in str(data):
        logging.error("Brandmeister returned an error - check password")
        sys.exit(0)
