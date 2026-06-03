import socket
import time
import os
import queue
import tkinter as tk
import configparser
import threading

from Cleware_USB_Server import*
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

isRunning = False

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
def get_config():
    config = configparser.ConfigParser(allow_unnamed_section=True)
    config.read('ClewareClientConfig.ini')
    host = '0.0.0.0' # Default host
    port = 0          # Default port
    dll  = r"USBaccessX64.dll"
    try:
        NetConfig = config[configparser.UNNAMED_SECTION]
        host = NetConfig.get('host', host)
        port = NetConfig.getint('port', port)
        dll  = NetConfig.get('dll', dll)
    except Exception:
        pass
    return [host, port, dll]

def run_agent():
    global isRunning

    host, port, _ = get_config()
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

                isRunning = True
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
            isRunning = False
            time.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)
            break  # Exit after one failed attempt for better UX in this demo
    

def save_config():
    config = configparser.ConfigParser(allow_unnamed_section=True)
    config.read('ClewareClientConfig.ini')

    host = ServerAddress.get().strip()
    port = Port.get().strip()
    if not host or not port:
        set_status("Please provide host and port", "lightcoral")
        return
    try:
        int(port)
    except ValueError:
        set_status("Port must be an integer", "lightcoral")
        return
    with open("ClewareClientConfig.ini", "w") as f:
        f.write(f"host = {host}\nport = {port}\ndll  = USBaccessX64.dll")
    set_status("Connection info saved", "lightgreen")
    
def connect():

    global isRunning
    isRunning = False  # Reset before trying to connect

    def agent_thread():
        run_agent()

    set_status("Connecting...", "khaki")
    threading.Thread(target=agent_thread, daemon=True).start()

    # Wait for connection attempt to finish (success or failure)
    for _ in range(30):  # Wait up to ~3 seconds (30 x 0.1s)
        if isRunning:
            set_status("Connected", "lightgreen")
            print("Agent is running")
            break
        time.sleep(0.1)
    else:
        set_status("Connection failed", "lightcoral")
    

def set_status(text, bg):
    text_widget.config(state="normal")
    text_widget.delete("1.0", "end")
    text_widget.insert("1.0", text)
    text_widget.config(state="disabled")
    messageVar.config(text=text, bg=bg)

#======================GUI==========================
root = tk.Tk()
root.title("Client")

tk.Label(root, text=f"Please insert server details (e.g. Address: 0.0.0.0, Port: 54757)").grid(row=0, column=0, columnspan=2, pady=(8,10))
#tk.Label(root, text="").grid(row=1, column=0)  # Spacer
tk.Label(root, text=f"Current Address: {get_config()[0]}, Port: {get_config()[1]} (If ok click  Connect)").grid(row=2, column=0, columnspan=2, pady=(8,10))

tk.Label(root, text="Server Address").grid(row=3, column=0, sticky="e", padx=4, pady=2)
tk.Label(root, text="Port").grid(row=4, column=0, sticky="e", padx=4, pady=2)

ServerAddress = tk.Entry(root)
Port = tk.Entry(root)
ServerAddress.grid(row=3, column=1, padx=4, pady=2)
Port.grid(row=4, column=1, padx=4, pady=2)

button = tk.Button(root, text="Save", width=20, command=save_config)
button.grid(row=5, column=0, columnspan=1, pady=5)

button2 = tk.Button(root, text="Connect", width=20, command=connect)
button2.grid(row=5, column=1, columnspan=1, pady=5)

text_widget = tk.Text(root, height=1, width=35, state="disabled")
text_widget.grid(row=6, column=0, columnspan=2, pady=(0,8))

messageVar = tk.Label(root, text="", width=40)
messageVar.grid(row=7, column=0, columnspan=2, pady=(0,8))
    
if __name__ == "__main__":
    root.mainloop()