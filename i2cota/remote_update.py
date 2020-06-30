#!/usr/bin/env python
# coding=utf-8
# Filename : hgx2_fpga_update.py, working on python 2.7 win64, NVIDIA
# For hGX2 Firmware update
# Revision :
# [Kevin] 0.1v 2020/2/16 First release

from __future__ import division, with_statement, print_function
import socket
import binascii
import time
import datetime
import os
import sys
import argparse
import sys

from smbus2 import SMBus, i2c_msg

I2C_BITRATE = 400  # kHz
DEV_ADDR = 0x55
#####################################################
#File_Path = "C:/Python27/ota_fpga.bin"
#File_Path = "ota_fpga.bin"
#####################################################



def calc_checksum(data_list):
    return sum(data_list)%(0x100)

def i2c_read_transaction(handle,reg,data_len):
    reg_msb = (reg & 0xff00) >> 8
    reg_lsb = (reg & 0xff)
    header = [reg_msb, reg_lsb]

    #print(data_out)
    try:
        with SMBus(handle) as bus:
            read = i2c_msg.read(DEV_ADDR,data_len)
            bus.i2c_rdwr(i2c_msg.write(DEV_ADDR,header),read)
    except IOError:
        print("[IOError]read again")
        retry = 1
        while retry:
            try:
                with SMBus(handle) as bus:
                    read = i2c_msg.read(DEV_ADDR,data_len)
                    bus.i2c_rdwr(i2c_msg.write(DEV_ADDR,header),read)
                    break
            except IOError:
                print("retry ",(2-retry))
                time.sleep(5)
                retry -=1
    data_in = list(read)
    if data_in[0] != calc_checksum(data_in[1:]):
        print("readback data doesn't meet the checksum value")
    return data_in

def i2c_write_transaction(handle,reg,data_list):
    # return checksum for check
    reg_msb = (reg & 0xff00) >> 8
    reg_lsb = (reg & 0xff)
    checksum = calc_checksum(data_list)
    data_out = [reg_msb, reg_lsb,checksum]
    data_out.extend(data_list)
    with SMBus(handle) as bus:
        write = i2c_msg.write(DEV_ADDR,data_out)
        bus.i2c_rdwr(write)
    return checksum

def check_cmd_readback_status(handle):
    # command status regisiter 0x4
    # return value is defined as below 
    # byte0: checksum
    # byte1: command
    # byte2: command status    
    data_checksum,checksum,command,cmd_sts = i2c_read_transaction(handle,0x4,4)
    # if command != 0x1:
    #     if data_checksum != checksum:
    #         sys.exit(-1)
    return checksum,command,cmd_sts

def check_fw_update_status(handle):
    # fw update status regisiter 0x5
    # return value is defined as below 
    # byte0: counter
    # byte1: status code
    while True:
        time.sleep(5)
        data_checksum,counter,status_code = i2c_read_transaction(handle,0x5,3)
        print("data_checksum:",data_checksum)
        print("status_code:",status_code)
        print("counter:",counter)
        if status_code == 0xa:
            print("The firmware update finished")
            break
        elif status_code == 0xb:
            print("Firmware update in progress, counter is in byte 1")
        elif status_code == 0xc:
            print("Firmware header is not valid")
            sys.exit(-1)
        elif status_code == 0xd:
            print("The firmware ID of the image doesn’t match the firmware ID specified in START_FW_UPDATE command")
            sys.exit(-1)
        elif status_code == 0x16:
            print("Firmware update in init status")
        else:
            print("Firmware update failed, the error code that should be reported to NVIDIA")
            sys.exit(-1)


def cmd_upload_block(handle,block_data,delay):
    # write port regisiter 0x3
    # cmd is 0x1
    # payload is blockidx, block_data
    header_major_ver = 1
    header_minor_ver = 0
    cmd = 0x1
    payload_len = len(block_data)
    block_len3 = (payload_len & 0xff000000) >> 24
    block_len2 = (payload_len & 0xff0000) >> 16
    block_len1 = (payload_len & 0xff00) >> 8
    block_len0 = (payload_len & 0xff) >> 0
    data_in = [header_major_ver,header_minor_ver,cmd,0,block_len3,block_len2,block_len1,block_len0]
    data_in.extend(block_data)
    w_checksum =  i2c_write_transaction(handle,0x3,data_in)
    if delay:
        time.sleep(0.1)
    # readback the command status for intergraty check
    checksum,command,cmd_sts = check_cmd_readback_status(handle)
    #print("command status=", hex(cmd_sts))
    
    while (cmd_sts == 4):
            print("send UPLOAD_BLOCK command is busy! wait 10 seconds and requery")
            time.sleep(10)
            checksum,command,cmd_sts = check_cmd_readback_status(handle)
    if (command != cmd) or (w_checksum != checksum):
        print("send UPLOAD_BLOCK command failed!")
        return False
    else:
        print("send UPLOAD_BLOCK command success!")
    #check if ERR_I2C_CHECKSUM (1)
    if cmd_sts == 1:
        print("Command checksum wrong, command should be re-sent")
        return False
    # check if CMD_ERR_CMD_LENGTH_MISMATCH (2)
    if cmd_sts == 2:
        print("Command header/payload length doesn't match the length being sent")
        return False
    # check if CMD_ERR_CMD_VERSION_SUPPORTED  (3)
    if cmd_sts == 3:
        print("The version of the command header is not supported")
        return False
    # check if any backend task is running 
    #if (cmd_sts == 4) and (last_page == 0):
    #    print("there're tasks runnig, wait for another time to try")
    #    return False
    # check if ERR_FLASH_ERROR   (5)
    if cmd_sts == 5:
        print("ERR_FLASH_ERROR")
        return False
    return True

