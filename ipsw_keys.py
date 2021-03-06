#!/usr/bin/env python
from sys import argv, stdout
from os import system, remove, path
from urlparse import urlparse
import re
import dfu
import ssl
import math
import json
import getopt
import image3
import dfuexec
import httplib
import usbexec
import zipfile
import plistlib

####################### BEGIN ASN1 DECODER ############################

# Author: Jens Getreu, 8.11.2014

##### NAVIGATE

# The following 4 functions are all you need to parse an ASN1 structure

# gets the first ASN1 structure in der
def asn1_node_root(der):
	return asn1_read_length(der,0)

# gets the next ASN1 structure following (ixs,ixf,ixl)
def asn1_node_next(der, (ixs,ixf,ixl)):
	return asn1_read_length(der,ixl+1)

# opens the container (ixs,ixf,ixl) and returns the first ASN1 inside
def asn1_node_first_child(der, (ixs,ixf,ixl)):
	if ord(der[ixs]) & 0x20 != 0x20:
		raise ValueError('Error: can only open constructed types. '
				+'Found type: 0x'+der[ixs].encode("hex"))
	return asn1_read_length(der,ixf)

# is true if one ASN1 chunk is inside another chunk.
def asn1_node_is_child_of((ixs,ixf,ixl), (jxs,jxf,jxl)):
	return ( (ixf <= jxs ) and (jxl <= ixl) ) or \
           ( (jxf <= ixs ) and (ixl <= jxl) )

##### END NAVIGATE



##### ACCESS PRIMITIVES

# get content and verify type byte
def asn1_get_value_of_type(der,(ixs,ixf,ixl),asn1_type):
	asn1_type_table = {
	'BOOLEAN':           0x01,	'INTEGER':           0x02,
	'BIT STRING':        0x03,	'OCTET STRING':      0x04,
	'NULL':              0x05,	'OBJECT IDENTIFIER': 0x06,
	'SEQUENCE':          0x70,	'SET':               0x71,
	'PrintableString':   0x13,	'IA5String':         0x16,
	'UTCTime':           0x17,	'ENUMERATED':        0x0A,
	'UTF8String':        0x0C,
	}
	if asn1_type_table[asn1_type] != ord(der[ixs]):
		raise ValueError('Error: Expected type was: '+
			hex(asn1_type_table[asn1_type])+
			' Found: 0x'+der[ixs].encode('hex'))
	return der[ixf:ixl+1]

# get value
def asn1_get_value(der,(ixs,ixf,ixl)):
	return der[ixf:ixl+1]

# get type+length+value
def asn1_get_all(der,(ixs,ixf,ixl)):
	return der[ixs:ixl+1]

##### END ACCESS PRIMITIVES



##### HELPER FUNCTIONS

# converter
def bitstr_to_bytestr(bitstr):
	if bitstr[0] != '\x00':
		raise ValueError('Error: only 00 padded bitstr can be converted to bytestr!')
	return bitstr[1:]

# converter
def bytestr_to_int(s):
	# converts bytestring to integer
	i = 0
	for char in s:
		i <<= 8
		i |= ord(char)
	return i

# ix points to the first byte of the asn1 structure
# Returns first byte pointer, first content byte pointer and last.
def asn1_read_length(der,ix):
	first= ord(der[ix+1])
	if  (ord(der[ix+1]) & 0x80) == 0:
		length = first
		ix_first_content_byte = ix+2
		ix_last_content_byte = ix_first_content_byte + length -1
	else:
		lengthbytes = first & 0x7F
		length = bytestr_to_int(der[ix+2:ix+2+lengthbytes])
		ix_first_content_byte = ix+2+lengthbytes
		ix_last_content_byte = ix_first_content_byte + length -1
	return (ix,ix_first_content_byte,ix_last_content_byte)

##### END HELPER FUNCTIONS


####################### END ASN1 DECODER ############################

serial_number = None
cpid = None
bdid = None
verbose = False

