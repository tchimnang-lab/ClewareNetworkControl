
import socket
import threading
import time
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, parse_qs
from queue import Queue
from typing import Dict, Tuple, List
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from ClewareUSBLib import *
from ctypes import windll

# ===================== CONFIG =====================
SOCKET_TIMEOUT = 5
STATE_POLL_INTERVAL = 20
WEB_PORT = int(os.environ.get("CLEWARE_WEB_PORT", "8080"))
MAX_USB_QUEUE = 100

WATCHDOG_DEVICE     = 0x05
AUTORESET_DEVICE    = 0x06
WATCHDOGXP_DEVICE   = 0x07
# ===================== GLOBALS =====================
connected_clients: Dict[str, Tuple[socket.socket, Tuple[str, int]]] = {}
connected_lock = threading.Lock()

STATE_CACHE: Dict[Tuple[str, int], str] = {}
STATE_CACHE_LOCK = threading.Lock()

DEVICE_NAME_CACHE: Dict[Tuple[str, int], str] = {}

USB_QUEUE: Queue = Queue()

server_name = None

# USB health & recovery
USB_RECOVERY_LOCK = threading.Lock()
USB_HEALTH_ERRORS = 0
USB_HEALTH_THRESHOLD = 3
LAST_USB_RECOVERY = 0
USB_RECOVERY_INTERVAL = 180  # seconds (3 minutes)
USB_START_TIME = time.time()
USB_MAX_UPTIME = 600          # 10 minutes before forced device reset
PANIC_WATCHDOG = False

