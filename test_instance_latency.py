#!/usr/bin/env python3
"""
Run latency test locally or on remote EC2 instances with beautiful formatted output.

This tool:
1. Automatically loads IP lists from discovered IPs for comprehensive testing
2. Falls back to DNS resolution if no IP list available
3. For remote: deploys test script and runs via SSH
4. For local: runs test directly on this machine
5. Displays beautiful formatted results with detailed statistics

Usage: 
  python3 test_instance_latency.py <instance-id>  # Run on remote EC2 instance
  python3 test_instance_latency.py               # Run locally for baseline comparison
"""

import sys
import os
import json
import subprocess
import socket
import boto3

def get_instance_public_ip(instance_id, region):
    """Get the public IP of an instance."""
    ec2 = boto3.client('ec2', region_name=region)
    
    try:
        response = ec2.describe_instances(InstanceIds=[instance_id])
        if not response['Reservations']:
            return None, "Instance not found"
        
        instance = response['Reservations'][0]['Instances'][0]
        
        # Check instance state
        state = instance['State']['Name']
        if state != 'running':
            return None, f"Instance is {state}, not running"
        
        # Get public IP
        public_ip = instance.get('PublicIpAddress')
        if not public_ip:
            # Check if it has an associated EIP
            if 'Association' in instance.get('NetworkInterfaces', [{}])[0]:
                public_ip = instance['NetworkInterfaces'][0]['Association'].get('PublicIp')
        
        if not public_ip:
            return None, "Instance has no public IP address"
        
        return public_ip, None
        
    except Exception as e:
        return None, str(e)

