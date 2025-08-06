"""Latency test execution for the latency finder."""

import json
import os
from typing import Dict, Any, Optional

from .ssh_client import SSHClient
from .file_deployment import create_file_deployer


class LatencyTestRunner:
    """Runs latency tests on EC2 instances."""
    
    def __init__(self, ssh_client: SSHClient, domains: list = None, tcp_timeout_ms: int = 3000):
        """Initialize latency test runner.
        
        Args:
            ssh_client: SSH client instance
            domains: List of domains to test
            tcp_timeout_ms: TCP connection timeout in milliseconds for each connection
        """
        self.ssh_client = ssh_client
        self._test_script = None
        self.domains = domains or []
        self.tcp_timeout_ms = tcp_timeout_ms
        # Use large SSH timeout as safety net (30 minutes)
        self.ssh_timeout = 1800
        
        # Initialize file deployer for reliable file operations
        self.file_deployer = create_file_deployer(ssh_client.key_path)
    
    def load_test_script(self, script_path: str = "binance_latency_test.py") -> None:
        """Load the latency test script from core/testing directory.
        
        Args:
            script_path: Path to test script file (relative to this module)
        """
        full_path = os.path.join(os.path.dirname(__file__), script_path)
        with open(full_path, "r") as f:
            self._test_script = f.read()
    
    def run_latency_test(self, public_ip: str, ip_list: Optional[Dict[str, list]] = None) -> Optional[Dict[str, Any]]:
        """Run latency test on instance and return results.
        
        Args:
            public_ip: Public IP address of instance
            ip_list: Optional dict of domain -> list of IPs to test
            
        Returns:
            Test results dict or None on failure
        """
        if not self._test_script:
            print("[ERROR] Test script not loaded")
            return None
        
        print("Running latency test via SSH...")
        
        # Deploy test script using reliable SCP-based method
        if not self.file_deployer.deploy_script_file(public_ip, self._test_script, "/tmp/latency_test.py"):
            print("[ERROR] Failed to deploy test script via SCP")
            return None
        
        print("[INFO] Test script deployed successfully via SCP")
        
        # Build test command with domains
        if self.domains:
            domains_args = " ".join(f'"{domain}"' for domain in self.domains)
            base_command = f"python3 /tmp/latency_test.py --domains {domains_args}"
        else:
            base_command = "python3 /tmp/latency_test.py"
        
        # Add IP list if provided
        if ip_list:
            # Deploy IP list using reliable SCP-based method
            if not self.file_deployer.deploy_ip_list(public_ip, ip_list, "/tmp/ip_list.json"):
                print("[ERROR] Failed to deploy IP list via SCP")
                return None
            
            # Run test with IP list and TCP timeout
            test_command = f"{base_command} --ip-list /tmp/ip_list.json --tcp-timeout-ms {self.tcp_timeout_ms}"
            print("[INFO] Using provided IP list for testing (deployed via SCP)")
        else:
            # Run test in legacy mode (local DNS resolution) with TCP timeout
            test_command = f"{base_command} --tcp-timeout-ms {self.tcp_timeout_ms}"
            print("[INFO] Using legacy mode with local DNS resolution")
        
        # Run the test script with progress display
        print(f"Executing latency tests with {self.tcp_timeout_ms}ms TCP timeout...")
        print(f"SSH timeout: {self.ssh_timeout}s (safety net)")
        print(f"Progress will be displayed below:")
        print("-" * 60)
        
        stdout, stderr, code = self.ssh_client.run_command_with_progress(
            public_ip, 
            test_command, 
            timeout=self.ssh_timeout
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