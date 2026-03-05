#Cleware test script
# test if USB-Contact was pressed

import socket
import threading
from ctypes import *
import time
import sys, os
from ClewareUSBLib import *
import sys
from urllib import response
import winreg

def send_help():
    tResponse  = "Available Commands:\n\n"
    tResponse += "list                       : lists all available USBSwitches (Devices)\n"
    tResponse += "state   <devID>            : shows the current state of the specified USBSwitch\n"
    tResponse += "turnon  <devID>            : turn ON  the specified USBSwitch \n"
    tResponse += "turnoff <devID>            : turn OFF the specified USBSwitch \n"
    tResponse += "toggle  <devID>            : toggle state of the specified USBSwitch \n"
    tResponse += "rename  <devID> <new name> : renames the specified USBSwitch in List (no quotes required)\n"
    tResponse += "exit                       : exits the application\n"
    tResponse += "\n"
    return tResponse

def handle_client(conn, tLocalCommand):
    bLocal = len(tLocalCommand) > 0
    quit= False
    while True:
        try:
            if bLocal == False:
                bEcho = False
                is_http = False
                # Receive data from the client
                data = conn.recv(4096)
                if not data:
                    break  # If no data, connection closed
                command = data.decode(errors='ignore').strip()
                # detect simple HTTP request (browser)
                first_line = command.splitlines()[0] if command else ''
                if first_line.startswith('GET ') or 'HTTP/' in first_line:
                    is_http = True
                    parts = first_line.split()
                    path = parts[1] if len(parts) > 1 else '/'
                    path = path.split('?', 1)[0]
                    if path.startswith('/'):
                        path = path[1:]
                    # map URL path to command tokens: /list -> "list", /state/123 -> "state 123"
                    command = path.replace('/', ' ').strip()
                    if command == '':
                        command = 'help'
                print(f"Received command: {command}")
            else:
                command = tLocalCommand
                bEcho   = True
                is_http = False
    
            response = ""  # Initialize response to avoid UnboundLocalError

            # Use a configurable or relative DLL path for better portability
            dll_path = os.environ.get("CLEWARE_DLL_PATH", os.path.join(os.path.dirname(__file__), "Source", "USBaccessX64.dll"))
            mydll = windll.LoadLibrary(dll_path)

            cw = mydll.FCWInitObject()
            devCnt = mydll.FCWOpenCleware(0)
            if devCnt == 0:
                print("No devices found.")
                return
            else: 
                print("Device count = ", devCnt)
                print(cwUSB_list_Devices() + "\n")
                if bLocal:
                    print("What do you want to do?")
                    command = input()
                    if command == '':
                        command = 'help'
                        print(f"Received command: {command}\n" + send_help())
                else:
                    if command == '':
                        command = 'help'
                        print(f"Received command: {command}\n" + send_help())
                parts = command.split()
                cmd = parts[0].lower()

                if cmd == 'help':
                    #print(send_help())
                    response = send_help()
                elif cmd == 'list':
                    response = cwUSB_list_Devices() + "\n"
                elif cmd in ('state', 'turnon', 'turnoff', 'toggle', 'rename'):
                    if len(parts) < 2:
                        response = f"Usage: {cmd} <devID> {'<new name>' if cmd=='rename' else ''}"
                    else:
                        try:
                            devID = int(parts[1])
                        except ValueError:
                            print("devID must be an integer")
                        else:
                            if cmd == 'state':
                                #print(f"Current state of device {devID}: {cwUSB_get_StateFromNum(devID)}")
                                response = f"Current state of device {devID}: {cwUSB_get_StateFromNum(devID)}"
                            elif cmd == 'turnon':
                                cwUSB_set_StateToNum(devID, 1)
                                iState = cwUSB_get_StateFromNum(devID)
                                print(f"Turned ON device {devID} - new state: {cwUSB_get_StateStr(iState)}")
                                response = f"Turned ON device {devID} - new state: {cwUSB_get_StateStr(iState)}"
                            elif cmd == 'turnoff':
                                cwUSB_set_StateToNum(devID, 0)
                                iState = cwUSB_get_StateFromNum(devID)
                                print(f"Turned OFF device {devID} - new state: {cwUSB_get_StateStr(iState)}")
                                response = f"Turned OFF device {devID} - new state: {cwUSB_get_StateStr(iState)}"
                            elif cmd == 'toggle':
                                try:
                                    cur = cwUSB_get_StateFromNum(devID)
                                    cwUSB_set_StateToNum(devID, 0 if cur else 1)
                                    print(f"Toggled device {devID} - new state: {cwUSB_get_StateStr(cwUSB_get_StateFromNum(devID))}")
                                    response = f"Toggled device {devID} - new state: {cwUSB_get_StateStr(cwUSB_get_StateFromNum(devID))}"
                                except Exception as e:
                                    print("Toggle failed:", e)
                                    response = "Toggle failed"
                            elif cmd == 'rename':
                                if len(parts) < 3:
                                    response = "Usage: rename <devID> <new name>"
                                else:
                                    newName = " ".join(parts[2:])
                                    # if the DLL expects bytes, encode: mydll.FCWRenameDevice(devID, newName.encode('utf-8'))
                                    try:
                                        #mydll.FCWRenameDevice(devID, newName)
                                        cwUSB_set_NametoNum(devID, newName)
                                        print(f"Renamed device {devID} to {newName}")
                                        response = f"Renamed device {devID} to {newName}"
                                    except Exception as e:
                                        print("Rename failed:", e)
                                        response = "Rename failed"
                elif cmd == 'exit':
                    quit = True
                    #break
                            
                else:
                    print(f"Unknown command: {command}")
                    response = "Unknown command"



             # Send response back to client
            if len(response) == 0:
                response = "no response"
            if bEcho:
                print("Response:")
                print(response)
            if bLocal == False:
                if is_http:
                    body = response + "\n"
                    http = "HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\n\r\n{}".format(len(body.encode('utf-8')), body)
                    conn.sendall(http.encode('utf-8'))
                else:
                    conn.sendall(response.encode('utf-8'))
            else:
                break  # exit loop

        except Exception as e:
            print(f"Error handling client: {e}")
            break       

    if bLocal == False:
        conn.close() 



