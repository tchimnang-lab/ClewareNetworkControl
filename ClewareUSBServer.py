# ============================================================
#  SERVER WITH WEB UI (STATUS COLORS, AUTO REFRESH, EVENT LOG)
# ============================================================

import socket
import threading
import sys
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote
from ctypes import windll
from typing import Dict, Tuple
from datetime import datetime

# Cleware helpers (must be available in your environment)
from ClewareUSBLib import (
    cwUSB_getConfig,
    cwUSB_list_Devices,
    cwUSB_get_StateFromNum,
    cwUSB_set_StateToNum,
)

# ========================================
# GLOBALS
# ========================================
connected_clients: Dict[str, Tuple[socket.socket, Tuple[str, int]]] = {}
connected_lock = threading.Lock()

event_log = []
event_log_lock = threading.Lock()
MAX_LOG_ENTRIES = 300

server_name = None
WEB_PORT = int(os.environ.get("CLEWARE_WEB_PORT", "8080"))

# ========================================
# EVENT LOGGING
# ========================================
def log_event(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {msg}"
    print(entry)
    with event_log_lock:
        event_log.append(entry)
        if len(event_log) > MAX_LOG_ENTRIES:
            event_log.pop(0)

# ========================================
# LOCAL USB HELPERS
# ========================================
def load_local_dll():
    dll_path = os.environ.get(
        "CLEWARE_DLL_PATH",
        os.path.join(os.path.dirname(__file__), "Source", "USBaccessX64.dll"),
    )
    return windll.LoadLibrary(dll_path)

def list_local_devices():
    try:
        dll = load_local_dll()
        dll.FCWInitObject()
        devCnt = dll.FCWOpenCleware(0)
        return cwUSB_list_Devices() if devCnt > 0 else ""
    except Exception as e:
        return f"ERROR:LOCAL_DEVICES({e})"

def local_execute(cmd, devID):
    try:
        dll = load_local_dll()
        dll.FCWInitObject()
        devCnt = dll.FCWOpenCleware(0)
        if devCnt == 0:
            return "NO_LOCAL_DEVICES"

        if cmd == "state":
            return f"{cwUSB_get_StateFromNum(devID)}"

        if cmd == "turnon":
            cwUSB_set_StateToNum(devID, 1)
            log_event(f"{server_name.upper()}: Turned ON device {devID}")
            return "OK"

        if cmd == "turnoff":
            cwUSB_set_StateToNum(devID, 0)
            log_event(f"{server_name.upper()}: Turned OFF device {devID}")
            return "OK"

        if cmd == "toggle":
            cur = cwUSB_get_StateFromNum(devID)
            cwUSB_set_StateToNum(devID, 0 if cur else 1)
            log_event(f"{server_name.upper()}: Toggled device {devID}")
            return "OK"

        return "UNKNOWN_LOCAL_CMD"

    except Exception as e:
        return f"ERROR:{e}"

# ========================================
# CLIENT FORWARDING
# ========================================
def forward_to_client(client_name, command):
    cname = client_name.lower()
    with connected_lock:
        entry = connected_clients.get(cname)

    if not entry:
        return "ERROR: CLIENT_NOT_CONNECTED"

    sock, _ = entry
    try:
        sock.sendall(command.encode("utf-8"))
        reply = sock.recv(65536)

        if not reply:
            with connected_lock:
                connected_clients.pop(cname, None)
            return "ERROR:CLIENT_DISCONNECTED"

        reply_txt = reply.decode(errors="ignore").strip()

        # Log actions (not logging 'state' to reduce noise)
        if command.startswith("turn") or command.startswith("toggle"):
            log_event(f"{cname.upper()}: Executed '{command}', result={reply_txt}")

        return reply_txt

    except Exception as e:
        with connected_lock:
            connected_clients.pop(cname, None)
        return f"ERROR:COMM_FAIL({e})"

# ========================================
# MERGED LIST + STATUS COLORS
# ========================================
def extract_first_int(text):
    m = re.search(r"\d+", text)
    return m.group(0) if m else None

def get_status_color(state_str):
    try:
        state = int(state_str)
        if state == 1:
            return "<span style='color:green;font-weight:bold'>ON</span>"
        elif state == 0:
            return "<span style='color:red;font-weight:bold'>OFF</span>"
    except:
        pass
    return "<span style='color:gray'>?</span>"

def merge_lists(server_node):
    out = []

    # SERVER devices
    local = list_local_devices()
    if local.strip():
        for line in local.splitlines():
            if line.strip():
                out.append(f"{server_node}:{line}")
    elif local.startswith("ERROR:"):
        out.append(f"{server_node}:{local}")

    # CLIENT devices
    with connected_lock:
        items = list(connected_clients.items())

    for cname, (sock, addr) in items:
        try:
            sock.sendall(b"list")
            txt = sock.recv(65536).decode(errors="ignore").strip()
            if txt:
                for line in txt.splitlines():
                    if line.strip():
                        out.append(f"{cname}:{line}")
        except Exception as e:
            # Drop dead client and show error row
            with connected_lock:
                connected_clients.pop(cname, None)
            out.append(f"{cname}: ERROR({e})")

    return "\n".join(out)

def execute_cmd(cmd, node, devID):
    node_l = node.lower()
    if node_l == server_name:
        return local_execute(cmd, int(devID))
    return forward_to_client(node_l, f"{cmd} {devID}")

def merged_devices_with_states():
    result = []
    merged = merge_lists(server_name)
    for line in merged.splitlines():
        if ":" not in line:
            continue
        node, rest = line.split(":", 1)
        node = node.strip()
        rest = rest.strip()
        devID = extract_first_int(rest)
        if devID is None:
            continue
        # Get state for color
        state = execute_cmd("state", node, devID)
        color = get_status_color(state)
        result.append((node, rest, devID, color))
    return result

# ========================================
# TCP AGENT ACCEPT LOOP
# ========================================
def accept_loop(server_sock):
    while True:
        conn, addr = server_sock.accept()
        try:
            hello = conn.recv(1024).decode(errors="ignore").strip()
        except Exception:
            conn.close()
            continue

        if not hello.startswith("HELLO "):
            conn.close()
            continue

        cname = hello.split(maxsplit=1)[1].strip().lower()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        with connected_lock:
            if cname in connected_clients:
                try:
                    connected_clients[cname][0].close()
                except:
                    pass
            connected_clients[cname] = (conn, addr)

        log_event(f"Client connected: {cname} at {addr[0]}:{addr[1]}")

# ========================================
# WEB UI
# ========================================
class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = (self.path or "/").split("?", 1)[0]

        if path == "/":
            self.send_html(ui_main_page()); return

        if path == "/listall":
            txt = merge_lists(server_name)
            self.send_text(txt); return

        if path == "/log":
            self.send_html(ui_event_log_page()); return

        # Actions: /toggle/<node>/<dev>, /turnon/<node>/<dev>, /turnoff/<node>/<dev>
        m = re.match(r"^/(toggle|turnon|turnoff)/([^/]+)/([^/]+)$", path)
        if m:
            action = m.group(1)
            node = unquote(m.group(2))
            dev = unquote(m.group(3))

            result = execute_cmd(action, node, dev)
            log_event(f"WEB: {action} {node}:{dev} → {result}")

            # After executing the action, redirect back to the dashboard
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        self.send_error(404, "Not Found")

    # ---- Helpers ----
    def send_text(self, txt):
        data = (txt or "").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, html):
        data = (html or "").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

