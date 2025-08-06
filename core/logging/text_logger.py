"""Text format logging for the latency finder."""

import os
from typing import Dict, Any

from ..utils import format_domain_short


class TextLogger:
    """Handles text format logging."""
    
    def __init__(self, log_file: str):
        """Initialize text logger.
        
        Args:
            log_file: Path to text log file
        """
        self.log_file = log_file
    
    def _ensure_file_exists(self) -> None:
        """Ensure log file and directory exist."""
        if not os.path.exists(self.log_file):
            # Create directory if needed
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
    
    def log_test_result(self, timestamp: str, instance_id: str, instance_type: str,
                       instance_passed: bool, domain_stats: Dict[str, Any],
                       results: Dict[str, Any], median_threshold: float,
                       best_threshold: float, ip_mode: str, public_ip: str) -> None:
        """Log detailed test result in text format.
        
        Args:
            timestamp: ISO format timestamp
            instance_id: EC2 instance ID
            instance_type: EC2 instance type
            instance_passed: Whether instance passed criteria
            domain_stats: Domain statistics with best values
            results: Raw test results
            median_threshold: Median latency threshold
            best_threshold: Best latency threshold
            ip_mode: IP assignment mode ('eip' or 'auto-assigned')
            public_ip: The public IP address of the instance
        """
        # Ensure directory exists before writing
        self._ensure_file_exists()
        
        with open(self.log_file, "a") as f:
            # Write summary line
            f.write(f"[{timestamp}] Instance: {instance_id} ({instance_type})\n")
            f.write(f"IP Mode: {'EIP' if ip_mode == 'eip' else 'Auto-assigned'} ({public_ip})\n")
            f.write(f"Status: {'PASSED' if instance_passed else 'FAILED'}\n\n")
            
            # Write per-domain best results
            for hostname, stats in domain_stats.items():
                domain_short = format_domain_short(hostname)
                f.write(f"  {domain_short}: median={stats['best_median']:.2f}µs "
                       f"({stats['best_median_ip']}), best={stats['best_best']:.2f}µs "
                       f"({stats['best_best_ip']})\n")
            
            f.write(f"  Passed: {instance_passed}\n")
            
            # Write detailed test results
            f.write("\nLatency test results:\n")
            for hostname, host_data in results.items():
                if "error" in host_data:
                    f.write(f"  {hostname}: {host_data['error']}\n")
                    continue
                
                f.write(f"  {hostname}:\n")
                
                for ip, ip_data in host_data["ips"].items():
                    median = ip_data.get("median", float("inf"))
                    best = ip_data.get("best", float("inf"))
                    avg = ip_data.get("average", float("inf"))
                    p1 = ip_data.get("p1", float("inf"))
                    p99 = ip_data.get("p99", float("inf"))
                    max_val = ip_data.get("max", float("inf"))
                    ip_passed = (median <= median_threshold) or (best <= best_threshold)
                    f.write(f"    IP {ip:<15}  median={median:7.2f}  best={best:7.2f}  "
                           f"avg={avg:7.2f}  p1={p1:7.2f}  p99={p99:7.2f}  "
                           f"max={max_val:7.2f} µs  passed={ip_passed}\n")
            
            # Add separator between instances
            f.write("\n" + "="*80 + "\n\n")