def cmd_upload_divide_fw_image(handle,File_Path):
    oldtime=datetime.datetime.now()
    print("[cmd_upload_divide_fw_image]Current Time :",oldtime)
    #Step 2: Divide Firmware Image Data and Program to Flash
    page_size = 128
    sector_size = 4096
    sector_num = 0
    page_num = 0

    total_page = (os.path.getsize(File_Path)+page_size-1)//page_size
    # print("total_page:",total_page)
    # sys.exit(0)
    total_sector = (total_page + page_size - 1)//(sector_size//page_size)
    delay_thred = (sector_size//page_size) - 1
    delay = False
    with open(File_Path,'rb') as f:
        while True:        
            data = f.read(page_size)
            if page_num >= total_page:
                break       
            block_data = bytearray(data)
            if (page_num%(delay_thred+1) == delay_thred) or (page_num+1) == total_page:
                # print(page_num)
                delay = True
                # sys.exit(0)
            else:
                delay = False
            while not cmd_upload_block(handle,block_data,delay):
                print("upload failed this time for blockidx", page_num)
                sys.exit(-1)
            page_num +=1
    newtime =datetime.datetime.now()
    print("Total UPLOAD_BLOCK Time cost is :",newtime-oldtime)

def cmd_start_fw_update(handle,fw_type,payload_len):
    # write port regisiter 0x3
    # cmd is 0
    # payload is fw_type
    header_major_ver = 1
    header_minor_ver = 0
    cmd = 0
    fw_size3 = (payload_len & 0xff000000) >> 24
    fw_size2 = (payload_len & 0xff0000) >> 16
    fw_size1 = (payload_len & 0xff00) >> 8
    fw_size0 = (payload_len & 0xff) >> 0
    data_in = [header_major_ver,header_minor_ver,cmd,0,0,0,0,6,fw_type,0,fw_size3,fw_size2,fw_size1,fw_size0]
    # readback the command status for intergraty check    
    checksum,command,cmd_sts = check_cmd_readback_status(handle)
    # check if any backend task is running 
    if cmd_sts == 4:
        print("there're tasks runnig, wait for another time to try")
        sys.exit(-1)
    w_checksum =  i2c_write_transaction(handle,0x3,data_in)
    time.sleep(1)  
    # readback the command status for intergraty check
    checksum,command,cmd_sts = check_cmd_readback_status(handle)

    if (cmd_sts) or (command != cmd) or (w_checksum != checksum):
        print("send START_FW_UPDATE command failed!")
        sys.exit(-1)
    else:
        print("send START_FW_UPDATE command success!")


def read_platform_ID(handle):
    # command status regisiter 0x0
    # return value is defined as below 
    # byte0: 0x46
    # byte1: 0x12
    data_checksum,platform_msb,platform_lsb= i2c_read_transaction(handle,0x0,3)
    print("data_checksum:",hex(data_checksum))
    print("platform_msb:",hex(platform_msb))
    print("platform_lsb:",hex(platform_lsb))
    platform_ID = (platform_msb << 8) + platform_lsb
    print("Platform ID:", hex(platform_ID))
    if platform_ID != 0x4612:
        print("platform_ID mismatch the GPU baseboard")
        sys.exit(-1)


def delta_remote_update(bus_num,file_path,fw_type):
    # Open the device
    # handle = SMBus(bus_num)
    handle = bus_num
    read_platform_ID(handle)
    start_time = datetime.datetime.now()
    #Step 1: send START_FW_UPDATE command with firmware type ID(with readback command status register)
    print("image size:",os.path.getsize(file_path))
    cmd_start_fw_update(handle,fw_type,os.path.getsize(file_path))
    #Step 2:  Divide FW image into blocks then send UPLOAD_BLOCK command for each block until the end
    cmd_upload_divide_fw_image(handle,file_path)
    current_time = datetime.datetime.now()
    #Step 3: check firmware update status register until FW update finish
    check_fw_update_status(handle)
    end_time =datetime.datetime.now()
    print("Total UPLOAD Time cost is :",current_time-start_time)
    print("Total Auth and update Time cost is :",end_time-current_time)
    print("Total Time cost is :",end_time-start_time)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RemoteAgent for hGX2 Firmware update",
                                     usage="\nFor example:\
                                            \nhgx2_fpga_update.exe -b 0 -f ota_fpga.bin -t 0 ")
    parser.add_argument("-b", help="I2C bus num", type=int, required=True)
    parser.add_argument("-f", help="Firmware path", type=str,required=True)
    parser.add_argument("-t", help="Firmware type(FPGA:0/RTU:1)", type=int,required=True)
    args = parser.parse_args()
    if (args.b is None) or (args.f is None):
        print("Error:Please input port and file_path. For example: hgx2_fpga_update.exe -b 1 -f ota_fpga.bin -t 0")
        sys.exit(0)
    delta_remote_update(args.b,args.f,args.t)








