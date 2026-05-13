# client.py — robust Cleware USB agent
import socket
import threading
import time
import os
import queue

from ClewareUSBServer import*
from ClewareUSBLib import (
    cwUSB_setup,
    cwUSB_cleanup,
    cwUSB_list_Devices,
    cwUSB_get_StateFromNum,
    cwUSB_set_StateToNum,
    cwUSB_set_NametoNum,
    cwUSB_get_NameFromNum,
    #cwiNoOfDevices,
)

RECONNECT_BASE_DELAY = 2
RECONNECT_MAX_DELAY = 30
USB_CMD_TIMEOUT = 10


# ===================== USB WORKER =====================

USB_QUEUE = queue.Queue()
USB_LOCK = threading.Lock()
USB_READY = False


class USBCommand:
    def __init__(self, cmd, args=None):
        self.cmd = cmd
        self.args = args or []
        self.result = None
        self.event = threading.Event()


def usb_worker():
    global USB_READY

    while True:
        job = USB_QUEUE.get()
        try:
            with USB_LOCK:
                if job.cmd == "state_all":
                    entries = []
                    for dev in range(cwUSB.FCWOpenCleware(0)):
                        try:
                            s = cwUSB_get_StateFromNum(dev)
                            n = cwUSB_get_NameFromNum(dev)
                            entries.append(f"{dev}:{s}:{n}")
                        except Exception:
                            pass
                    job.result = ",".join(entries)

                elif job.cmd == "state":
                    dev = int(job.args[0])
                    job.result = str(cwUSB_get_StateFromNum(dev))

                elif job.cmd == "set":
                    dev, val = int(job.args[0]), int(job.args[1])
                    cwUSB_set_StateToNum(dev, val)
                    job.result = "OK"

                elif job.cmd == "rename":
                    dev = int(job.args[0])
                    name = " ".join(job.args[1:])
                    cwUSB_set_NametoNum(dev, name)
                    job.result = "OK"

                elif job.cmd == "list":
                    job.result = cwUSB_list_Devices()

                else:
                    job.result = "ERROR: UNKNOWN_CMD"

        except Exception as e:
            # USB may be temporarily unavailable — try to recover
            try:
                cwUSB_cleanup()
                time.sleep(1)
                cwUSB_setup()
            except Exception:
                pass
            job.result = f"ERROR:{e}"

        job.event.set()


threading.Thread(target=usb_worker, daemon=True).start()


def usb_execute(cmd, args=None):
    job = USBCommand(cmd, args)
    USB_QUEUE.put(job)
    ok = job.event.wait(timeout=USB_CMD_TIMEOUT)
    if not ok:
        return "ERROR: USB_TIMEOUT"
    return job.result


# ===================== SOCKET CLIENT =====================

def run_agent():
    host, port, _ = cwUSB_getConfig()
    node = socket.gethostname().lower()

    print(f"[CLIENT] Starting Cleware agent as {node}")

    # Initialize USB once
    cwUSB_setup()

    delay = RECONNECT_BASE_DELAY

    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.connect((host, port))
                s.sendall(f"HELLO {node}\n".encode())

                print("[CLIENT] Connected to server")

                delay = RECONNECT_BASE_DELAY

                while True:
                    data = s.recv(4096)
                    if not data:
                        raise ConnectionError("Server disconnected")

                    line = data.decode(errors="ignore").strip()
                    if not line:
                        continue

                    parts = line.split()
                    cmd, args = parts[0], parts[1:]

                    resp = usb_execute(cmd, args)
                    s.sendall((resp + "\n").encode())

        except Exception as e:
            print(f"[CLIENT] Disconnected: {e}")
            time.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)


if __name__ == "__main__":
    run_agent()