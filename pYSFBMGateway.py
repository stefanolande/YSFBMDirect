import socket
import sys
import signal
from hashlib import sha256
import logging
import threading
import time
from datetime import datetime

import configparser as configparser

from ysf import ysffich


def set_client_addr(addr):
    global client_addr
    client_addr = addr


def now():
    dt = datetime.now()
    return datetime.timestamp(dt)


def set_last_client_packet_timestamp():
    global last_client_packet_timestamp
    last_client_packet_timestamp = now()


def set_dg_id(new_dg_id):
    global cur_dg_id
    cur_dg_id = new_dg_id


def pad(data: bytes, length: int) -> bytes:
    padding_length = length - len(data)
    return data + b'\x20' * padding_length


def send_login_message(call: str):
    message = "YSFL".encode() + pad(call.encode(), 10)
    logging.debug("sending: %s" % message)
    bm_sock.send(message)


def receive_salt() -> bytes:
    data = bm_sock.recv(20)
    logging.debug("received message: %s" % data)
    salt = data[16:]
    logging.debug("salt: %s" % salt)
    return salt


def send_challenge_message(call: str, salt: bytes, password: str):
    secret = salt + password.encode()
    secret_hash = sha256(secret).digest()
    message = "YSFK".encode() + pad(call.encode(), 10) + secret_hash
    logging.debug("sending: %s" % message)
    bm_sock.send(message)


def send_group_message(call: str, tg: int):
    message = "YSFO".encode() + pad(call.encode(), 10) + f"group={tg}".encode()
    logging.debug("sending: %s" % message)
    bm_sock.send(message)


def bm_to_pi():
    while True:
        data = bm_sock.recv(1024)
        logging.debug("received message: %s" % data)

        if "YSFNAK" in str(data):
            logging.error("Brandmeister returned an error")

        if client_addr != "":
            pi_sock.sendto(data, client_addr)


def pi_to_bm():
    while True:
        data, addr = pi_sock.recvfrom(1024)  # buffer size is 1024 bytes
        set_client_addr(addr)
        logging.debug("received message from pi-star: %s" % data)

        if "YSFP" in str(data):
            continue

        if "YSFD" in str(data):
            ysffich.decode(data[40:])
            dg_id = ysffich.getSQ()

            if cur_dg_id != dg_id:
                logging.info(f"Changing TG to {dgid_tg[dg_id]} mapped from DG-ID {dg_id}")
                send_group_message(callsign, dgid_tg[dg_id])
                set_dg_id(dg_id)

        bm_sock.send(data)
        set_last_client_packet_timestamp()


def send_ping(call: str):
    while True:
        curr_ts = now()
        if curr_ts - last_client_packet_timestamp > 10:
            message = "YSFP".encode() + pad(call.encode(), 10)
            logging.debug("sending ping: %s" % message)
            bm_sock.send(message)
        time.sleep(10)


def login_and_set_tg():
    send_login_message(callsign)
    salt = receive_salt()
    send_challenge_message(callsign, salt, bm_password)
    send_group_message(callsign, default_tg)

    data = bm_sock.recv(1024)  # buffer size is 1024 bytes

    if "YSFNAK" in str(data):
        logging.error("Brandmeister returned an error - check password")
        sys.exit(0)


if __name__ == '__main__':
    config = configparser.ConfigParser()
    config.read("ysfdirect.conf")

    callsign = config["CONNECTION"]["callsign"]
    server_ip = config["CONNECTION"]["server_ip"]
    server_name = config["CONNECTION"]["server_name"]
    bm_port = int(config["CONNECTION"]["bm_port"])
    bm_password = config["CONNECTION"]["bm_password"]
    ysf_port = int(config["CONNECTION"]["ysf_port"])

    default_tg = int(config["TG"]["default_tg"])

    dgid_tg = {int(k): int(v) for k, v in config["DGID-TO-TG"].items()}

    tg_dgid = {v: k for k, v in dgid_tg.items()}

    client_addr = ""
    last_client_packet_timestamp = 0
    cur_dg_id = tg_dgid[default_tg]

    bm_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bm_sock.connect((server_ip, bm_port))

    pi_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    pi_sock.bind(("", ysf_port))

    loglevel = config["LOG"]["loglevel"]
    if config["LOG"]["logtype"] == "stdout":
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=loglevel)
    else:
        file = config["LOG"]["path"]
        logging.basicConfig(filename=file, format='%(asctime)s - %(levelname)s - %(message)s', level=loglevel)

    logging.info("Starting pYSFBMGateway")
    logging.info(f"Default TG {default_tg} mapped to DG-ID {cur_dg_id}")

    login_and_set_tg()

    ping_thread = threading.Thread(target=send_ping, args=(callsign,), daemon=True)
    ping_thread.start()

    bm2pi_thread = threading.Thread(target=bm_to_pi, daemon=True)
    bm2pi_thread.start()

    pi2bm_thread = threading.Thread(target=pi_to_bm, daemon=True)
    pi2bm_thread.start()

    signal.signal(signal.SIGINT, lambda a, b: sys.exit(0))
    while True:
        time.sleep(1)