def getInfo():
    global serial_number, cpid, bdid
    dev = dfu.acquire_device()
    serial_number = dev.serial_number
    print("Found:" + serial_number)
    if not "PWND:[" in serial_number:
        print "Please enable pwned DFU Mode first."
        exit(4)
    cpid_m = re.search("CPID:([0-9A-F]{4})", serial_number)
    if cpid_m == None:
        print("Could not find CPID in serial")
        exit(4)
    cpid = cpid_m.group(1)
    if bdid == None:
        bdid_m = re.search("BDID:([0-9A-F][0-9A-F])", serial_number)
        if bdid_m == None:
            print("Could not find BDID in serial")
            exit(4)
        bdid = bdid_m.group(1)
    dfu.release_device(dev)

def getKeybag(der, k):
    if der[:4] == "3gmI":
        if verbose: print("Reading IMG3 for " + k)
        kbag = image3.Image3(der).getKeybag()
        if kbag == None: return (None, None)
        ivenc = kbag[:16]
        keyenc = kbag[16:]
        keys = None
        if 'PWND:[checkm8]' in serial_number:
            pwned = usbexec.PwnedUSBDevice()
            keys = pwned.aes((ivenc + keyenc), usbexec.AES_DECRYPT, usbexec.AES_GID_KEY).encode('hex')
        else:
            device = dfuexec.PwnedDFUDevice()
            keys = device.aes((ivenc + keyenc), dfuexec.AES_DECRYPT, dfuexec.AES_GID_KEY).encode("hex")
        return (keys[:32], keys[32:])
    else:
        if verbose: print("Reading IMG4 for " + k)
        dec = asn1_node_next(der, asn1_node_next(der, asn1_node_next(der, asn1_node_first_child(der, asn1_node_root(der)))))
        if dec[2] >= len(der) - 4:
            return (None, None)
        if verbose: print("Reading kbag")
        kbag = asn1_get_value_of_type(der, asn1_node_next(der, dec), 'OCTET STRING')
        if verbose: print("Extracting kbag")
        dec = asn1_node_next(kbag, asn1_node_first_child(kbag, asn1_node_first_child(kbag, asn1_node_root(kbag))))
        if verbose: print("Reading values from kbag")
        ivenc = asn1_get_value_of_type(kbag, dec, 'OCTET STRING')
        keyenc = asn1_get_value_of_type(kbag, asn1_node_next(kbag, dec), 'OCTET STRING')
        keys = None

        if "SEP" in k:
            return ("KBAG", str(ivenc + keyenc).encode('hex'))
        else:
            if verbose: print("Decrypting key with device GID")
            if 'PWND:[checkm8]' in serial_number:
                pwned = usbexec.PwnedUSBDevice()
                keys = pwned.aes((ivenc + keyenc), usbexec.AES_DECRYPT, usbexec.AES_GID_KEY).encode('hex')
            else:
                device = dfuexec.PwnedDFUDevice()
                keys = device.aes_hex((ivenc + keyenc), dfuexec.AES_DECRYPT, dfuexec.AES_GID_KEY)
            return (keys[:32], keys[32:])

