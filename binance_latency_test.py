import socket, subprocess, statistics, time, sys, json
ATTEMPTS = 10000
TIMEOUT = 1
HOSTNAMES = ["fapi-mm.binance.com", "ws-fapi-mm.binance.com", "fstream-mm.binance.com"]

def resolve_ips(hostname):
    ips = []
    result = subprocess.run(["host", hostname], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        parts = line.split()
        if "address" in parts:
            ips.append(parts[-1])
    return ips

def test_latency(ip):
    latencies = []
    for _ in range(ATTEMPTS):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(TIMEOUT)
            t0 = time.perf_counter_ns()
            s.connect((ip, 443))
            t1 = time.perf_counter_ns()
            s.close()
            latencies.append((t1 - t0) / 1000)  # ns to microseconds
        except socket.error:
            continue
    if not latencies:
        return float("inf"), float("inf")
    median_us = statistics.median(latencies)
    best_us = min(latencies)
    return median_us, best_us

def main():
    results = {}
    for hostname in HOSTNAMES:
        ips = resolve_ips(hostname)
        results[hostname] = {"ips": {}}
        if not ips:
            results[hostname]["error"] = f"Could not resolve {hostname}"
            continue
        for ip in ips:
            print(f"Testing {hostname} ({ip})...", file=sys.stderr)
            median_us, best_us = test_latency(ip)
            results[hostname]["ips"][ip] = {
                "median": median_us,
                "best": best_us
            }
    # Output JSON for easy parsing
    print(json.dumps(results))

if __name__ == "__main__":
    main()