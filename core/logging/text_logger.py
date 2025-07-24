"""Text format logging for DC Machine."""

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
    
    def log_test_result(self, timestamp: str, instance_id: str, instance_type: str,
                       instance_passed: bool, domain_stats: Dict[str, Any],
                       results: Dict[str, Any], median_threshold: float,
                       best_threshold: float) -> None:
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
        """
        with open(self.log_file, "a") as f:
            # Write summary line
            f.write(f"[{timestamp}] {instance_id}  {instance_type}\n")
            
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
                    median = ip_data["median"]
                    best = ip_data["best"]
                    ip_passed = (median <= median_threshold) or (best <= best_threshold)
                    f.write(f"    IP {ip:<15}  median={median:9.2f} µs  "
                           f"best={best:9.2f} µs  passed={ip_passed}\n")
            
            # Add separator between instances
            f.write("\n" + "="*80 + "\n\n")