def extractKeys(infile, outfile, outtype=0, delete=False, infodict=None):
    print("Reading manifest...")
    zip = zipfile.ZipFile(infile)
    manifest = plistlib.readPlistFromString(zip.read("BuildManifest.plist"))

    print("Reading keys...")
    output = {}

    try:
        identity = next(item for item in manifest["BuildIdentities"] if item["ApChipID"] == "0x" + cpid and item["ApBoardID"] == "0x" + bdid and item["Info"]["RestoreBehavior"] == "Erase")
    except StopIteration:
        print("Error: Could not find restore identity for CPID " + cpid + " and BDID " + bdid + " in manifest")
        exit(5)
    if identity == None:
        print("Error: Could not find restore identity for CPID " + cpid + " and BDID " + bdid + " in manifest")
        exit(5)

    maxlen = 11
    if outtype == 2: 
        for k in identity["Manifest"].keys(): 
            if k == "RestoreSEP" or k == "RestoreDeviceTree": continue
            maxlen = max(maxlen, len(k) + (4 if "SEP" in k else 3))
    
    for k,v in identity["Manifest"].items():
        if not "Path" in v["Info"].keys(): continue
        if verbose: print("Getting keys for " + k)
        if k == "OS":
            output["RootFS"] = {"Path": v["Info"]["Path"], "Encrypted": False}
            continue
        if not (v["Info"]["Path"].endswith("im4p") or v["Info"]["Path"].endswith("img3") or v["Info"]["Path"].endswith("trustcache") or v["Info"]["Path"].endswith("dmg") or k == "RestoreRamDisk" or k == "KernelCache"): continue
        iv, key = (None, None)
        try:
            iv, key = getKeybag(zip.read(v["Info"]["Path"]), k)
        except ValueError:
            print("Error reading keys from " + k + ". Assuming it's unencrypted.")
            iv = None
        if iv == None: output[k] = {"Path": v["Info"]["Path"], "Encrypted": False}
        elif iv == "KBAG": output[k] = {"Path": v["Info"]["Path"], "Encrypted": True, "KBAG": key}
        else: output[k] = {"Path": v["Info"]["Path"], "Encrypted": True, "IV": iv, "Key": key}
    
    try:
        identity = next(item for item in manifest["BuildIdentities"] if item["ApChipID"] == "0x" + cpid and item["ApBoardID"] == "0x" + bdid and item["Info"]["RestoreBehavior"] == "Update")
    except StopIteration:
        print("Error: Could not find update identity for CPID " + cpid + " and BDID " + bdid + " in manifest")
        exit(5)
    if identity == None:
        print("Error: Could not find update identity for CPID " + cpid + " and BDID " + bdid + " in manifest")
        exit(5)

    maxlen = 11
    if outtype == 2: 
        for k in identity["Manifest"].keys(): 
            if k == "RestoreSEP" or k == "RestoreDeviceTree": continue
            maxlen = max(maxlen, len(k) + (4 if "SEP" in k else 3))
    
    for k,v in identity["Manifest"].items():
        if not "Path" in v["Info"].keys(): continue
        if k == "OS":
            output["RootFS"] = {"Path": v["Info"]["Path"], "Encrypted": False}
            continue
        if not (v["Info"]["Path"].endswith("im4p") or v["Info"]["Path"].endswith("img3") or v["Info"]["Path"].endswith("trustcache") or v["Info"]["Path"].endswith("dmg") or k == "RestoreRamDisk" or k == "KernelCache"): continue
        if k == "RestoreRamDisk": k = "UpdateRamDisk"
        if k == "RestoreTrustCache": k = "UpdateTrustCache"

        iv, key = getKeybag(zip.read(v["Info"]["Path"]), k)
        if iv == None: output[k] = {"Path": v["Info"]["Path"], "Encrypted": False}
        elif iv == "KBAG": output[k] = {"Path": v["Info"]["Path"], "Encrypted": True, "KBAG": key}
        else: output[k] = {"Path": v["Info"]["Path"], "Encrypted": True, "IV": iv, "Key": key}

    ProductType = None
    
    if "Restore.plist" in zip.namelist():
        restore = plistlib.readPlistFromString(zip.read("Restore.plist"))
        if "ProductType" in restore.keys(): ProductType = restore["ProductType"]

    zip.close()

    file = open(outfile, "w")
    if outtype == 0: json.dump(output, file)
    elif outtype == 1: file.write(plistlib.writePlistToString(output))
    else:
        file.write("""{}keys
 | Version{} = {}
 | Build{} = {}
 | Device{} = {}
 | Codename{} = {}
 | DownloadURL{} = {}

""".format("{{", " " * (maxlen - 7), manifest["ProductVersion"], " " * (maxlen - 5), manifest["ProductBuildVersion"], " " * (maxlen - 6), infodict["identifier"] if infodict != None else (ProductType if ProductType != None else "?"), " " * (maxlen - 8), identity["Info"]["BuildTrain"], " " * (maxlen - 11), infodict["url"] if infodict != None else "?"))
        output["GlyphPlugin"] = output["BatteryPlugin"]
        del output["BatteryPlugin"]
        for k in ["RootFS", "UpdateRamDisk", "RestoreRamDisk"]:
            if k not in output.keys(): continue
            v = output[k]
            if k == "RestoreRamDisk": k = "RestoreRamdisk"
            if k == "UpdateRamDisk": k = "UpdateRamdisk"
            file.write(" | " + k + (" " * (maxlen - len(k))) + " = " + path.basename(v["Path"]).replace(".dmg", "") + "\n")
            if v["Encrypted"]:
                file.write(" | " + k + "IV" + (" " * (maxlen - len(k) - 2)) + " = " + v["IV"] + "\n")
                file.write(" | " + k + "Key" + (" " * (maxlen - len(k) - 3)) + " = " + v["Key"] + "\n\n")
            elif k == "RootFS" and manifest["ProductVersion"][0] != "1":
                file.write(" | " + k + "IV" + (" " * (maxlen - len(k) - 2)) + " = ?\n")
                file.write(" | " + k + "Key" + (" " * (maxlen - len(k) - 3)) + " = ?\n\n")
            else:
                file.write(" | " + k + ("Key" if k == "RootFS" else "IV") + (" " * (maxlen - len(k) - (3 if k == "RootFS" else 2))) + " = Not Encrypted\n\n")
            if k == "RestoreRamdisk": k = "RestoreRamDisk"
            if k == "UpdateRamdisk": k = "UpdateRamDisk"
            del output[k]
        for k,v in sorted(output.items(), key=lambda k: (k[0].lower(), k[1])):
            if k == "RestoreSEP" or k == "RestoreDeviceTree" or k == "RestoreTrustCache" or k == "UpdateTrustCache" or k == "StaticTrustCache" or k == "RestoreLogo": continue
            if k == "KernelCache": k = "Kernelcache"
            if k == "SEP": k = "SEPFirmware"
            file.write(" | " + k + (" " * (maxlen - len(k))) + " = " + path.basename(v["Path"]).replace(".dmg", "") + "\n")
            if v["Encrypted"]:
                if "KBAG" in v.keys():
                    file.write(" | " + k + "IV" + (" " * (maxlen - len(k) - 2)) + " = Unknown\n | " + k + "Key" + (" " * (maxlen - len(k) - 3)) + " = Unknown\n")
                    file.write(" | " + k + "KBAG" + (" " * (maxlen - len(k) - 4)) + " = " + v["KBAG"] + "\n\n")
                else:
                    file.write(" | " + k + "IV" + (" " * (maxlen - len(k) - 2)) + " = " + v["IV"] + "\n")
                    file.write(" | " + k + "Key" + (" " * (maxlen - len(k) - 3)) + " = " + v["Key"] + "\n\n")
            else:
                file.write(" | " + k + "IV" + (" " * (maxlen - len(k) - 2)) + " = Not Encrypted\n\n")
        file.write("}}")
    file.close()

    if delete:
        print("Cleaning up...")
        remove(infile)

    print("Keys saved to " + outfile)

