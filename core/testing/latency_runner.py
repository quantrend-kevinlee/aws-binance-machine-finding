"""Latency test execution for DC Machine."""

import json
import os
from typing import Dict, Any, Optional

from .ssh_client import SSHClient


class LatencyTestRunner:
    """Runs latency tests on EC2 instances."""
    
    def __init__(self, ssh_client: SSHClient, domains: list = None,
                 timeout_per_domain: int = 30, min_timeout: int = 180):
        """Initialize latency test runner.
        
        Args:
            ssh_client: SSH client instance
            domains: List of domains to test
            timeout_per_domain: Timeout seconds per domain
            min_timeout: Minimum timeout regardless of domain count
        """
        self.ssh_client = ssh_client
        self._test_script = None
        self.domains = domains or []
        self.num_domains = len(self.domains)
        self.timeout_per_domain = timeout_per_domain
        self.min_timeout = min_timeout
        # Calculate timeout based on domain count, with configured minimum
        self.test_timeout = max(self.min_timeout, self.timeout_per_domain * self.num_domains)
    
    def load_test_script(self, script_path: str = "binance_latency_test.py") -> None:
        """Load the latency test script.
        
        Args:
            script_path: Path to test script file
        """
        full_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), script_path)
        with open(full_path, "r") as f:
            self._test_script = f.read()
    
    def run_latency_test(self, eip_address: str, ip_list: Optional[Dict[str, list]] = None) -> Optional[Dict[str, Any]]:
        """Run latency test on instance and return results.
        
        Args:
            eip_address: EIP address of instance
            ip_list: Optional dict of domain -> list of IPs to test
            
        Returns:
            Test results dict or None on failure
        """
        if not self._test_script:
            print("[ERROR] Test script not loaded")
            return None
        
        print("Running latency test via SSH...")
        
        # Deploy test script
        if not self.ssh_client.deploy_script(eip_address, self._test_script, "/tmp/latency_test.py"):
            return None
        
        # Build test command with domains
        if self.domains:
            domains_args = " ".join(f'"{domain}"' for domain in self.domains)
            base_command = f"python3 /tmp/latency_test.py --domains {domains_args}"
        else:
            base_command = "python3 /tmp/latency_test.py"
        
        # Add IP list if provided
        if ip_list:
            # Deploy IP list as JSON file
            ip_list_json = json.dumps(ip_list)
            deploy_cmd = f"echo '{ip_list_json}' > /tmp/ip_list.json"
            stdout, stderr, code = self.ssh_client.run_command(eip_address, deploy_cmd)
            if code != 0:
                print(f"[ERROR] Failed to deploy IP list: {stderr}")
                return None
            
            # Run test with IP list
            test_command = f"{base_command} --ip-list /tmp/ip_list.json"
            print("[INFO] Using provided IP list for testing")
        else:
            # Run test in legacy mode (local DNS resolution)
            test_command = base_command
            print("[INFO] Using legacy mode with local DNS resolution")
        
        # Run the test script with progress display
        print(f"Executing latency tests (timeout: {self.test_timeout}s for {self.num_domains} domains)...")
        print(f"Timeout configuration: {self.timeout_per_domain}s per domain, {self.min_timeout}s minimum")
        print(f"Progress will be displayed below:")
        print("-" * 60)
        
        stdout, stderr, code = self.ssh_client.run_command_with_progress(
            eip_address, 
            test_command, 
            timeout=self.test_timeout
        )
        
        print("-" * 60)
        
        if code != 0:
            print(f"\n[ERROR] Test script failed with return code: {code}")
            if stderr:
                print(f"[ERROR] Last stderr output:\n{stderr}")
            
            # Try to parse partial results from stdout if available
            if stdout:
                try:
                    results = json.loads(stdout)
                    print("[INFO] Partial results were obtained despite the error")
                    return results
                except json.JSONDecodeError:
                    print("[ERROR] Could not parse partial results")
            
            return None
        
        # Parse JSON results
        try:
            results = json.loads(stdout)
            return results
        except json.JSONDecodeError as e:
            print(f"[ERROR] Failed to parse test results: {e}")
            print(f"Raw output: {stdout}")
            return None
    
    def display_results(self, results: Dict[str, Any], 
                       median_threshold: float, best_threshold: float) -> None:
        """Display test results.
        
        Args:
            results: Test results dict
            median_threshold: Median latency threshold
            best_threshold: Best latency threshold
        """
        print("\nLatency test results:")
        for hostname, host_data in results.items():
            if "error" in host_data:
                print(f"  {hostname}: {host_data['error']}")
                continue
            
            print(f"  {hostname}:")
            
            for ip, ip_data in host_data["ips"].items():
                median = ip_data.get("median", float("inf"))
                best = ip_data.get("best", float("inf"))
                avg = ip_data.get("average", float("inf"))
                p99 = ip_data.get("p99", float("inf"))
                max_val = ip_data.get("max", float("inf"))
                
                # Check if this IP meets criteria
                ip_passed = (median <= median_threshold) or (best <= best_threshold)
                
                print(f"    IP {ip:<15}  median={median:7.2f}  best={best:7.2f}  "
                      f"avg={avg:7.2f}  p99={p99:7.2f}  max={max_val:7.2f} µs  "
                      f"passed={ip_passed}")