def main():
    
    iNoOfArg = len(sys.argv) - 1
    bLocal = iNoOfArg > 0
    
    if bLocal == False:
        print("Startup:")
        [tHost, iPort, tDll] = cwUSB_getConfig()
        print(f"Try to start Server on {tHost}:{iPort}")

        try:
            # Create a socket object
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                # Bind the socket to the address and port
                try:
                    s.bind((tHost, iPort))
                except OSError as e:
                    # WinError 10049: The requested address is not valid in its context
                    if getattr(e, "winerror", None) == 10049:
                        print(f"Configured host '{tHost}' is not local; binding to all interfaces (0.0.0.0:{iPort}) instead")
                        s.bind(("", iPort))  # bind to all interfaces
                    else:
                        raise

                # Listen for incoming connections (max 5 in queue)
                s.listen(5)
                print(f"Server started on {tHost}:{iPort}")

                while True:
                    # Accept a connection
                    conn, IPAddr = s.accept()
                    try:
                        [tClientName, aliasList, ipAdressList] = socket.gethostbyaddr(IPAddr[0])
                    except Exception:
                        tClientName = IPAddr[0]

                    print(f"{tClientName} connected    ({IPAddr[0]}:{IPAddr[1]})")

                    # Handle the client in a separate thread so we can accept more clients
                    threading.Thread(target=handle_client, args=(conn, ""), daemon=True).start()
                    # connection will be closed by the handler; do not block here

        except Exception as e:
            print(f"Error starting server: {e}")
    else:
        if 0 < iNoOfArg:
            command = ""
        for i in range(1, iNoOfArg+1):
            command += sys.argv[i] + " "
        handle_client(None, command)

if __name__ == "__main__":
    main()

