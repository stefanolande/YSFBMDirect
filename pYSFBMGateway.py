import configparser as configparser
import logging
import signal
import socket
import sys
import threading
import time

from utils import now, pad, validate_dg_id_map
from ysf import ysffich
from ysfd_protocol import send_group_message, login_and_set_tg

keep_running: bool = False


def set_last_client_packet_timestamp():
    global last_client_packet_timestamp
    last_client_packet_timestamp = now()


def set_dg_id(new_dg_id):
    global cur_dg_id
    cur_dg_id = new_dg_id


def set_client_addr(addr):
    global client_addr
    client_addr = addr


def bm_to_ysf():
    while keep_running:
        data = bm_sock.recv(1024)
        logging.debug("received message from BM: %s" % data)

        if "YSFNAK" in str(data):
            logging.error("Brandmeister returned an error")

        if client_addr != "":
            ysf_sock.sendto(data, client_addr)


def ysf_to_bm():
    while keep_running:
        data, addr = ysf_sock.recvfrom(1024)  # buffer size is 1024 bytes
        set_client_addr(addr)
        logging.debug("received message from YSFGateway: %s" % data)

        if "YSFP" in str(data):
            ysf_sock.sendto("YSFP".encode() + pad("YSFBMG".encode(), 10), client_addr)
            continue

        if "YSFD" in str(data):
            ysffich.decode(data[40:])
            dg_id = ysffich.getSQ()

            if cur_dg_id != dg_id and dg_id in dgid_to_tg:
                logging.info(f"Changing TG to {dgid_to_tg[dg_id]} mapped from DG-ID {dg_id}")
                send_group_message(callsign, dgid_to_tg[dg_id], bm_sock)
                set_dg_id(dg_id)

        bm_sock.send(data)
        set_last_client_packet_timestamp()


def send_ping(call: str):
    while keep_running:
        curr_ts = now()
        if curr_ts - last_client_packet_timestamp > 10:
            message = "YSFP".encode() + pad(call.encode(), 10)
            logging.debug("sending ping: %s" % message)
            bm_sock.send(message)
        time.sleep(10)


if __name__ == '__main__':
    config = configparser.ConfigParser()
    config.read("pYSFBMGateway.conf")

    loglevel = config["LOG"]["loglevel"]
    if config["LOG"]["logtype"] == "stdout":
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=loglevel)
    else:
        file = config["LOG"]["path"]
        logging.basicConfig(filename=file, format='%(asctime)s - %(levelname)s - %(message)s', level=loglevel)

    callsign = config["CONNECTION"]["callsign"]
    server_ip = config["CONNECTION"]["server_ip"]
    server_name = config["CONNECTION"]["server_name"]
    bm_port = int(config["CONNECTION"]["bm_port"])
    bm_password = config["CONNECTION"]["bm_password"]
    ysf_port = int(config["CONNECTION"]["ysf_port"])

    default_tg = int(config["TG"]["default_tg"])

    dgid_to_tg = {int(k): int(v) for k, v in config["DGID-TO-TG"].items()}

    if not validate_dg_id_map(dgid_to_tg):
        message = "DGID-TO-TG configuration is invalid - check for duplicated TGs"
        logging.error(message)
        print(message)
        sys.exit(1)

    tg_to_dgid = {v: k for k, v in dgid_to_tg.items()}

    client_addr = ""
    last_client_packet_timestamp = 0
    cur_dg_id = tg_to_dgid[default_tg]

    bm_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bm_sock.connect((server_ip, bm_port))

    ysf_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ysf_sock.bind(("", ysf_port))

    logging.info("Starting pYSFBMGateway")
    logging.info(f"Default TG {default_tg} mapped to DG-ID {cur_dg_id}")

    login_and_set_tg(callsign, bm_password, default_tg, bm_sock)

    keep_running = True

    ping_thread = threading.Thread(target=send_ping, args=(callsign,), daemon=True)
    ping_thread.start()

    bm2ysf_thread = threading.Thread(target=bm_to_ysf)
    bm2ysf_thread.start()

    ysf2bm_thread = threading.Thread(target=ysf_to_bm)
    ysf2bm_thread.start()


    def signal_handler(signum, _) -> None:
        global keep_running
        keep_running = False

        bm_sock.close()
        bm_sock.send(b"")
        ysf_sock.close()
        ysf_sock.send(b"")


    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, signal_handler)
    else:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    ping_thread.join()
    bm2ysf_thread.join()
    ysf2bm_thread.join()
