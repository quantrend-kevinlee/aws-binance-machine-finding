import socket, subprocess, statistics, time, sys, json
from datetime import datetime

# Domain configuration
DOMAINS = [
    "fstream-mm.binance.com", # futures: xstream, auth stream
    "ws-fapi-mm.binance.com", # futures: wsapi
    "fapi-mm.binance.com", # futures: restful api
    "stream.binance.com", # spot: xstream, auth stream
    "ws-api.binance.com", # spot: wsapi
    "api.binance.com", # spot: restful api
]

ATTEMPTS = 1000
TIMEOUT = 1

# Progress reporting to stderr
def log_progress(message):
    """Log progress message with timestamp to stderr"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", file=sys.stderr)
    sys.stderr.flush()  # Ensure immediate output

def resolve_ips(hostname):
    ips = []
    log_progress(f"Resolving {hostname}...")
    try:
        result = subprocess.run(["host", hostname], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            parts = line.split()
            if "address" in parts:
                ips.append(parts[-1])
        log_progress(f"  Found {len(ips)} IPs for {hostname}")
    except subprocess.TimeoutExpired:
        log_progress(f"  ERROR: DNS resolution timeout for {hostname}")
    except Exception as e:
        log_progress(f"  ERROR: DNS resolution failed for {hostname}: {e}")
    return ips

def test_latency(ip, hostname):
    latencies = []
    
    for i in range(ATTEMPTS):
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
        log_progress(f"    ERROR: No successful connections to {ip}")
        return float("inf"), float("inf")
    
    median_us = statistics.median(latencies)
    best_us = min(latencies)
    return median_us, best_us

def main():
    results = {}
    total_start = time.time()
    
    log_progress(f"Starting latency tests for {len(DOMAINS)} domains")
    log_progress(f"Configuration: {ATTEMPTS} attempts per IP, {TIMEOUT}s timeout")
    log_progress("=" * 60)
    
    for domain_idx, hostname in enumerate(DOMAINS, 1):
        domain_start = time.time()
        log_progress(f"\n[{domain_idx}/{len(DOMAINS)}] Testing domain: {hostname}")
        
        ips = resolve_ips(hostname)
        results[hostname] = {"ips": {}}
        
        if not ips:
            results[hostname]["error"] = f"Could not resolve {hostname}"
            log_progress(f"  SKIPPING: No IPs found for {hostname}")
            continue
        
        for ip_idx, ip in enumerate(ips, 1):
            log_progress(f"  [{ip_idx}/{len(ips)}] Testing {hostname} ({ip})...")
            try:
                median_us, best_us = test_latency(ip, hostname)
                results[hostname]["ips"][ip] = {
                    "median": median_us,
                    "best": best_us
                }
            except Exception as e:
                log_progress(f"    ERROR: Exception during latency test: {e}")
                results[hostname]["ips"][ip] = {
                    "median": float("inf"),
                    "best": float("inf"),
                    "error": str(e)
                }
        
        domain_elapsed = time.time() - domain_start
        log_progress(f"  Domain completed in {domain_elapsed:.1f}s")
    
    total_elapsed = time.time() - total_start
    log_progress(f"\n{'=' * 60}")
    log_progress(f"All tests completed in {total_elapsed:.1f}s")
    
    # Output JSON for easy parsing
    print(json.dumps(results))

if __name__ == "__main__":
    main()