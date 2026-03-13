# client.py (USB agent that controls local Cleware USB and talks to the server)
import socket
import sys
import os
import time
from ctypes import windll
from ClewareUSBLib import cwUSB_getConfig, cwUSB_list_Devices, cwUSB_get_StateFromNum, cwUSB_set_StateToNum

RECONNECT_BASE_DELAY = 2
RECONNECT_MAX_DELAY = 30

def handle_command(command: str) -> str:
    """Execute a single hardware command locally and return a string response."""
    dll_path = os.environ.get(
        "CLEWARE_DLL_PATH",
        os.path.join(os.path.dirname(__file__), "Source", "USBaccessX64.dll")
    )
    mydll = windll.LoadLibrary(dll_path)
    mydll.FCWInitObject()
    devCnt = mydll.FCWOpenCleware(0)

    parts = (command or "").strip().split()
    if not parts:
        return "ERROR: EMPTY_CMD"

    cmd = parts[0].lower()

    if cmd == "list":
        # Note: return an empty string if no devices, so the server can still mark the node online
        return cwUSB_list_Devices() if devCnt > 0 else ""

    if devCnt == 0:
        return "NO_DEVICES"

    if len(parts) < 2:
        return "ERROR: Missing devID"

    try:
        devID = int(parts[1])
    except Exception:
        return "ERROR: devID must be int"

    if cmd == "state":
        return f"{cwUSB_get_StateFromNum(devID)}"

    if cmd == "turnon":
        cwUSB_set_StateToNum(devID, 1)
        return "OK"

    if cmd == "turnoff":
        cwUSB_set_StateToNum(devID, 0)
        return "OK"

    if cmd == "toggle":
        cur = cwUSB_get_StateFromNum(devID)
        cwUSB_set_StateToNum(devID, 0 if cur else 1)
        return "OK"

    return "ERROR: UNKNOWN_CMD"


def run_agent():
    # Read server host/port from your existing config
    host, port, _ = cwUSB_getConfig()
    hostname = socket.gethostname()
    print(f"[CLIENT] Will connect to server at {host}:{port} as {hostname}")

    delay = RECONNECT_BASE_DELAY
    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.connect((host, port))
                # Introduce ourselves (lowercased name will be stored server-side)
                s.sendall(f"HELLO {hostname}".encode("utf-8"))
                print("[CLIENT] Connected. Waiting for commands...")

                while True:
                    data = s.recv(4096)
                    if not data:
                        print("[CLIENT] Server closed connection.")
                        break

                    command = data.decode(errors="ignore").strip()
                    if not command:
                        s.sendall(b"ERROR: EMPTY_CMD")
                        continue

                    try:
                        resp = handle_command(command)
                    except Exception as e:
                        resp = f"ERROR: {e}"

                    s.sendall(resp.encode("utf-8"))

            # Disconnected: try to reconnect with backoff
            print(f"[CLIENT] Disconnected. Reconnecting in {delay}s ...")
            time.sleep(delay)
            delay = min(RECONNECT_MAX_DELAY, delay * 2)

        except Exception as e:
            print(f"[CLIENT] Connect error: {e}. Retrying in {delay}s ...")
            time.sleep(delay)
            delay = min(RECONNECT_MAX_DELAY, delay * 2)


if __name__ == "__main__":
    run_agent()