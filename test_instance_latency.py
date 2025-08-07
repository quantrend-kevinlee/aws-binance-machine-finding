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
import boto3
from core.testing import SSHClient, LocalCommandRunner
from core.testing.file_deployment import create_ip_list_deployer

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
        
        # Create SSH client for file operations
        ssh_client = SSHClient(key_path)
        
        # Deploy test script using shared utilities
        print("[INFO] Deploying test script via SCP...")
        if not ssh_client.copy_file(public_ip, test_script, "~/binance_latency_test.py"):
            print("[ERROR] Failed to deploy test script")
            sys.exit(1)
        print("[INFO] Test script deployed successfully")
    
    # Load IP list using shared utilities
    ip_list_file = os.path.join(config['ip_list_dir'], "ip_list_latest.json")
    
    # Create IP list deployer for shared functionality
    if not run_locally:
        key_path = os.path.expanduser(config['key_path'])
        ip_deployer = create_ip_list_deployer(key_path)
    
    # Load IP list using the core discovery system
    try:
        # Add parent directory to path for imports
        parent_dir = os.path.dirname(__file__)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        
        from core.ip_discovery import load_ip_list
        ip_list = load_ip_list(ip_list_file, config['latency_test_domains'])
        
        if ip_list:
            total_ips = sum(len(ips) for ips in ip_list.values())
            print(f"[INFO] Loaded {total_ips} IPs from discovery system ({len(ip_list)} domains)")
        else:
            print("[ERROR] Could not load IP list from file or DNS fallback")
            sys.exit(1)
            
    except Exception as e:
        print(f"[ERROR] Failed to load IP list: {e}")
        sys.exit(1)

    if run_locally:
        # For local execution, create temporary IP list file
        with open('/tmp/ip_list_local.json', 'w') as f:
            json.dump(ip_list, f)
        
        domains_args = " ".join(config['latency_test_domains'])
        tcp_timeout_ms = config.get('tcp_connection_timeout_ms', 1000)  # Default to 1000ms if not in config
        test_command = f"python3 {test_script} --domains {domains_args} --ip-list /tmp/ip_list_local.json --tcp-timeout-ms {tcp_timeout_ms}"
        print(f"[INFO] Running locally: {test_command}")
    else:
        # For remote execution, deploy the IP list using shared utilities
        print("[INFO] Deploying IP list via SCP...")
        
        # Use the file deployer from SSH client to deploy IP list
        file_deployer = ssh_client.file_deployer if hasattr(ssh_client, 'file_deployer') else ip_deployer.file_deployer
        
        if file_deployer.deploy_ip_list(public_ip, ip_list, "/tmp/ip_list.json"):
            domains_args = " ".join(config['latency_test_domains'])
            tcp_timeout_ms = config.get('tcp_connection_timeout_ms', 1000)  # Default to 1000ms if not in config
            test_command = f"python3 binance_latency_test.py --domains {domains_args} --ip-list /tmp/ip_list.json --tcp-timeout-ms {tcp_timeout_ms}"
            print("[INFO] IP list deployed successfully via SCP")
        else:
            print("[ERROR] Failed to deploy IP list via SCP")
            sys.exit(1)
    
    # Run the test using shared utilities
    print("\n" + "="*60)
    print("RUNNING LATENCY TEST")
    print("="*60)
    
    if run_locally:
        # Run locally without SSH - use LocalCommandRunner for real-time progress
        tcp_timeout_ms = config.get('tcp_connection_timeout_ms', 1000)  # Default to 1000ms if not in config
        cmd_args = [
            "python3", test_script,
            "--domains"
        ] + config['latency_test_domains'] + [
            "--ip-list", "/tmp/ip_list_local.json",
            "--tcp-timeout-ms", str(tcp_timeout_ms)
        ]
        print(f"[INFO] Running locally: {' '.join(cmd_args)}")
        print("Progress will be displayed below:")
        print("-" * 40)
        
        # Use LocalCommandRunner for consistent progress display
        local_runner = LocalCommandRunner()
        full_stdout, stderr_output, return_code = local_runner.run_command_with_progress(
            cmd_args, 
            timeout=1800  # 30 minute timeout
        )
        
        print("-" * 40)
        
        if return_code != 0:
            print(f"\n[ERROR] Local execution failed with return code {return_code}")
            if stderr_output:
                print(f"Error output: {stderr_output}")
            if full_stdout:
                print(f"Partial results: {full_stdout}")
            sys.exit(1)
            
    else:
        # Run via SSH using shared utilities with real-time progress
        print(f"[INFO] Running on remote instance: {test_command}")
        print("Real-time progress will be displayed below:")
        print("-" * 40)
        
        full_stdout, stderr_output, return_code = ssh_client.run_command_with_progress(
            public_ip, 
            test_command, 
            timeout=1800  # 30 minute timeout
        )
        
        print("-" * 40)
        
        if return_code != 0:
            print(f"\n[ERROR] Remote execution failed with return code {return_code}")
            if stderr_output:
                print(f"Error output: {stderr_output}")
            if full_stdout:
                print(f"Partial results: {full_stdout}")
            sys.exit(1)
    if full_stdout:
        try:
            print("\nProcessing results...")
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