"""JSONL format logging for the latency finder."""

import json
import os
from typing import Dict, Any


class JSONLLogger:
    """Handles JSONL format logging."""
    
    def __init__(self, log_file: str):
        """Initialize JSONL logger.
        
        Args:
            log_file: Path to JSONL log file
        """
        self.log_file = log_file
        self._ensure_file_exists()
    
    def _ensure_file_exists(self) -> None:
        """Ensure log file exists."""
        if not os.path.exists(self.log_file):
            # Just touch the file to create it
            with open(self.log_file, "w") as f:
                pass
    
    def log_test_result(self, timestamp: str, instance_id: str, instance_type: str,
                       instance_passed: bool, domain_stats: Dict[str, Any],
                       ip_mode: str, public_ip: str) -> None:
        """Log test result in JSONL format.
        
        Args:
            timestamp: ISO format timestamp
            instance_id: EC2 instance ID
            instance_type: EC2 instance type
            instance_passed: Whether instance passed criteria
            domain_stats: Domain statistics with best values
            ip_mode: IP assignment mode ('eip' or 'auto-assigned')
            public_ip: The public IP address of the instance
        """
        jsonl_entry = {
            "timestamp": timestamp,
            "instance_id": instance_id,
            "instance_type": instance_type,
            "ip_mode": ip_mode,
            "public_ip": public_ip,
            "passed": instance_passed,
            "domains": {}
        }
        
        # Add domain-specific data
        for domain, stats in domain_stats.items():
            if stats["best_median"] < float("inf"):  # Only add domains with valid data
                jsonl_entry["domains"][domain] = {
                    "median": round(stats["best_median"], 2),
                    "best": round(stats["best_best"], 2),
                    "median_ip": stats["best_median_ip"],
                    "best_ip": stats["best_best_ip"]
                }
        
        # Append to JSONL file
        with open(self.log_file, "a") as f:
            json.dump(jsonl_entry, f)
            f.write("\n")
            f.flush()  # Ensure data is written to disk