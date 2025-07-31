"""Detailed JSONL format logging with complete per-IP statistics."""

import json
import os
from typing import Dict, Any


class DetailedJSONLLogger:
    """Handles detailed JSONL format logging with complete per-IP statistics."""
    
    def __init__(self, log_file: str):
        """Initialize detailed JSONL logger.
        
        Args:
            log_file: Path to detailed JSONL log file
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
                       instance_passed: bool, results: Dict[str, Any], 
                       median_threshold: float, best_threshold: float) -> None:
        """Log detailed test result with complete per-IP statistics.
        
        Args:
            timestamp: ISO format timestamp
            instance_id: EC2 instance ID
            instance_type: EC2 instance type
            instance_passed: Whether instance passed criteria
            results: Raw test results from latency test
            median_threshold: Median latency threshold in microseconds
            best_threshold: Best latency threshold in microseconds
        """
        detailed_entry = {
            "timestamp": timestamp,
            "instance_id": instance_id,
            "instance_type": instance_type,
            "passed": instance_passed,
            "domains": {}
        }
        
        # Process each domain
        for hostname, host_data in results.items():
            if "error" in host_data:
                # Skip domains with errors for now - could be added if needed
                continue
            
            detailed_entry["domains"][hostname] = {
                "ips": {}
            }
            
            # Process each IP for this domain
            for ip, ip_data in host_data.get("ips", {}).items():
                median = ip_data.get("median", float("inf"))
                best = ip_data.get("best", float("inf"))
                average = ip_data.get("average", float("inf"))
                p1 = ip_data.get("p1", float("inf"))
                p99 = ip_data.get("p99", float("inf"))
                max_val = ip_data.get("max", float("inf"))
                
                # Determine if this IP passed criteria
                ip_passed = (median <= median_threshold) or (best <= best_threshold)
                
                detailed_entry["domains"][hostname]["ips"][ip] = {
                    "median": round(median, 2) if median != float("inf") else None,
                    "best": round(best, 2) if best != float("inf") else None,
                    "average": round(average, 2) if average != float("inf") else None,
                    "p1": round(p1, 2) if p1 != float("inf") else None,
                    "p99": round(p99, 2) if p99 != float("inf") else None,
                    "max": round(max_val, 2) if max_val != float("inf") else None,
                    "passed": ip_passed
                }
        
        # Append to detailed JSONL file
        with open(self.log_file, "a") as f:
            json.dump(detailed_entry, f)
            f.write("\n")
            f.flush()  # Ensure data is written to disk