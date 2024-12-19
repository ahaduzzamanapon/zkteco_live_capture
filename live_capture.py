from zk import ZK
from typing import Type
from dotenv import load_dotenv
import requests
import subprocess
import os
import threading
from struct import unpack
from socket import timeout
import time
from distutils.util import strtobool
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

# Load environment variables
load_dotenv()

# Log file size from .env or default to 10MB
log_file_size = int(os.getenv('LOG_FILE_SIZE', '10485760').split('#')[0].strip())

# Set up logging
log_file_path = os.path.join(os.getcwd(), 'live-capture.log')
handler = RotatingFileHandler(log_file_path, maxBytes=log_file_size, backupCount=3)

formatter = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
handler.setFormatter(formatter)

logger = logging.getLogger("zkteco-live-capture")
logger.addHandler(handler)
logger.setLevel(logging.INFO)

class ZktecoWrapper:
    def __init__(self, zk_class: Type[ZK], ip, port=4370, verbose=False, timeout=None, password=0, force_udp=False):
        try:
            self.zk = zk_class(
                ip,
                port=port,
                timeout=timeout,
                password=password,
                force_udp=force_udp,
                verbose=verbose
            )
            self.connect(True)
        except Exception as e:
            logger.error(f"Could not connect to Zkteco device on {ip}:{port} : {e}")

    def start_live_capture_thread(self):
        self.live_capture_thread = threading.Thread(target=self.live_capture)
        self.live_capture_thread.start()

    def live_capture(self, new_timeout=None):
        try:
            self.zk.cancel_capture()
            self.zk.verify_user()
            self.enable_device()
            self.zk.reg_event(1)
            self.zk._ZK__sock.settimeout(new_timeout)
            self.zk.end_live_capture = False
            while not self.zk.end_live_capture:
                try:
                    data_recv = self.zk._ZK__sock.recv(1032)
                    self.zk._ZK__ack_ok()

                    if self.zk.tcp:
                        size = unpack('<HHI', data_recv[:8])[2]
                        header = unpack('HHHH', data_recv[8:16])
                        data = data_recv[16:]
                    else:
                        size = len(data_recv)
                        header = unpack('<4H', data_recv[:8])
                        data = data_recv[8:]
                
                    if not header[0] == 500 or not len(data):
                        continue

                    while len(data) >= 10:
                        if len(data) == 10:
                            user_id, _status, _punch, _timehex = unpack('<HBB6s', data)
                            data = data[10:]
                        elif len(data) == 12:
                            user_id, _status, _punch, _timehex = unpack('<IBB6s', data)
                            data = data[12:]
                        elif len(data) == 14:
                            user_id, _status, _punch, _timehex, _other = unpack('<HBB6s4s', data)
                            data = data[14:]
                        elif len(data) == 32:
                            user_id, _status, _punch, _timehex = unpack('<24sBB6s', data[:32])
                            data = data[32:]
                        elif len(data) == 36:
                            user_id, _status, _punch, _timehex, _other = unpack('<24sBB6s4s', data[:36])
                            data = data[36:]
                        elif len(data) == 37:
                            user_id, _status, _punch, _timehex, _other = unpack('<24sBB6s5s', data[:37])
                            data = data[37:]
                        elif len(data) >= 52:
                            user_id, _status, _punch, _timehex, _other = unpack('<24sBB6s20s', data[:52])
                            data = data[52:]
                        if isinstance(user_id, int):
                            user_id = str(user_id)
                        else:
                            user_id = (user_id.split(b'\x00')[0]).decode(errors='ignore')
                        self.send_attendace_request(user_id)
                except timeout:
                    logger.info("time out")
                except BlockingIOError:
                    pass
                except (KeyboardInterrupt, SystemExit):
                    break
            self.zk._ZK__sock.settimeout(None)
            self.zk.reg_event(0)
        except Exception as e:
            logger.error(f"Error in live_capture: {e}")

    def send_attendace_request(self, member_id):
        try:
            if self.zk.end_live_capture:
                return
            
            # Get the current date and time
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Print the attendance data
            print(f"[{current_time}] Attendance captured for member_id: {member_id}")
        except Exception as e:
            logger.error(f"Error in send_attendance_request: {str(e)}")

    def connect(self, enable_live_capture=False):
        if self.zk.is_connect and self.zk.helper.test_ping():
            return
        retry_count = 0
        max_retries_log = 10

        while True:
            try:
                self.zk.connect()
                logger.info("Connected to ZK device successfully")
                retry_count = 0
                if enable_live_capture:
                    self.start_live_capture_thread()
                self.keepAlive()
                return
            except Exception as e:
                retry_count += 1
                if retry_count < max_retries_log:
                    logger.warning(f"Failed to connect to ZK device. Retrying... ({e})")
                time.sleep(6)
                continue

    def keepAlive(self):
        while True:
            isDeviceAlive = self.zk.helper.test_ping()
            if not isDeviceAlive:
                logger.warning("Terminating zkteco-live-capture service.")
                self.terminate_service()
                return
            time.sleep(15)

    def enable_device(self):
        self.zk.enable_device()

    def disable_device(self):
        self.zk.disable_device()

    def terminate_service(self):
        sudo_password = os.environ.get('PASSWORD')
        command = ["sudo", "-S", "systemctl", "kill", "--signal=SIGHUP", os.environ.get('SERVICE_NAME')]
        subprocess.run(command, input=sudo_password, check=True, text=True, user=os.environ.get('SUBPROCESS_USER'))


if __name__ == "__main__":
    devices = [
        {
            "ip": os.environ.get('DEVICE_IP_1'),
            "port": int(os.environ.get('DEVICE_PORT_1', '4370')),
        },
        {
            "ip": os.environ.get('DEVICE_IP_2'),
            "port": int(os.environ.get('DEVICE_PORT_2', '4370')),
        }
    ]

    for device in devices:
        ip = device["ip"]
        port = device["port"]

        if not ip:
            logger.error("Device IP is not set in environment variables.")
            continue

        logger.info(f"Starting ZktecoWrapper for device at {ip}:{port}")
        threading.Thread(target=ZktecoWrapper, kwargs={
            "zk_class": ZK,
            "ip": ip,
            "port": port,
            "verbose": bool(strtobool(os.getenv("FLASK_DEBUG", "false"))),
        }).start()
