"""Champion event logging for DC Machine."""

import os
from typing import Optional, Dict, Any

from ..utils import get_current_timestamp


class ChampionEventLogger:
    """Logs champion events to dedicated log file."""
    
    def __init__(self, log_file: str):
        """Initialize champion event logger.
        
        Args:
            log_file: Path to champion log file
        """
        self.log_file = log_file
    
    def log_event(self, domain: str, instance_id: str, instance_type: str,
                  median_latency: float, best_latency: float, ip: str,
                  placement_group: str, old_champion: Optional[Dict[str, Any]] = None) -> None:
        """Log a champion event.
        
        Args:
            domain: Domain name
            instance_id: New champion instance ID
            instance_type: Instance type
            median_latency: Median latency in microseconds
            best_latency: Best latency in microseconds
            ip: Optimal IP address
            placement_group: Placement group name
            old_champion: Old champion info (if replaced)
        """
        timestamp = get_current_timestamp()
        
        with open(self.log_file, "a") as f:
            f.write(f"\n{timestamp}\n")
            f.write(f"  Domain: {domain}\n")
            f.write(f"  New Champion: {instance_id} ({instance_type})\n")
            f.write(f"  Median Latency: {median_latency:.2f}µs\n")
            f.write(f"  Best Latency: {best_latency:.2f}µs\n")
            f.write(f"  Optimal IP: {ip}\n")
            f.write(f"  Placement Group: {placement_group}\n")
            if old_champion:
                f.write(f"  Replaced: {old_champion['instance_id']} "
                       f"(median: {old_champion['median_latency']:.2f}µs)\n")
            f.write("-" * 80 + "\n")
    
    def format_champion_summary(self, champions: Dict[str, Any], 
                               eip_allocation_id: str, key_path: str) -> str:
        """Format champion summary for display.
        
        Args:
            champions: Champion instances by domain
            eip_allocation_id: EIP allocation ID
            key_path: SSH key path
            
        Returns:
            Formatted summary string
        """
        if not champions:
            return "\n[WARN] No champions found during this session."
        
        lines = ["\n[CHAMPION] Current Domain Champions:"]
        
        # Group champions by instance to show multi-domain champions
        instance_domains = {}
        for domain, info in champions.items():
            instance_id = info.get("instance_id")
            if instance_id:
                if instance_id not in instance_domains:
                    instance_domains[instance_id] = {
                        "domains": [],
                        "info": info
                    }
                instance_domains[instance_id]["domains"].append(domain)
        
        # Display champions grouped by instance
        for instance_id, data in instance_domains.items():
            domains = data["domains"]
            info = data["info"]
            
            lines.extend([
                f"\n   Instance: {instance_id} ({info.get('instance_type', 'N/A')})",
                f"   Placement Group: {info.get('placement_group', 'N/A')}",
                f"   Champions for: {', '.join(domains)}",
                f"   Status: [PROTECTED] PROTECTED - Will persist after script termination"
            ])
            
            # Show latency details for each domain this instance champions
            for domain in domains:
                domain_info = champions[domain]
                domain_short = domain.replace(".binance.com", "")
                lines.append(
                    f"     {domain_short}: "
                    f"median={domain_info.get('median_latency', 'N/A'):.2f}µs, "
                    f"best={domain_info.get('best_latency', 'N/A'):.2f}µs "
                    f"({domain_info.get('ip', 'N/A')})"
                )
        
        # Add access instructions
        lines.extend([
            f"\n   [INFO] Champion Access Instructions:",
            f"   1. To SSH to any champion: aws ec2 associate-address "
            f"--instance-id <INSTANCE_ID> --allocation-id {eip_allocation_id}",
            f"   2. Then SSH to EIP address with key: {key_path}",
            f"   3. For production, use optimal IPs for each service:"
        ])
        
        # Show optimal IPs for production use
        for domain, info in champions.items():
            domain_short = domain.replace(".binance.com", "")
            lines.append(f"      {domain_short}: {info.get('ip', 'N/A')}")
        
        lines.extend([
            f"\n   [SAVE] Champion state persisted in: {os.path.dirname(self.log_file)}/champion_state.json",
            f"   [LOG] Champion log available at: {self.log_file}"
        ])
        
        return "\n".join(lines)