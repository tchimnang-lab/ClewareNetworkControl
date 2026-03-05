# Cleware USB Switch Client
# to be executed on the client, that wants to control the Cleware USB Switches
# on the POWER_MANAGEMENT computer (currently EH39M31C)
# called without parameter we go into interactive mode
# all commands listed there can be passed to the Client console application.
# the command will be executed immediately and the application terminates
#
# Configuration can be done in an unnamed section in
# ClewareUSB.ini:
#   host = EH39M31C.ad005.onehc.net
#   port = 59001
#   dll  = USBaccessX64.dll
#
# executable was generated with 
# pyinstaller --noconfirm --onefile --console --icon "E:\PythonProjects\ClewareUSBClientServer\ClewareUSBClient.ico"  --name "ClewareUSBClient" "E:\PythonProjects\ClewareUSBClientServer\ClewareUSBClient.py"

import socket
import sys
import getopt
from ClewareUSBLib import cwUSB_getConfig

def main():

    iNoOfArg = len(sys.argv) - 1

    [tHost, iPort, tDll] = cwUSB_getConfig()

    print ("Connecting to " + tHost + ":" + str(iPort))
    try:
        # Create a socket object
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # Connect to the server
            s.connect((tHost, iPort))
            print(f"Connected to {tHost}:{iPort}")

            if 0 < iNoOfArg:
                command = ""
                for i in range(1, iNoOfArg+1):
                    command += sys.argv[i] + " "
            else:
                command = "help" # interactive mode. get list of commands first

            while True:
                command = command.strip()
                if command.lower() == "exit":
                    break
                if len(command) > 0: 
                    # Send the command to the server
                    s.sendall(command.encode())

                    # Receive response from the server
                    data = s.recv(10240)
                    print(f"Server response:\n{data.decode()}")

                if 0 < iNoOfArg:
                    break
                else: # interactive mode
                    command = input("Enter command (or 'exit' to quit): ")

    except Exception as e:
        print(f"Error connecting to server: {e}")

if __name__ == "__main__":
    main()