# ========================================
# UI HTML PAGES (auto-refresh, status colors, log panel)
# ========================================
def ui_main_page():
    devices = merged_devices_with_states()

    html = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Cleware Dashboard</title>
<meta http-equiv="refresh" content="20">
<style>
body { font-family: Segoe UI, Arial, sans-serif; margin: 0; padding: 0; display: flex; }
.container { display: flex; width: 100%; }
.main { flex: 1; padding: 16px; }
h2 { margin: 16px 0; }
table { border-collapse: collapse; width: 100%; max-width: 1100px; }
th, td { padding: 8px; border: 1px solid #ddd; }
th { background: #f5f5f5; }
tr:nth-child(even) { background: #fafafa; }
.actions a.btn {
    display: inline-block; padding: 4px 8px; margin-right: 6px;
    border: 1px solid #aaa; border-radius: 4px;
    background: #eee; color: #000; text-decoration: none;
}
.actions a.btn:hover { background: #ddd; }
#logbox {
    width: 36%; max-width: 520px; height: 100vh; overflow-y: auto;
    border-left: 3px solid #aaa; padding: 16px; background: #fafafa;
}
.topbar a { margin-right: 12px; text-decoration: none; }
.topbar a:hover { text-decoration: underline; }
.mono { font-family: Consolas, Monaco, monospace; }
</style>
</head>
<body>
<div class="container">
  <div class="main">
    <div class="topbar">
      <h2>Cleware Network Control</h2>
      <div>
        <span class="mono"><b>Server:</b> """ + server_name + """</span>&nbsp;&nbsp;
        <a href="/">Refresh</a>
        <a href="/listall">Raw list</a>
        <a href="/log">Open log page</a>
      </div>
    </div>
    <table>
      <tr><th>Node</th><th>Device</th><th>Status</th><th>Actions</th></tr>
"""
    for node, desc, devID, color in devices:
        html += (
            "<tr>"
            f"<td>{node}</td>"
            f"<td>{desc}</td>"
            f"<td>{color}</td>"
            "<td class='actions'>"
            f"<a class='btn' href=\"/toggle/{node}/{devID}\">Toggle</a>"
            f"<a class='btn' href=\"/turnon/{node}/{devID}\">On</a>"
            f"<a class='btn' href=\"/turnoff/{node}/{devID}\">Off</a>"
            "</td>"
            "</tr>"
        )

    html += """
    </table>
  </div>

  <div id="logbox">
    <h3>Event Log</h3>
    <pre class="mono">
"""
    with event_log_lock:
        for entry in event_log[-200:]:
            html += entry + "\n"

    html += """
    </pre>
  </div>
</div>
</body>
</html>
"""
    return html

def ui_event_log_page():
    html = "<html><head><meta charset='utf-8'><title>Event Log</title></head><body><h1>Event Log</h1><pre>"
    with event_log_lock:
        for entry in event_log:
            html += entry + "\n"
    html += "</pre></body></html>"
    return html

# ========================================
# CONSOLE HELP
# ========================================
def send_help() -> str:
    return (
        "Available Commands:\n"
        "  help                     : show this help\n"
        "  clients                  : list connected clients\n"
        "  listall                  : list devices from server + all clients\n"
        "  state  <node:devID>      : get device state\n"
        "  turnon <node:devID>      : turn ON device\n"
        "  turnoff <node:devID>     : turn OFF device\n"
        "  toggle <node:devID>      : toggle device\n"
        "  exit                     : quit server\n"
    )

# ========================================
# START WEB SERVER
# ========================================
def start_web_server():
    httpd = HTTPServer(("0.0.0.0", WEB_PORT), WebHandler)
    print(f"[WEB] UI available on http://<SERVER_IP>:{WEB_PORT}")
    httpd.serve_forever()

# ========================================
# MAIN
# ========================================
def main():
    global server_name
    server_name = socket.gethostname().lower()

    tHost, iPort, _ = cwUSB_getConfig()
    print(f"[SERVER] Agent host configured as {tHost}:{iPort} (server name: {server_name})")

    # TCP server for USB agents
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((tHost, iPort))
    except OSError as e:
        if getattr(e, "winerror", None) == 10049:
            print(f"[SERVER] Configured host '{tHost}' is not local; binding to 0.0.0.0:{iPort}")
            s.bind(("", iPort))
        else:
            print(f"[SERVER] Bind failed on {tHost}:{iPort}, falling back to 0.0.0.0:{iPort} ({e})")
            s.bind(("", iPort))

    s.listen(50)
    ip, prt = s.getsockname()[0], s.getsockname()[1]
    print(f"[SERVER] Agent TCP listening on {ip}:{prt}")

    # Start TCP accept loop for USB agents
    threading.Thread(target=accept_loop, args=(s,), daemon=True).start()
    # Start Web UI
    threading.Thread(target=start_web_server, daemon=True).start()

    print(f"[INFO] Web UI on http://<SERVER_IP>:{WEB_PORT}")
    print(f"[INFO] Type 'help' for console commands.\n")

    # Console loop
    while True:
        try:
            cmd_line = input("server> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not cmd_line:
            continue

        low = cmd_line.lower()
        if low == "help":
            print(send_help()); continue

        if low == "clients":
            with connected_lock:
                if connected_clients:
                    for name, (sock, addr) in connected_clients.items():
                        print(f"{name}  ({addr[0]}:{addr[1]})")
                else:
                    print("(no clients connected)")
            continue

        if low == "listall":
            print(merge_lists(server_name)); continue

        if any(low.startswith(pfx) for pfx in ("state ", "turnon ", "turnoff ", "toggle ")):
            parts = cmd_line.split()
            if len(parts) != 2 or ":" not in parts[1]:
                print("Usage: state <node:devID>  (example: state pc1:0)")
                continue

            node, dev = parts[1].split(":", 1)
            try:
                dev_int = int(dev)
            except Exception:
                print("ERROR: devID must be an integer")
                continue

            verb = parts[0].lower()
            if node.lower() == server_name:
                print(local_execute(verb, dev_int))
            else:
                print(forward_to_client(node, f"{verb} {dev_int}"))
            continue

        if low == "exit":
            break

        print("Unknown command. Type 'help'.")

    # Cleanup
    with connected_lock:
        for sock, _ in connected_clients.values():
            try:
                sock.close()
            except Exception:
                pass
        connected_clients.clear()

if __name__ == "__main__":
    main()