def usage():
    print("Usage: " + argv[0] + " <-i <input>|-d <identifier>> [-jpw] [-v <version>] [-b <bdid>] [options] [-a] [-o <output>]")
    print("Extracts iOS encryption keys from an IPSW using a physical device's AES engine.")
    print("")
    print("Required arguments:")
    print("    -d, --device <identifier>    Device identifier to download IPSW for")
    print("    -i, --input <input>          Local IPSW file to read from")
    print("    -o, --output <output>        Location to store output keys")
    print("")
    print("Optional arguments:")
    print("    -a, --auto-name              Automatically name output based on version and device, and save in folder at <output> if specified")
    print("    -b, --bdid <BDID>|*          Use a custom board ID instead of the current device's BDID")
    print("    -h, --help                   Show this help prompt")
    print("    -j, --json                   Store output as JSON file")
    print("    -p, --plist                  Store output as property list file")
    print("    -v, --version <version>      Version of iOS to download (without this, downloads all versions and implies -a)")
    print("    -w, --wiki                   Format output for iPhone Wiki upload")

def getext(t):
    if t == 0: return "json"
    elif t == 1: return "plist"
    else: return "wiki"

if __name__ == "__main__":
    if len(argv) == 1:
        usage()
        exit(0)
    
    optlist, args = getopt.getopt(argv[1:], "hi:o:d:v:ajpwb:", ["device=", "input=", "output=", "auto-name", "json", "plist", "version=", "wiki", "help", "bdid=", "verbose"])
    inputName = None
    inputDevice = None
    inputVersion = None
    outputName = None
    outputType = None
    autoName = False
    for o, a in optlist:
        if o == "-j" or o == "--json":
            if outputType == None: outputType = 0
            else: 
                print("Error: Only one of -j, -p, -w can be specified.")
                exit(1)
        elif o == "-p" or o == "--plist":
            if outputType == None: outputType = 1
            else: 
                print("Error: Only one of -j, -p, -w can be specified.")
                exit(1)
        elif o == "-w" or o == "--wiki":
            if outputType == None: outputType = 2
            else: 
                print("Error: Only one of -j, -p, -w can be specified.")
                exit(1)
        elif o == "-d" or o == "--device":
            inputDevice = a
        elif o == "-i" or o == "--input":
            inputName = a
        elif o == "-o" or o == "--output":
            outputName = a
        elif o == "-v" or o == "--version":
            inputVersion = a
        elif o == "-a" or o == "--auto-name":
            autoName = True
        elif o == "-h" or o == "--help":
            usage()
            exit(0)
        elif o == "-b" or o == "--bdid":
            bdid = a
        elif o == "--verbose":
            verbose = True
    
    if outputType == None: outputType = 0
    if inputName == None and inputDevice == None:
        print("Error: No input file or device specified.")
        exit(1)
    if outputName == None and autoName == False:
        print("Error: No output file specified.")
        exit(1)
    if inputName != None and inputDevice != None:
        print("Error: Only one of -i, -d can be specified.")
        exit(1)
    if inputName != None and inputVersion != None:
        print("Error: -v cannot be used in conjunction with -i.")
        exit(1)

    infoDict = None
    getInfo()

    if inputName == None: 
        def getFile(url):
            uri = urlparse(url)
            con = httplib.HTTPSConnection(uri.netloc, context=ssl.SSLContext(ssl.PROTOCOL_SSLv23))
            con.request("GET", uri.path)
            res = con.getresponse()
            if math.floor(res.status / 100) != 2:
                print(res.status)
                return None
            retval = res.read()
            con.close()
            return retval

        firmwares_json = getFile("https://api.ipsw.me/v4/device/" + inputDevice)
        if firmwares_json == None:
            print("Error: Unknown device type " + inputDevice)
            exit(2)
        firmwares = json.loads(firmwares_json)
        if firmwares == None:
            print("Error: Could not decode firmwares json")
            exit(2)
        
        if inputVersion == None:
            print("Warning: -v was not specified, downloading all versions available. This will take a long time. Did you mean to specify -v?")
            for firm in firmwares["firmwares"]:
                print("Downloading iOS " + firm["version"] + " (" + firm["buildid"] + ") for device " + firm["identifier"] + "...")
                system("curl -o firmware.ipsw -L --progress-bar " + firm["url"])
                extractKeys("firmware.ipsw", outputName + "/" + firm["identifier"] + "_" + firm["version"] + "_" + firm["buildid"] + "_Keys.json", outtype=outputType, delete=True, infodict=firm)
            exit(0)

        try:
            firm = next(item for item in firmwares["firmwares"] if item["version"] == inputVersion)
        except StopIteration:
            print("Error: Unknown version " + inputVersion + " for device " + inputDevice)
            exit(3)
        if firm == None:
            print("Error: Unknown version " + inputVersion + " for device " + inputDevice)
            exit(3)

        print("Downloading iOS " + firm["version"] + " (" + firm["buildid"] + ") for device " + firm["identifier"] + "...")
        system("curl -o firmware.ipsw -L --progress-bar " + firm["url"])
        inputName = "firmware.ipsw"
        infoDict = firm
        if autoName: outputName = (outputName + "/" if outputName != None else "") + firm["identifier"] + "_" + firm["version"] + "_" + firm["buildid"] + "_Keys." + getext(outputType)
    
    elif autoName: 
        if re.match("iP\w+?[0-9a-z._,]+?[0-9.]+_[0-9]+[A-Z][0-9]+_Restore.ipsw", path.basename(inputName)):
            m = re.match("(iP\w+?[0-9a-z._,]+?[0-9.]+_[0-9]+[A-Z][0-9]+)_Restore.ipsw", path.basename(inputName))
            outputName = m.group(1) + "_Keys." + getext(outputType)
        else: outputName = outputName + "/" + inputName.replace(".ipsw", "") + "_Keys." + getext(outputType)
    
    extractKeys(inputName, outputName, outtype=outputType, delete=inputDevice != None, infodict=infoDict)