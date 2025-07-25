"""IP list persistence management for DC Machine."""

import json
import os
from datetime import datetime
from typing import Dict, Any, Optional
from ..constants import UTC_PLUS_8
from ..utils import ensure_directory_exists


class IPPersistence:
    """Manages loading and saving IP lists to disk."""
    
    def __init__(self, report_dir: str):
        """Initialize IP persistence manager.
        
        Args:
            report_dir: Base directory for reports
        """
        self.report_dir = report_dir
        self.ip_lists_dir = os.path.join(report_dir, "ip_lists")
        ensure_directory_exists(self.ip_lists_dir)
        self.latest_file = os.path.join(self.ip_lists_dir, "ip_list_latest.json")
    
    def load_latest(self) -> Dict[str, Any]:
        """Load the latest IP list from disk.
        
        Returns:
            Dictionary with IP data or empty structure if file doesn't exist
        """
        if os.path.exists(self.latest_file):
            try:
                with open(self.latest_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"[WARN] Failed to load IP list: {e}")
        
        # Return empty structure
        return {
            "last_updated": None,
            "domains": {}
        }
    
    def save(self, ip_data: Dict[str, Any], create_snapshot: bool = False) -> None:
        """Save IP list to disk.
        
        Args:
            ip_data: IP data to save
            create_snapshot: Whether to also save a dated snapshot
        """
        # Update timestamp
        ip_data["last_updated"] = datetime.now(UTC_PLUS_8).isoformat()
        
        # Count active IPs for logging
        total_ips = 0
        active_ips = 0
        for domain_data in ip_data.get("domains", {}).values():
            for ip_info in domain_data.get("ips", {}).values():
                total_ips += 1
                if ip_info.get("alive", True):
                    active_ips += 1
        
        # Save to latest file
        with open(self.latest_file, 'w') as f:
            json.dump(ip_data, f, indent=2)
        
        if active_ips < total_ips:
            print(f"[IP Discovery] Saved {active_ips} active IPs ({total_ips - active_ips} dead) to {os.path.basename(self.latest_file)}")
        else:
            print(f"[IP Discovery] Saved {active_ips} active IPs to {os.path.basename(self.latest_file)}")
        
        # Create snapshot if requested
        if create_snapshot:
            # Use date-only naming for daily snapshots
            snapshot_file = os.path.join(
                self.ip_lists_dir,
                f"ip_list_{datetime.now(UTC_PLUS_8).strftime('%Y-%m-%d')}.json"
            )
            # Only create if it doesn't exist or has different content
            create_new_snapshot = True
            if os.path.exists(snapshot_file):
                # Check if content is different
                with open(snapshot_file, 'r') as f:
                    existing_data = json.load(f)
                if existing_data == ip_data:
                    create_new_snapshot = False
            
            if create_new_snapshot:
                with open(snapshot_file, 'w') as f:
                    json.dump(ip_data, f, indent=2)
                print(f"[IP Discovery] Created/updated daily snapshot: {os.path.basename(snapshot_file)}")
            else:
                print(f"[IP Discovery] Daily snapshot already up-to-date: {os.path.basename(snapshot_file)}")
    
    def get_domain_ips(self, ip_data: Dict[str, Any], domain: str) -> Dict[str, Dict[str, Any]]:
        """Get IPs for a specific domain.
        
        Args:
            ip_data: IP data structure
            domain: Domain name
            
        Returns:
            Dictionary of IP -> metadata
        """
        return ip_data.get("domains", {}).get(domain, {}).get("ips", {})
    
    def update_ip(self, ip_data: Dict[str, Any], domain: str, ip: str, 
                  alive: Optional[bool] = None, validated: bool = False) -> None:
        """Update IP metadata.
        
        Args:
            ip_data: IP data structure to update
            domain: Domain name
            ip: IP address
            alive: Whether IP is alive (None to keep current)
            validated: Whether this is from a validation check
        """
        # Ensure structure exists
        if "domains" not in ip_data:
            ip_data["domains"] = {}
        if domain not in ip_data["domains"]:
            ip_data["domains"][domain] = {"ips": {}}
        if "ips" not in ip_data["domains"][domain]:
            ip_data["domains"][domain]["ips"] = {}
        
        now = datetime.now(UTC_PLUS_8).isoformat()
        
        # Get or create IP entry
        ip_entry = ip_data["domains"][domain]["ips"].get(ip, {
            "first_seen": now,
            "last_seen": now,
            "last_validated": None,
            "alive": True
        })
        
        # Update fields
        ip_entry["last_seen"] = now
        if validated:
            ip_entry["last_validated"] = now
        if alive is not None:
            ip_entry["alive"] = alive
        
        ip_data["domains"][domain]["ips"][ip] = ip_entry
    
    def remove_dead_ips(self, ip_data: Dict[str, Any]) -> int:
        """Remove IPs marked as dead from all domains.
        
        Args:
            ip_data: IP data structure to update
            
        Returns:
            Number of IPs removed
        """
        removed = 0
        for domain, domain_data in ip_data.get("domains", {}).items():
            ips = domain_data.get("ips", {})
            dead_ips = [ip for ip, data in ips.items() if not data.get("alive", True)]
            for ip in dead_ips:
                del ips[ip]
                removed += 1
        return removed
    
    def get_all_active_ips(self, ip_data: Dict[str, Any]) -> Dict[str, list]:
        """Get all active IPs grouped by domain.
        
        Args:
            ip_data: IP data structure
            
        Returns:
            Dictionary of domain -> list of active IPs
        """
        result = {}
        for domain, domain_data in ip_data.get("domains", {}).items():
            active_ips = [
                ip for ip, data in domain_data.get("ips", {}).items()
                if data.get("alive", True)
            ]
            if active_ips:
                result[domain] = active_ips
        return result