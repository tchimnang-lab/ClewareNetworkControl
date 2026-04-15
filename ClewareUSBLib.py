# Configuration can be done in an unnamed section in
# ClewareUSB.ini:
#   host = 127.0.0.1
#   port = 59001
#   dll  = USBaccessX64.dll

from ctypes import *
import time
import winreg
import sys
import configparser

REG_PATH = r"SOFTWARE\WOW6432Node\Cleware GmbH\USB"

cwbInitiqalized = False

# load config from ini-file or provide default
def cwUSB_getConfig():
    config = configparser.ConfigParser(allow_unnamed_section=True)
    config.read('ClewareUSB.ini')
    tHost = '127.0.0.1'
    iPort = 59001
    tDll  = r"USBaccessX64.dll"
    try:
        NetConfig = config[configparser.UNNAMED_SECTION]
        tHost = NetConfig.get('host', tHost)
        iPort = NetConfig.getint('port', iPort)
        tDll  = NetConfig.get('dll', tDll)
    except Exception:
        pass
    return [tHost, iPort, tDll]
    
# must be called before first usage of cwUSB functions
# done implicetly inside those functions
def cwUSB_setup():
    global cwUSB
    global cwObj
    global cwbInitiqalized
    global cwiNoOfDevices

    [tHost, iPort, tDll] = cwUSB_getConfig()
    try:
        cwUSB          = windll.LoadLibrary(tDll)
        cwObj          = cwUSB.FCWInitObject()
        cwObj          = 0 # unclear, why it only works with 0
        cwiNoOfDevices = cwUSB.FCWOpenCleware(cwObj)
    except Exception as e:
        print(f"Error handling cwUSB_setup: {e}") 
    cwbInitiqalized = True

# cleanup your mess. actually not necessary, since this is done on proceess termination
def cwUSB_cleanup():
    if cwbInitiqalized == False: return
    if cwiNoOfDevices > 0: cwUSB.FCWCloseCleware(cwObj)
    cwUSB.FCWUnInitObject(cwObj)

# read friendly name of device from registry
# can set done via ClewareControl, but also via cwUSB_set_NametoNum()
def cwUSB_get_NameFromNum(iDevNum):
    if cwbInitiqalized == False: cwUSB_setup()
    iVersion = cwUSB.FCWGetVersion(cwObj, iDevNum)
    iSerial  = cwUSB.FCWGetSerialNumber(cwObj, iDevNum)
    tDevID = "08-" + ("%x" % iVersion) + "-" + ("%08x" % iSerial) + "-devID"
    try:
        hKey   = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REG_PATH, 0, winreg.KEY_READ)
        [tName, iType] = winreg.QueryValueEx(hKey, tDevID)
        winreg.CloseKey(hKey)
        return tName
    except WindowsError:
        return "unknown"
# write friendly name of device to registry
def cwUSB_set_NametoNum(iDevNum, tName):
    if cwbInitiqalized == False: cwUSB_setup()
    iVersion = cwUSB.FCWGetVersion(cwObj, iDevNum)
    iSerial  = cwUSB.FCWGetSerialNumber(cwObj, iDevNum)
    tDevID = "08-" + ("%x" % iVersion) + "-" + ("%08x" % iSerial) + "-devID"
    try:
        hKey   = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REG_PATH, 0, winreg.KEY_WRITE)
        winreg.SetValueEx(hKey, tDevID, 0, winreg.REG_SZ, tName)
        if hKey: 
            winreg.CloseKey(hKey)
        return tName
    except WindowsError:
        return "failed to set name"

#get the SerialNumber for an index of  the consecutive numbered cleware devices
def cwUSB_get_SerialFromNum(iDevNum):
    if cwbInitiqalized == False: cwUSB_setup()
    iType = cwUSB.FCWGetUSBType(cwObj, iDevNum)
    if 0x08 != iType: return -1
    return cwUSB.FCWGetSerialNumber(cwObj, iDevNum)

#readout the current state of the usb switch: 0 = OFF, 1 = ON
def cwUSB_get_StateFromNum(iDevNum):
    if cwbInitiqalized == False: cwUSB_setup()
    return cwUSB.FCWGetContact(cwObj, iDevNum) & 1

# sets a new state of an USBSwitch: 0 = OFF, 1 = ON
def cwUSB_set_StateToNum(iDevNum, iState):
    if cwbInitiqalized == False: cwUSB_setup()
    iChannel = 0x10    # CUSBaccess::::SWITCH_0
    cwUSB.FCWSetSwitch(cwObj, iDevNum, iChannel, iState & 1)
    time.sleep(1)

# search a specified SerialNumber in the list of all USBSwitches and return its index
def cwUSB_get_DevNumFromSerial(iSerial):
    if cwbInitiqalized == False: cwUSB_setup()
    iDevNum=0
    while iDevNum < cwiNoOfDevices :
        iType = cwUSB.FCWGetUSBType(cwObj, iDevNum);
        if  0x08  == iType:
            iFound = cwUSB.FCWGetSerialNumber(cwObj, iDevNum)
            if iSerial == iFound: 
                return iDevNum
        iDevNum= iDevNum + 1
    return -1

# just convert 0 into ON and 1 into OFF for more readable output
def cwUSB_get_StateStr(iState):
    if 1 == iState: return "ON "
    else:           return "OFF"

# generate a list of all available USBSwitches
def cwUSB_list_Devices():
    if cwbInitiqalized == False: cwUSB_setup()
    iDevNum=0
    tRet = ""
    while iDevNum < cwiNoOfDevices:
        iSerial = cwUSB_get_SerialFromNum(iDevNum)
        if iSerial >= 0:
            tName  = cwUSB_get_NameFromNum (iDevNum)
            iState = cwUSB_get_StateFromNum(iDevNum)
            tRet += "serial number=" + ("%7d" % iSerial) + " state=" + cwUSB_get_StateStr(iState) + " Name="+ tName + "\n"
        iDevNum = iDevNum + 1
    return tRet

#Program and device recovery
def cwUSB_Recover():
    global cwbInitiqalized, cwObj, cwiNoOfDevices

    try:
        cwUSB.FCWCloseCleware(cwObj)
    except:
        pass

    time.sleep(1)

    cwObj= cwUSB.FCWInitObject()
    cwiNoOfDevices = cwUSB.FCWOpenCleware(cwObj)
    return cwiNoOfDevices

def cwUSB_RecoverDevice(iDevNum):
    global cwbInitiqalized, cwObj, cwiNoOfDevices

    if cwbInitiqalized == False: cwUSB_setup()
    iSerial = cwUSB.FCWGetSerialNumber(cwObj, iDevNum)
    if iSerial < 0: return False

    cwUSB.FCWCloseCleware(cwObj)
    time.sleep(1)

    cwObj = cwUSB.FCWInitObject()
    cwiNoOfDevices = cwUSB.FCWOpenCleware(cwObj)
    iDevNum = cwUSB_get_DevNumFromSerial(iSerial)

# Get the USB type for a device
def cwUSB_get_USBType(iDevNum):
    if not cwbInitiqalized:
        cwUSB_setup()
    return cwUSB.FCWGetUSBType(cwObj, iDevNum)

# Calm a watchdog device
def cwUSB_CalmWatchdog(iDevNum, t1, t2):
    if not cwbInitiqalized:
        cwUSB_setup()
    cwUSB.FCWCalmWatchdog(cwObj, iDevNum, t1, t2)

def cwUSB_ResetDevice(iDevNum):
    if not cwbInitiqalized:
        cwUSB_setup()
    cwUSB.FCWResetDevice(cwObj, iDevNum)