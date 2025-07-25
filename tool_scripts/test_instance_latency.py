#!/usr/bin/env python3
"""
Bind EIP to an instance and run latency test.

This tool automates the process of:
1. Binding the Elastic IP to a specified instance
2. Deploying the binance_latency_test.py script
3. Running the latency test
4. Displaying formatted results

Usage: python3 test_instance_latency.py <instance-id>
"""

import sys
import os
import json
import subprocess
import socket

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 test_instance_latency.py <instance-id>")
        sys.exit(1)
    
    instance_id = sys.argv[1]
    
    # First bind the EIP
    print(f"Binding EIP to {instance_id}...")
    bind_script = os.path.join(os.path.dirname(__file__), "bind_eip.py")
    result = subprocess.run([sys.executable, bind_script, instance_id], capture_output=True, text=True)
    
    if result.returncode != 0:
        print("Failed to bind EIP:")
        print(result.stdout)
        print(result.stderr)
        sys.exit(1)
    
    # Extract IP from output
    eip_address = None
    for line in result.stdout.split('\n'):
        if "Success! EIP" in line:
            parts = line.split()
            for i, part in enumerate(parts):
                if part == "EIP" and i + 1 < len(parts):
                    potential_ip = parts[i + 1]
                    # Validate it's an IP address
                    if '.' in potential_ip and potential_ip.count('.') == 3:
                        try:
                            # Basic IP validation
                            octets = potential_ip.split('.')
                            if all(0 <= int(octet) <= 255 for octet in octets):
                                eip_address = potential_ip
                                break
                        except ValueError:
                            pass
    
    if not eip_address:
        print("Could not extract IP from bind_eip output")
        sys.exit(1)
    
    print(f"EIP bound: {eip_address}")
    
    # Load config for key path
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
    with open(config_path, "r") as f:
        config = json.load(f)
    key_path = os.path.expanduser(config['key_path'])
    
    # Copy and run the latency test
    test_script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "binance_latency_test.py")
    
    print("\nDeploying and running latency test...")
    
    # SCP the file
    scp_cmd = [
        "scp",
        "-i", key_path,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        test_script,
        f"ec2-user@{eip_address}:~/"
    ]
    
    result = subprocess.run(scp_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("Failed to copy test script:")
        print(result.stderr)
        sys.exit(1)
    
    # Check if we have an IP list available
    ip_list_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports", "ip_lists", "ip_list_latest.json")
    ip_list_loaded = False
    
    if os.path.exists(ip_list_file):
        # Try to load IP list directly from file
        try:
            # Add parent directory to path for imports
            parent_dir = os.path.dirname(os.path.dirname(__file__))
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
                # Deploy IP list to remote
                ip_json = json.dumps(ip_list)
                # Use a temporary file to avoid shell escaping issues
                with open('/tmp/ip_list_deploy.json', 'w') as f:
                    f.write(ip_json)
                
                # SCP the IP list file
                scp_ip_cmd = [
                    "scp",
                    "-i", key_path,
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null",
                    "/tmp/ip_list_deploy.json",
                    f"ec2-user@{eip_address}:/tmp/ip_list.json"
                ]
                result = subprocess.run(scp_ip_cmd, capture_output=True, text=True)
                
                # Clean up temporary file
                os.unlink('/tmp/ip_list_deploy.json')
                
                if result.returncode == 0:
                    print(f"[INFO] Using discovered IP list for testing ({len(ip_list)} domains)")
                    # Pass domains from config
                    domains_args = " ".join(f'"{domain}"' for domain in config.get('domains', []))
                    test_command = f"python3 binance_latency_test.py --domains {domains_args} --ip-list /tmp/ip_list.json"
                    ip_list_loaded = True
                else:
                    print(f"[WARN] Failed to deploy IP list: {result.stderr}")
            else:
                print("[INFO] No active IPs found in IP list")
                
        except Exception as e:
            print(f"[WARN] Failed to load IP list: {e}")
    else:
        print("[INFO] No IP list file found at expected location")
    
    # If IP list wasn't loaded, we need to provide domains at minimum
    if not ip_list_loaded:
        print("[WARN] Running without pre-discovered IPs (DNS resolution only)")
        # Create a minimal IP list by doing DNS resolution locally
        print("[INFO] Performing local DNS resolution for domains...")
        
        minimal_ip_list = {}
        for domain in config.get('domains', []):
            try:
                # Do DNS resolution
                ips = socket.gethostbyname_ex(domain)[2]
                if ips:
                    minimal_ip_list[domain] = ips
                    print(f"  - {domain}: {len(ips)} IPs resolved")
            except Exception as e:
                print(f"  - {domain}: DNS resolution failed: {e}")
        
        if minimal_ip_list:
            # Deploy minimal IP list
            ip_json = json.dumps(minimal_ip_list)
            with open('/tmp/ip_list_deploy.json', 'w') as f:
                f.write(ip_json)
            
            # SCP the IP list file
            scp_ip_cmd = [
                "scp",
                "-i", key_path,
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "/tmp/ip_list_deploy.json",
                f"ec2-user@{eip_address}:/tmp/ip_list.json"
            ]
            result = subprocess.run(scp_ip_cmd, capture_output=True, text=True)
            os.unlink('/tmp/ip_list_deploy.json')
            
            if result.returncode == 0:
                domains_args = " ".join(f'"{domain}"' for domain in config.get('domains', []))
                test_command = f"python3 binance_latency_test.py --domains {domains_args} --ip-list /tmp/ip_list.json"
            else:
                print("[ERROR] Failed to deploy even minimal IP list")
                sys.exit(1)
        else:
            print("[ERROR] Could not resolve any domains")
            sys.exit(1)
    
    # Run the test
    ssh_cmd = [
        "ssh",
        "-i", key_path,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        f"ec2-user@{eip_address}",
        test_command
    ]
    
    # Run with real-time output
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
            
            # Get instance type from EC2
            ec2 = subprocess.run([
                'aws', 'ec2', 'describe-instances',
                '--instance-ids', instance_id,
                '--region', config['region'],
                '--query', 'Reservations[0].Instances[0].InstanceType',
                '--output', 'text'
            ], capture_output=True, text=True)
            instance_type = ec2.stdout.strip() if ec2.returncode == 0 else "unknown"
            
            # Format timestamp
            import datetime
            timestamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S%z')
            
            print(f"\n[{timestamp}] {instance_id}  {instance_type:<9}")
            
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
                
                # Find best median and best single across all IPs
                best_median = float('inf')
                best_single = float('inf')
                best_median_ip = None
                best_single_ip = None
                
                # Sort IPs for consistent output
                ips = sorted(data.get("ips", {}).items())
                for ip, stats in ips:
                    median = stats["median"]
                    best = stats["best"]
                    print(f"    IP {ip:<15} median={median:>8.2f} µs  best={best:>8.2f} µs")
                    
                    # Track best values
                    if median < best_median:
                        best_median = median
                        best_median_ip = ip
                    if best < best_single:
                        best_single = best
                        best_single_ip = ip
                
                # Store summary data
                summary_data[domain] = {
                    "best_median": best_median,
                    "best_median_ip": best_median_ip,
                    "best_single": best_single,
                    "best_single_ip": best_single_ip,
                    "passed": best_median <= median_threshold or best_single <= best_threshold
                }
                
                if summary_data[domain]["passed"]:
                    instance_passed = True
            
            # Print summary at the bottom
            print("\nSummary - Best results per domain:")
            print("-" * 80)
            for domain, summary in summary_data.items():
                abbrev = domain_abbrev.get(domain, domain[:10])
                print(f"  {abbrev}: median={summary['best_median']:.2f}µs ({summary['best_median_ip']}), "
                      f"best={summary['best_single']:.2f}µs ({summary['best_single_ip']})")
            print(f"\n  Instance Passed: {instance_passed}")
            print("-" * 80)
            
        except json.JSONDecodeError:
            print("\nRaw output:")
            print(full_stdout)
    
    print(f"\nTest complete. Instance {instance_id} still has EIP {eip_address} bound.")

if __name__ == "__main__":
    main()