def main():
    if len(sys.argv) > 2:
        print("Usage: python3 test_instance_latency.py [instance-id]")
        print("  If no instance-id provided, runs latency test locally")
        sys.exit(1)
    
    # Check if running locally or on remote instance
    run_locally = len(sys.argv) == 1
    instance_id = None if run_locally else sys.argv[1]
    
    # Load config
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, "r") as f:
        config = json.load(f)
    
    if run_locally:
        print("Running latency test locally...")
        public_ip = None
        key_path = None
        test_script = os.path.join(os.path.dirname(__file__), "core", "testing", "binance_latency_test.py")
    else:
        # Get instance public IP
        print(f"Getting public IP for {instance_id}...")
        public_ip, error = get_instance_public_ip(instance_id, config['region'])
        
        if not public_ip:
            print(f"[ERROR] {error}")
            sys.exit(1)
        
        print(f"Instance public IP: {public_ip}")
        
        key_path = os.path.expanduser(config['key_path'])
        
        # Copy and run the latency test
        test_script = os.path.join(os.path.dirname(__file__), "core", "testing", "binance_latency_test.py")
        
        print("\nDeploying and running latency test...")
        
        # SCP the file
        scp_cmd = [
            "scp",
            "-i", key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            test_script,
            f"ec2-user@{public_ip}:~/"
        ]
        
        result = subprocess.run(scp_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print("Failed to copy test script:")
            print(result.stderr)
            sys.exit(1)
    
    # Load IP list for both local and remote execution
    ip_list_file = os.path.join(os.path.dirname(__file__), "reports", "ip_lists", "ip_list_latest.json")
    ip_list_loaded = False
    
    if os.path.exists(ip_list_file):
        # Try to load IP list directly from file
        try:
            # Add parent directory to path for imports
            parent_dir = os.path.dirname(__file__)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            
            # Try using the core module
            try:
                from core.ip_discovery import IPPersistence
                persistence = IPPersistence(config['report_dir'])
                ip_data = persistence.load_latest()
                ip_list = persistence.get_all_active_ips(ip_data)
            except ImportError:
                # Fallback: Load IP list directly from file
                print("[INFO] Loading IP list directly from file")
                with open(ip_list_file, 'r') as f:
                    ip_data = json.load(f)
                
                # Extract IPs from the data structure
                ip_list = {}
                for domain, domain_data in ip_data.get('domains', {}).items():
                    active_ips = []
                    for ip, ip_info in domain_data.get('ips', {}).items():
                        if ip_info.get('alive', True):  # Default to True if not specified
                            active_ips.append(ip)
                    if active_ips:
                        ip_list[domain] = active_ips
            
            if ip_list:
                print(f"[INFO] Using discovered IP list for testing ({len(ip_list)} domains)")
                ip_list_loaded = True
            else:
                print("[INFO] No active IPs found in IP list")
                
        except Exception as e:
            print(f"[WARN] Failed to load IP list: {e}")
    else:
        print("[INFO] No IP list file found at expected location")
    
    # If IP list wasn't loaded, fall back to DNS resolution
    if not ip_list_loaded:
        print("[WARN] Running without pre-discovered IPs (DNS resolution only)")
        print("[INFO] Performing local DNS resolution for domains...")
        
        ip_list = {}
        for domain in config.get('domains', []):
            try:
                # Do DNS resolution
                ips = socket.gethostbyname_ex(domain)[2]
                if ips:
                    ip_list[domain] = ips
                    print(f"  - {domain}: {len(ips)} IPs resolved")
            except Exception as e:
                print(f"  - {domain}: DNS resolution failed: {e}")
        
        if not ip_list:
            print("[ERROR] Could not resolve any domains")
            sys.exit(1)

    if run_locally:
        # For local execution, create temporary IP list file
        with open('/tmp/ip_list_local.json', 'w') as f:
            json.dump(ip_list, f)
        
        domains_args = " ".join(config.get('domains', []))
        test_command = f"python3 {test_script} --domains {domains_args} --ip-list /tmp/ip_list_local.json"
        print(f"[INFO] Running locally: {test_command}")
    else:
        # For remote execution, deploy the IP list to the instance
        # Use a temporary file to avoid shell escaping issues
        with open('/tmp/ip_list_deploy.json', 'w') as f:
            json.dump(ip_list, f)
        
        # SCP the IP list file
        scp_ip_cmd = [
            "scp",
            "-i", key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "/tmp/ip_list_deploy.json",
            f"ec2-user@{public_ip}:/tmp/ip_list.json"
        ]
        result = subprocess.run(scp_ip_cmd, capture_output=True, text=True)
        
        # Clean up temporary file
        os.unlink('/tmp/ip_list_deploy.json')
        
        if result.returncode == 0:
            domains_args = " ".join(config.get('domains', []))
            test_command = f"python3 binance_latency_test.py --domains {domains_args} --ip-list /tmp/ip_list.json"
        else:
            print(f"[ERROR] Failed to deploy IP list: {result.stderr}")
            sys.exit(1)
    
    # Run the test
    if run_locally:
        # Run locally without SSH - build proper command args
        cmd_args = [
            "python3", test_script,
            "--domains"
        ] + config.get('domains', []) + [
            "--ip-list", "/tmp/ip_list_local.json"
        ]
        process = subprocess.Popen(cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    else:
        # Run via SSH on remote instance
        ssh_cmd = [
            "ssh",
            "-i", key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            f"ec2-user@{public_ip}",
            test_command
        ]
        process = subprocess.Popen(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # Display stderr (progress) in real-time
    import select
    import fcntl
    
    # Make stderr non-blocking
    stderr_fd = process.stderr.fileno()
    flags = fcntl.fcntl(stderr_fd, fcntl.F_GETFL)
    fcntl.fcntl(stderr_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    
    stdout_data = []
    while True:
        if process.poll() is not None:
            break
        
        # Read stderr for progress
        ready, _, _ = select.select([process.stderr], [], [], 0.1)
        if ready:
            try:
                line = process.stderr.readline()
                if line:
                    print(line.rstrip())
            except:
                pass
    
    # Get remaining output
    stdout, stderr = process.communicate()
    if stdout:
        stdout_data.append(stdout)
    if stderr:
        print(stderr)
    
    # Parse and display results
    full_stdout = ''.join(stdout_data)
    if full_stdout:
        try:
            results = json.loads(full_stdout)
            print("\n" + "="*60)
            print("RESULTS:")
            print("="*60)
            
            # Process results
            median_threshold = config['latency_thresholds']['median_us']
            best_threshold = config['latency_thresholds']['best_us']
            
            # Get instance type from EC2 (if running remotely)
            if run_locally:
                instance_type = "local"
                display_id = "LOCAL"
            else:
                ec2 = subprocess.run([
                    'aws', 'ec2', 'describe-instances',
                    '--instance-ids', instance_id,
                    '--region', config['region'],
                    '--query', 'Reservations[0].Instances[0].InstanceType',
                    '--output', 'text'
                ], capture_output=True, text=True)
                instance_type = ec2.stdout.strip() if ec2.returncode == 0 else "unknown"
                display_id = instance_id
            
            # Format timestamp
            import datetime
            timestamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S%z')
            
            print(f"\n[{timestamp}] {display_id}  {instance_type:<9}")
            
            # Print detailed results per domain and IP
            instance_passed = False
            summary_data = {}  # Collect summary data
            
            # Domain abbreviations
            domain_abbrev = {
                "fstream-mm.binance.com": "fstream-mm",
                "ws-fapi-mm.binance.com": "ws-fapi-mm",
                "fapi-mm.binance.com": "fapi-mm",
                "stream.binance.com": "stream",
                "ws-api.binance.com": "ws-api",
                "api.binance.com": "api"
            }
            
            print("\nDetailed results by IP:")
            for domain, data in results.items():
                if "error" in data:
                    print(f"  {domain}: ERROR - {data['error']}")
                    continue
                
                print(f"  {domain}:")
                
                # Find best values across all IPs
                best_median = float('inf')
                best_single = float('inf')
                best_avg = float('inf')
                best_p99 = float('inf')
                best_median_ip = None
                best_single_ip = None
                best_avg_ip = None
                best_p99_ip = None
                
                # Sort IPs by average latency (best/lowest first)
                ips = sorted(data.get("ips", {}).items(), 
                           key=lambda x: x[1].get("average", float("inf")))
                for ip, stats in ips:
                    median = stats.get("median", float("inf"))
                    best = stats.get("best", float("inf"))
                    avg = stats.get("average", float("inf"))
                    p1 = stats.get("p1", float("inf"))
                    p99 = stats.get("p99", float("inf"))
                    max_val = stats.get("max", float("inf"))
                    
                    print(f"    IP {ip:<15} median={median:>7.2f} avg={avg:>7.2f} "
                          f"p1={p1:>7.2f} p99={p99:>7.2f} max={max_val:>7.2f} µs")
                    
                    # Track best values
                    if median < best_median:
                        best_median = median
                        best_median_ip = ip
                    if best < best_single:
                        best_single = best
                        best_single_ip = ip
                    if avg < best_avg:
                        best_avg = avg
                        best_avg_ip = ip
                    if p99 < best_p99:
                        best_p99 = p99
                        best_p99_ip = ip
                
                # Store summary data
                summary_data[domain] = {
                    "best_median": best_median,
                    "best_median_ip": best_median_ip,
                    "best_single": best_single,
                    "best_single_ip": best_single_ip,
                    "best_avg": best_avg,
                    "best_avg_ip": best_avg_ip,
                    "best_p99": best_p99,
                    "best_p99_ip": best_p99_ip,
                    "passed": best_median <= median_threshold or best_single <= best_threshold
                }
                
                if summary_data[domain]["passed"]:
                    instance_passed = True
            
            # Print summary at the bottom
            print("\nSummary - Best results per domain:")
            print("-" * 90)
            for domain, summary in summary_data.items():
                abbrev = domain_abbrev.get(domain, domain[:10])
                print(f"  {abbrev}:")
                print(f"    Best median: {summary['best_median']:>7.2f}µs ({summary['best_median_ip']})")
                print(f"    Best single: {summary['best_single']:>7.2f}µs ({summary['best_single_ip']})")
                print(f"    Best avg:    {summary['best_avg']:>7.2f}µs ({summary['best_avg_ip']})")
                print(f"    Best p99:    {summary['best_p99']:>7.2f}µs ({summary['best_p99_ip']})")
            print(f"\n  Instance Passed: {instance_passed}")
            print("-" * 90)
            
        except json.JSONDecodeError:
            print("\nRaw output:")
            print(full_stdout)
    
    # Clean up temporary files
    if run_locally and os.path.exists('/tmp/ip_list_local.json'):
        os.unlink('/tmp/ip_list_local.json')
    
    if run_locally:
        print(f"\nTest complete. Ran locally on this machine.")
    else:
        print(f"\nTest complete. Instance {instance_id} public IP: {public_ip}")

if __name__ == "__main__":
    main()