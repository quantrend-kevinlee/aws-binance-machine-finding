"""
Reference latency testing script provided by client DC
This script contains some bugs that are noted in comments
"""
import socket
import time
import os

hostnames = [
    "fapi-mm.binance.com",
    "ws-fapi-mm.binance.com",
    "fstream-mm.binance.com"
]
hostname_ips = dict()
for hostname in hostnames:
    r = os.popen("host " + hostname)
    hostname_ips[hostname] = list()
    for text in r.readlines():
        # print(text)
        # NOTE: This parsing assumes specific output format from 'host' command
        # May fail if format changes or on different systems
        hostname_ips[hostname].append(text.split(" ")[3][0:-1])

# print(hostname_ips)
for k in hostname_ips.keys():
    v = hostname_ips[k]
    print(f"{k}:")
    for ip in v:
        latency = 0
        for i in range(100):
            try:
                start_time = time.time()
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1)
                s.connect((ip, 443))
                end_time = time.time()
                latency += end_time - start_time
                s.close()
            except socket.error as e:
                # BUG: ip.address should be just ip (ip is a string, not an object)
                print(f"Connection failed for {ip.address}: {e}")
        # Calculate average latency: (sum of 100 measurements) * 10 * 1000 = average in microseconds
        print(latency * 10 * 1000, "us --", ip)