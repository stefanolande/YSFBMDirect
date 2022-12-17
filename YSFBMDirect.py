import configparser as configparser
import logging
import signal
import socket
import sys
import threading
import time
import traceback

from utils import now, validate_dg_id_map, close_socket, consume_tail, send_tg_change_tx
from ysf import ysffich, ysfpayload
from ysf.ysffich import DT
from ysfd_protocol import send_tg_message, login_and_set_tg, send_logout_message

keep_running: bool = True
logged_in: bool = False

maybe_salt = []
is_salt_received = threading.Event()

ping_awaiting_response = 0
last_ping_time = 0

max_failed_pings = 10
ping_ttl = 30  # seconds

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
    global keep_running
    global logged_in
    global ping_awaiting_response
    while keep_running:
        try:
            data = bm_sock.recv(1024)
            logging.debug("received message from BM: %s" % data)

            if data == b"":
                continue

            if "YSFP" in str(data) and logged_in and ping_awaiting_response > 0:
                ping_awaiting_response -= 1

            if "YSFNAK" in str(data):
                logging.error("Brandmeister returned an error")
                logged_in = False

            if "YSFACK" in str(data) and len(data) == 20:
                salt = data[16:]
                maybe_salt.append(salt)
                is_salt_received.set()
                logging.debug("salt: %s" % salt)
                continue

            if "YSFACK" in str(data):
                continue

            if "YSFD" in str(data) and show_dgid_callsing:
                ysffich.decode(data[40:])
                fn = ysffich.getFN()
                dt = ysffich.getDT()

                if fn == 1 and dt == DT.VD2:
                    payload = bytearray(data[35:])
                    orig = str(data[14:24], 'utf-8').strip()
                    src = (str(cur_dg_id) + '/' + orig).ljust(10).encode()
                    ysfpayload.writeVDMmode2Data(payload, src)
                    data = data[:35] + payload

            if client_addr != "":
                ysf_sock.sendto(data, client_addr)
        except Exception as e:
            logging.error(traceback.format_exc())
            terminate()


def ysf_to_bm():
    global keep_running
    global logged_in
    global ping_awaiting_response
    global last_ping_time

    while keep_running:
        try:
            data, addr = ysf_sock.recvfrom(1024)
            set_client_addr(addr)
            logging.debug("received message from YSFGateway: %s" % data)

            if data == b"":
                continue

            if "YSFP" in str(data) and logged_in:
                ping_awaiting_response += 1
                last_ping_time = now()
                if ping_awaiting_response > max_failed_pings:
                    logged_in = False
                    continue

            if "YSFP" in str(data) and not logged_in:
                logging.info(f"Logging in to BM and setting TG {default_tg}")
                login_and_set_tg(callsign, bm_password, default_tg, bm_sock, is_salt_received, maybe_salt)
                logged_in = True
                ping_awaiting_response = 0

            if "YSFD" in str(data):
                ysffich.decode(data[40:])
                dg_id = ysffich.getSQ()

                # avoid sending wires-x commands to BM
                if ysffich.getDT() == DT.DATA and (dg_id == 127 or dg_id == 0):
                    continue

                set_last_client_packet_timestamp()

                if cur_dg_id != dg_id and dg_id in dgid_to_tg and logged_in:
                    new_tg = dgid_to_tg[dg_id]
                    logging.info(f"Changing TG to {new_tg} mapped from DG-ID {dg_id}")
                    send_tg_message(callsign, new_tg, bm_sock)
                    set_dg_id(dg_id)
                    consume_tail(ysf_sock)

                    send_tg_change_tx(callsign, new_tg, ysf_sock, client_addr)

                    continue

            if "YSFU" in str(data):
                logged_in = False

            if "YSFO" in str(data):
                continue

            bm_sock.send(data)
        except Exception as e:
            logging.error(traceback.format_exc())
            terminate()


def timed_checks():
    global logged_in

    while keep_running:
        curr_ts = now()

        if logged_in and cur_dg_id != default_dgid and 0 < back_to_home_seconds < curr_ts - last_client_packet_timestamp:
            logging.info(f"Changing TG to default  {default_tg} after a timeout")
            send_tg_message(callsign, default_tg, bm_sock)
            set_dg_id(default_dgid)
            send_tg_change_tx(callsign, default_dgid, ysf_sock, client_addr)

        if logged_in and curr_ts - last_ping_time > ping_ttl:
            logging.info(f"logging out due to ping timeout")
            send_logout_message(callsign, bm_sock)
            logged_in = False

        time.sleep(10)


def terminate() -> None:
    logging.info("Exiting")
    global keep_running
    keep_running = False
    close_socket(bm_sock)
    close_socket(ysf_sock)


if __name__ == '__main__':
    config = configparser.ConfigParser()
    config.read("YSFBMDirect.conf")

    loglevel = config["LOG"]["loglevel"]
    logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=loglevel)

    callsign = config["CONNECTION"]["callsign"]
    server_ip = config["CONNECTION"]["server_ip"]
    bm_port = int(config["CONNECTION"]["bm_port"])
    bm_password = config["CONNECTION"]["bm_password"]
    ysf_port = int(config["CONNECTION"]["ysf_port"])

    default_tg = int(config["TG"]["default_tg"])
    back_to_home_time = int(config["TG"]["back_to_default_time"])
    back_to_home_seconds = back_to_home_time * 60
    show_dgid_callsing = config["TG"].get("show_dgid_callsign", "false").lower() == "true"

    dgid_to_tg = {int(k): int(v) for k, v in config["DGID-TO-TG"].items()}

    if not validate_dg_id_map(dgid_to_tg):
        message = "DGID-TO-TG configuration is invalid - check for duplicated TGs"
        logging.error(message)
        sys.exit(1)

    tg_to_dgid = {v: k for k, v in dgid_to_tg.items()}

    default_dgid = tg_to_dgid[default_tg]

    signal.signal(signal.SIGINT, lambda a, b: terminate())
    signal.signal(signal.SIGTERM, lambda a, b: terminate())

    client_addr = ""
    last_client_packet_timestamp = 0
    cur_dg_id = tg_to_dgid[default_tg]

    bm_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bm_sock.connect((server_ip, bm_port))

    ysf_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ysf_sock.bind(("", ysf_port))

    logging.info("Starting YSFBMDirect")
    logging.info(f"Default TG {default_tg} mapped to DG-ID {cur_dg_id}")

    bm2ysf_thread = threading.Thread(target=bm_to_ysf)
    bm2ysf_thread.start()

    ysf2bm_thread = threading.Thread(target=ysf_to_bm)
    ysf2bm_thread.start()

    timed_tasks_thread = threading.Thread(target=timed_checks)
    timed_tasks_thread.start()

    bm2ysf_thread.join()
    ysf2bm_thread.join()
    timed_tasks_thread.join()