#Escalation counters
USB_RECOVERY_COUNT = 0
USB_RESET_COUNT = 0
USB_MAX_RECOVERIES = 3
MAX_DEVICE_RESETS = 2
# ===================== LOGGING =====================
def log_event(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# ===================== DLL =====================
DLL_HANDLE = None

def load_local_dll():
    global DLL_HANDLE
    if DLL_HANDLE:
        return DLL_HANDLE

    path = os.path.join(os.path.dirname(__file__), "Source", "USBaccessX64.dll")
    DLL_HANDLE = windll.LoadLibrary(path)
    return DLL_HANDLE

# ===================== USB WORKER =====================
class USBCommand:
    def __init__(self, cmd, devID=None, extra=None):
        self.cmd = cmd
        self.devID = devID
        self.extra = extra
        self.result = None
        self.event = threading.Event()


def usb_worker():
    dll = load_local_dll()
    dll.FCWInitObject()
    dll.FCWOpenCleware(0)

    while True:
        job: USBCommand = USB_QUEUE.get()

        try:
            if job.cmd == "list":
                job.result = cwUSB_list_Devices()

            elif job.cmd == "state":
                job.result = str(cwUSB_get_StateFromNum(job.devID))

            elif job.cmd == "set":
                cwUSB_set_StateToNum(job.devID, job.extra)
                job.result = "OK"

            elif job.cmd == "rename":
                cwUSB_set_NametoNum(job.devID, job.extra)
                job.result = "OK"

        except Exception as e:
            job.result = f"ERROR:{e}"

        job.event.set()

threading.Thread(target=usb_worker, daemon=True).start()

def usb_execute(cmd, devID=None, extra=None):
    if USB_QUEUE.qsize() > MAX_USB_QUEUE:
        return "BUSY"

    job = USBCommand(cmd, devID, extra)
    USB_QUEUE.put(job)
    job.event.wait(timeout=5)
    return job.result

# Full USB recovery

def usb_escalating_recover(devID=None, reason="unknown"):
    global USB_RECOVERY_COUNT, LAST_USB_RECOVERY, USB_START_TIME, PANIC_WATCHDOG

    with USB_RECOVERY_LOCK:
        USB_RECOVERY_COUNT += 1
        log_event(f"[USB] Escalation #{USB_RECOVERY_COUNT} — reason: {reason}")

        # ---------- LEVEL 1: full USB re-enumeration ----------
        try:
            cwUSB_cleanup()
        except:
            pass

        time.sleep(1)

        try:
            cwUSB_setup()
            LAST_USB_RECOVERY = time.time()
            log_event("[USB] Re-enumeration successful")
        except Exception as e:
            log_event(f"[USB] Re-enumeration failed: {e}")

        # ---------- LEVEL 3: time-based device reset ----------
        uptime = time.time() - USB_START_TIME
        if uptime > USB_MAX_UPTIME:
            log_event("[USB] Max USB uptime exceeded — resetting devices")

            txt = usb_execute("list")
            if txt:
                for line in txt.splitlines():
                    dev = extract_dev(line)
                    if dev is not None:
                        try:
                            cwUSB_ResetDevice(dev)
                            log_event(f"[USB] Device {dev} reset")
                        except:
                            pass

            USB_START_TIME = time.time()
            USB_RECOVERY_COUNT = 0
            time.sleep(5)
            return

        # ---------- LEVEL 4: panic watchdog ----------
        if USB_RECOVERY_COUNT >= USB_MAX_RECOVERIES:
            log_event("[USB] PANIC: unrecoverable USB failure — triggering watchdog reboot")
            PANIC_WATCHDOG = True

# ===================== TCP =====================
def send_msg(sock, msg):
    sock.sendall((msg + "\n").encode())


def recv_msg(sock):
    buffer = ""
    while True:
        data = sock.recv(1024).decode()
        if not data:
            return None
        buffer += data
        if "\n" in buffer:
            msg, _ = buffer.split("\n", 1)
            return msg.strip()


def rpc_call(sock, msg):
    try:
        send_msg(sock, msg)
        return recv_msg(sock)
    except:
        return None

# ===================== STATE LOOP =====================
def extract_dev(line):
    m = re.search(r"serial number=\s*(\d+)", line)
    return int(m.group(1)) if m else None


def extract_name(line):
    m = re.search(r"Name=(.*)$", line)
    return m.group(1).strip() if m else ""


def state_loop():
    global USB_HEALTH_ERRORS
    global LAST_USB_RECOVERY

    while True:
        cycle_ok = True
        last_bad_dev = None

        txt = usb_execute("list")

        if not txt:
            cycle_ok = False
        else:
            lines = [l.strip() for l in txt.splitlines() if l.strip()]
            if not lines:
                cycle_ok = False
            else:
                for line in lines:
                    devID = extract_dev(line)
                    
                    if not txt or not lines:
                        usb_escalating_recover(reason="device list empty")

                    if devID is None:
                        continue

                    name = extract_name(line)
                    raw_state = usb_execute("state", devID)

                    if raw_state not in ("0", "1"):
                        cycle_ok = False
                        last_bad_dev = devID
                        break   # stop scanning — failure detected

                    with STATE_CACHE_LOCK:
                        STATE_CACHE[(server_name, devID)] = raw_state
                        DEVICE_NAME_CACHE[(server_name, devID)] = name

        # ---- health evaluation (ONCE per cycle) ----
        if cycle_ok:
            USB_HEALTH_ERRORS = 0
        else:
            USB_HEALTH_ERRORS += 1

        # ---- escalation based on errors ----
        if USB_HEALTH_ERRORS >= USB_HEALTH_THRESHOLD:
            log_event("USB health degraded — escalating recovery")
            usb_escalating_recover(last_bad_dev)
            USB_HEALTH_ERRORS = 0
            LAST_USB_RECOVERY = time.time()

        # ---- periodic preventive recovery ----
        now = time.time()
        if now - LAST_USB_RECOVERY > USB_RECOVERY_INTERVAL:
            usb_escalating_recover(reason="periodic refresh")
            LAST_USB_RECOVERY = now

        # remote nodes via RPC
        with connected_lock:
            clients = dict(connected_clients)

        for cname, (sock, _) in clients.items():
            reply = rpc_call(sock, "state_all")
            if not reply:
                continue

            for entry in reply.split(","):
                try:
                    dev, state, name = entry.split(":")
                    dev = int(dev)

                    with STATE_CACHE_LOCK:
                        STATE_CACHE[(cname, dev)] = state.strip()  # raw
                        DEVICE_NAME_CACHE[(cname, dev)] = name.strip()

                except:
                    pass
    
        time.sleep(STATE_POLL_INTERVAL)

threading.Thread(target=state_loop, daemon=True).start()

# ============== Watchdog loop ==============
def watchdog_loop():
    """
    Feeds watchdog devices unless PANIC_WATCHDOG is set.
    When panic is active, feeding stops → system reboot.
    """
    global PANIC_WATCHDOG

    print("[Watchdog] Started watchdog feeder thread")

    while True:
        if PANIC_WATCHDOG:
            log_event("[Watchdog] PANIC MODE — watchdog feeding stopped")
            while True:
                time.sleep(1)   # intentionally do nothing

        txt = usb_execute("list")
        if not txt:
            time.sleep(1)
            continue

        for line in txt.splitlines():
            devID = extract_dev(line)
            if devID is None:
                continue

            t = cwUSB_get_USBType(devID)

            try:
                # WATCHDOG / AUTORESET (minutes)
                if t in (WATCHDOG_DEVICE, AUTORESET_DEVICE):
                    cwUSB_CalmWatchdog(devID, 1, 0)

                # WATCHDOG XP (seconds)
                elif t == WATCHDOGXP_DEVICE:
                    cwUSB_CalmWatchdog(devID, 1, 0)

            except:
                pass

        time.sleep(1)

threading.Thread(target=watchdog_loop, daemon=True).start()

# ===================== COMMAND =====================
def execute_cmd(node, dev, action, extra=None):
    node = node.lower()
    dev = int(dev)

    if node == server_name:
        if action == "toggle":
            cur = usb_execute("state", dev)
            new = 0 if cur == "1" else 1
            return usb_execute("set", dev, new)
        elif action == "on":
            return usb_execute("set", dev, 1)
        elif action == "off":
            return usb_execute("set", dev, 0)
        elif action == "rename":
            return usb_execute("rename", dev, extra)

    with connected_lock:
        entry = connected_clients.get(node)
    if not entry:
        return "NOCLIENT"

    sock, _ = entry
    return rpc_call(sock, f"{action} {dev} {extra or ''}".strip())

# ===================== WEB =====================
class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/action":
            q = parse_qs(parsed.query)
            node = q.get("node", [""])[0]
            dev = q.get("dev", [""])[0]
            cmd = q.get("cmd", [""])[0]
            name = q.get("name", [""])[0]

            execute_cmd(node, dev, cmd, name)
            log_event(f"{cmd} {node}:{dev}")

            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        rows = []
        with STATE_CACHE_LOCK:
            for (node, dev), state in STATE_CACHE.items():
                name = DEVICE_NAME_CACHE.get((node, dev), "")
                state_txt = "ON" if state == "1" else "OFF" if state == "0" else "?"
                state_class = "state-on" if state == "1" else "state-off" if state == "0" else "state-unk"

                rows.append(f"""
                <tr>
                    <td>{node}</td>
                    <td>{dev}</td>
                    <td>{name}</td>
                    <td class='{state_class}'>{state_txt}</td>
                    <td>
                        <a class='btn' href='#' onclick="doAction('{node}','{dev}','on')">On</a>
                        <a class='btn' href='#' onclick="doAction('{node}','{dev}','off')">Off</a>
                        <a class='btn' href='#' onclick="doAction('{node}','{dev}','toggle')">Toggle</a>                      
                        <form style='display:inline' action='/action' onsubmit="renameDevice(event, this)">
                            <input type='hidden' name='node' value='{node}'>
                            <input type='hidden' name='dev' value='{dev}'>
                            <input type='hidden' name='cmd' value='rename'>
                            <input name='name' placeholder='Rename (press Enter)'>
                        </form>
                    </td>
                </tr>
                """)

        html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset='UTF-8'>
<title>Cleware Switch Dashboard</title>
<meta http-equiv='refresh' content='60' id='refreshMeta'>
<style>
body {{ font-family: Arial; background:#1e1e1e; color:#ddd; margin:20px; }}
h2 {{ color:#4fc3f7; }}
table {{ border-collapse:collapse; width:100%; background:#2a2a2a; text-align: center;}}
th,td {{ padding:10px; border-bottom:1px solid #444; }}
th {{ background:#333; cursor:pointer; }}
tr:hover {{ background:#3a3a3a; }}
a {{ color:#4fc3f7; margin-right:5px; }}
a.btn {{
    display: inline-block; padding: 4px 8px; margin-right: 6px;
    border: 1px solid #aaa; border-radius: 4px;
    background: #eee; color: #000; text-decoration: none;
}}
a.btn:hover {{ background: #ddd; }}
input {{ padding:4px; }}
.state-on {{ color:#4caf50; font-weight: bold;}}
.state-off {{ color:#f44336; font-weight: bold;}}
.state-unk {{ color:#aaa; font-weight: bold;}}
</style>
</head>
<body>
<h1>Cleware Switch Dashboard</h1>
<h2>Server: {server_name}</h2>

<table id='deviceTable'>
<tr>
<th onclick="sortTable(0)">Node</th>
<th onclick="sortTable(1)">ID</th>
<th onclick="sortTable(2)">Name</th>
<th>State</th>
<th>Actions</th>
</tr>
{''.join(rows)}
</table>

<script>
// pause refresh while typing
document.querySelectorAll('input').forEach(inp => {{
    inp.addEventListener('focus', () => {{
        document.getElementById('refreshMeta').setAttribute('content', '999999');
    }});
    inp.addEventListener('blur', () => {{
        document.getElementById('refreshMeta').setAttribute('content', '5');
    }});
}});

// sorting
function sortTable(col) {{
    let table = document.getElementById("deviceTable");
    let rows = Array.from(table.rows).slice(1);
    let asc = table.getAttribute("data-sort") !== "asc";

    rows.sort((a, b) => {{
        let A = a.cells[col].innerText.toLowerCase();
        let B = b.cells[col].innerText.toLowerCase();

        if (!isNaN(A) && !isNaN(B)) {{
            return asc ? A - B : B - A;
        }}
        return asc ? A.localeCompare(B) : B.localeCompare(A);
    }});

    rows.forEach(r => table.appendChild(r));
    table.setAttribute("data-sort", asc ? "asc" : "desc");
}}

// Auto refresh after action
function doAction(node, dev, cmd) {{
    fetch(`/action?node=${{node}}&dev=${{dev}}&cmd=${{cmd}}`)
        .then(() => location.reload());
}}

// Auto refresh after renaming
function renameDevice(ev, form) {{
    ev.preventDefault();
    const data = new FormData(form);
    const params = new URLSearchParams(data).toString();

    fetch("/action?" + params)
        .then(() => location.reload());
}}
</script>

</body>
</html>
"""

        self.send_response(200)
        self.end_headers()
        self.wfile.write(html.encode())


# ===================== SERVER =====================
def accept_loop(sock):
    while True:
        try:
            conn, addr = sock.accept()
            name = recv_msg(conn)

            if not name or not name.startswith("HELLO "):
                conn.close()
                continue

            cname = name.split()[1]

            with connected_lock:
                connected_clients[cname] = (conn, addr)

            log_event(f"Client connected: {cname}")

        except:
            time.sleep(0.1)

# ===================== MAIN =====================
def main():
    global server_name

    server_name = socket.gethostname().lower()

    cwUSB_setup()

    host, port, _ = cwUSB_getConfig()

    s = socket.socket()
    s.bind(("", port))
    s.listen()

    threading.Thread(target=accept_loop, args=(s,), daemon=True).start()

    web = ThreadingHTTPServer(("0.0.0.0", WEB_PORT), Handler)
    web.serve_forever()


if __name__ == "__main__":
    main()
