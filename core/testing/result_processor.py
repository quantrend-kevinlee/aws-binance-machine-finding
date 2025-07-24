"""Process and analyze latency test results."""

from typing import Dict, Any, Tuple

from ..utils import format_domain_short


class ResultProcessor:
    """Processes latency test results."""
    
    def __init__(self, median_threshold: float, best_threshold: float):
        """Initialize result processor.
        
        Args:
            median_threshold: Median latency threshold in microseconds
            best_threshold: Best latency threshold in microseconds
        """
        self.median_threshold = median_threshold
        self.best_threshold = best_threshold
    
    def process_results(self, results: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """Process test results and calculate statistics.
        
        Args:
            results: Raw test results from latency test
            
        Returns:
            Tuple of (domain_stats, instance_passed)
        """
        domain_stats = {}
        instance_passed = False
        
        for hostname, host_data in results.items():
            if "error" in host_data:
                continue
            
            # Initialize domain tracking
            domain_stats[hostname] = {
                "best_median": float("inf"),
                "best_best": float("inf"),
                "best_median_ip": "",
                "best_best_ip": ""
            }
            
            # Process each IP
            for ip, ip_data in host_data["ips"].items():
                median = ip_data["median"]
                best = ip_data["best"]
                
                # Check if this IP meets criteria
                ip_passed = (median <= self.median_threshold) or (best <= self.best_threshold)
                
                # Track best median for this domain
                if median < domain_stats[hostname]["best_median"]:
                    domain_stats[hostname]["best_median"] = median
                    domain_stats[hostname]["best_median_ip"] = ip
                
                # Track best "best" value for this domain
                if best < domain_stats[hostname]["best_best"]:
                    domain_stats[hostname]["best_best"] = best
                    domain_stats[hostname]["best_best_ip"] = ip
                
                # Instance passes if ANY IP meets criteria
                if ip_passed:
                    instance_passed = True
        
        return domain_stats, instance_passed
    
    def format_summary(self, instance_id: str, instance_type: str,
                      domain_stats: Dict[str, Any], instance_passed: bool) -> str:
        """Format result summary for display.
        
        Args:
            instance_id: EC2 instance ID
            instance_type: EC2 instance type
            domain_stats: Processed domain statistics
            instance_passed: Whether instance passed criteria
            
        Returns:
            Formatted summary string
        """
        lines = []
        
        # Show per-domain best results
        for hostname, stats in domain_stats.items():
            domain_short = format_domain_short(hostname)
            lines.append(
                f"  {domain_short}: median={stats['best_median']:.2f}µs "
                f"({stats['best_median_ip']}), best={stats['best_best']:.2f}µs "
                f"({stats['best_best_ip']})"
            )
        
        lines.append(f"  Passed: {instance_passed}")
        
        return "\n".join(lines)
    
    def format_anchor_report(self, instance_id: str, instance_type: str,
                           placement_group: str, availability_zone: str,
                           domain_stats: Dict[str, Any]) -> str:
        """Format anchor instance success report.
        
        Args:
            instance_id: EC2 instance ID
            instance_type: EC2 instance type
            placement_group: Placement group name
            availability_zone: AWS availability zone
            domain_stats: Processed domain statistics
            
        Returns:
            Formatted report string
        """
        lines = [
            f"*** Found anchor instance {instance_id} (type {instance_type}) "
            f"meeting latency criteria! ***"
        ]
        
        # Show per-domain results
        for hostname, stats in domain_stats.items():
            domain_short = format_domain_short(hostname)
            lines.append(
                f"{domain_short}: median={stats['best_median']:.2f}µs "
                f"({stats['best_median_ip']}), best={stats['best_best']:.2f}µs "
                f"({stats['best_best_ip']})"
            )
        
        # Write success report
        lines.extend([
            f"\nSuccessfully found anchor small instance!",
            f"- Instance ID: {instance_id}",
            f"- Instance Type: {instance_type}",
            f"- Placement Group: {placement_group} (AZ {availability_zone})",
            f"- Per-domain results:"
        ])
        
        for hostname, stats in domain_stats.items():
            domain_short = format_domain_short(hostname)
            lines.append(
                f"  - {domain_short}: median={stats['best_median']:.2f}µs "
                f"({stats['best_median_ip']}), best={stats['best_best']:.2f}µs "
                f"({stats['best_best_ip']})"
            )
        
        return "\n".join(lines)