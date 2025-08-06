"""
Internal latency testing script for the latency finder.

This script performs TCP handshake latency measurements to Binance endpoints.
It's designed to be deployed to EC2 instances or run locally, and outputs
JSON results for programmatic analysis.

Note: This is an internal script. Use test_instance_latency.py for user-facing
latency testing with beautiful formatted output.
"""

import socket, statistics, time, sys, json, argparse, os
from datetime import datetime

ATTEMPTS = 1000
WARMUP_ATTEMPTS = 100  # Warmup attempts to populate caches
DEFAULT_TIMEOUT_MS = 1000  # Default TCP timeout in milliseconds

# Progress reporting to stderr
def log_progress(message):
    """Log progress message with timestamp to stderr"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", file=sys.stderr)
    sys.stderr.flush()  # Ensure immediate output

def load_config_domains():
    """Load domains from config.json file."""
    config_path = "config.json"
    # Check parent directory if not in current directory
    if not os.path.exists(config_path):
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(config_path):
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            return config.get('domains', [])
    except (IOError, json.JSONDecodeError) as e:
        log_progress(f"WARNING: Failed to load config.json: {e}")
        return []

def test_latency(ip, hostname, timeout_seconds):
    # Warmup phase - establish connections but don't record timings
    warmup_success = 0
    warmup_errors = {}
    for i in range(WARMUP_ATTEMPTS):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout_seconds)
            s.connect((ip, 443))
            s.close()
            warmup_success += 1
        except socket.error as e:
            error_msg = str(e)
            warmup_errors[error_msg] = warmup_errors.get(error_msg, 0) + 1
            continue
    
    if warmup_errors:
        for error_msg, count in warmup_errors.items():
            log_progress(f"    WARMUP: {count} failures with error: {error_msg}")
    
    if warmup_success == 0:
        log_progress(f"    ERROR: No successful connections during warmup to {ip}")
        return float("inf"), float("inf")
    
    # Actual measurement phase
    latencies = []
    test_errors = {}
    for i in range(ATTEMPTS):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout_seconds)
            t0 = time.perf_counter_ns()
            s.connect((ip, 443))
            t1 = time.perf_counter_ns()
            s.close()
            latencies.append((t1 - t0) / 1000)  # ns to microseconds
        except socket.error as e:
            error_msg = str(e)
            test_errors[error_msg] = test_errors.get(error_msg, 0) + 1
            continue
    
    if test_errors:
        for error_msg, count in test_errors.items():
            log_progress(f"    TEST: {count} failures with error: {error_msg}")
    
    if not latencies:
        log_progress(f"    ERROR: No successful connections to {ip}")
        return {
            "median": float("inf"),
            "best": float("inf"),
            "average": float("inf"),
            "p1": float("inf"),
            "p99": float("inf"),
            "max": float("inf")
        }
    
    success_rate = len(latencies) / ATTEMPTS * 100
    log_progress(f"    Success rate: {success_rate:.1f}% ({len(latencies)}/{ATTEMPTS} connections)")
    
    # Calculate all statistics
    sorted_latencies = sorted(latencies)
    n = len(sorted_latencies)
    
    # Calculate percentiles
    p1_index = int(n * 0.01)
    p99_index = int(n * 0.99)
    if p99_index >= n:
        p99_index = n - 1
    
    stats = {
        "median": statistics.median(sorted_latencies),
        "best": sorted_latencies[0],  # min
        "average": statistics.mean(sorted_latencies),
        "p1": sorted_latencies[p1_index],
        "p99": sorted_latencies[p99_index],
        "max": sorted_latencies[-1]  # max
    }
    
    return stats

def parse_ip_input(input_data):
    """Parse IP input data from JSON string, file path, or stdin.
    
    Expected format:
    {
        "domain1": ["ip1", "ip2", ...],
        "domain2": ["ip3", "ip4", ...],
        ...
    }
    
    Returns:
        Dictionary of domain -> list of IPs
    """
    if input_data == '-':
        # Read from stdin
        data = sys.stdin.read()
    elif input_data.startswith('/') or input_data.startswith('./'):
        # Read from file
        try:
            with open(input_data, 'r') as f:
                data = f.read()
        except IOError as e:
            log_progress(f"ERROR: Cannot read file {input_data}: {e}")
            return None
    else:
        # Assume it's a JSON string
        data = input_data
    
    try:
        ip_dict = json.loads(data)
        return ip_dict
    except json.JSONDecodeError as e:
        log_progress(f"ERROR: Invalid JSON input: {e}")
        return None

def main():
    global WARMUP_ATTEMPTS  # Declare at the beginning
    parser = argparse.ArgumentParser(description="Test latency to Binance IPs")
    parser.add_argument(
        "--ip-list",
        default=None,
        help="JSON string or '-' to read from stdin with domain->IPs mapping"
    )
    parser.add_argument(
        "--domains",
        nargs='+',
        help="List of domains to test (overrides config.json)"
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=WARMUP_ATTEMPTS,
        help=f"Number of warmup attempts (default: {WARMUP_ATTEMPTS})"
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="Skip warmup phase"
    )
    parser.add_argument(
        "--tcp-timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help=f"TCP connection timeout in milliseconds (default: {DEFAULT_TIMEOUT_MS}ms)"
    )
    args = parser.parse_args()
    
    # Update warmup attempts
    if args.no_warmup:
        WARMUP_ATTEMPTS = 0
    else:
        WARMUP_ATTEMPTS = args.warmup
    
    # Convert TCP timeout from milliseconds to seconds
    tcp_timeout_seconds = args.tcp_timeout_ms / 1000.0
    
    # Determine domains to test
    domains = None
    if args.domains:
        # Use domains from command line
        domains = args.domains
        log_progress(f"Using domains from command line: {domains}")
    else:
        # Try to load from config.json
        domains = load_config_domains()
        if domains:
            log_progress(f"Using domains from config.json: {domains}")
        else:
            log_progress("ERROR: No domains specified. Use --domains argument or add 'domains' to config.json")
            sys.exit(1)
    
    results = {}
    total_start = time.time()
    
    # Get IP list
    if args.ip_list:
        # Use provided IP list
        domain_ips = parse_ip_input(args.ip_list)
        if not domain_ips:
            sys.exit(1)
        # Filter to only requested domains
        domain_ips = {d: ips for d, ips in domain_ips.items() if d in domains}
        if not domain_ips:
            log_progress(f"ERROR: No IPs provided for requested domains: {domains}")
            sys.exit(1)
    else:
        # IP list is required when using binance_latency_test.py directly
        log_progress("ERROR: --ip-list is required when running binance_latency_test.py directly")
        log_progress("Use discover_ips.py to generate IP list or provide via --ip-list")
        sys.exit(1)
    
    log_progress(f"Starting latency tests for {len(domains)} domains")
    log_progress(f"Configuration: {WARMUP_ATTEMPTS} warmup + {ATTEMPTS} measured attempts per IP, {tcp_timeout_seconds:.3f}s timeout")
    log_progress("=" * 60)
    
    for domain_idx, hostname in enumerate(domains, 1):
        domain_start = time.time()
        log_progress(f"\n[{domain_idx}/{len(domains)}] Testing domain: {hostname}")
        
        ips = domain_ips.get(hostname, [])
        results[hostname] = {"ips": {}}
        
        if not ips:
            results[hostname]["error"] = f"No IPs provided for {hostname}"
            log_progress(f"  SKIPPING: No IPs for {hostname}")
            continue
        
        for ip_idx, ip in enumerate(ips, 1):
            log_progress(f"  [{ip_idx}/{len(ips)}] Testing {hostname} ({ip})...")
            try:
                stats = test_latency(ip, hostname, tcp_timeout_seconds)
                results[hostname]["ips"][ip] = stats
            except Exception as e:
                log_progress(f"    ERROR: Exception during latency test: {e}")
                results[hostname]["ips"][ip] = {
                    "median": float("inf"),
                    "best": float("inf"),
                    "average": float("inf"),
                    "p1": float("inf"),
                    "p99": float("inf"),
                    "max": float("inf"),
                    "error": str(e)
                }
        
        domain_elapsed = time.time() - domain_start
        log_progress(f"  Domain completed in {domain_elapsed:.1f}s")
    
    total_elapsed = time.time() - total_start
    log_progress(f"\n{'=' * 60}")
    log_progress(f"All tests completed in {total_elapsed:.1f}s")
    
    # Output JSON for easy parsing
    print(json.dumps(results))
    sys.stdout.flush()  # Ensure output is flushed immediately

if __name__ == "__main__":
    main()