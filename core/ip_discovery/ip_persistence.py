"""IP list persistence management for the latency finder."""

import json
import os
import tempfile
import threading
from datetime import datetime
from typing import Dict, Any, Set
from ..constants import UTC_PLUS_8
from ..utils import ensure_directory_exists


class IPPersistence:
    """Manages loading and saving IP lists to disk."""
    
    def __init__(self, ip_list_dir: str):
        """Initialize IP persistence manager.
        
        Args:
            ip_list_dir: Directory for IP list files
        """
        self.ip_lists_dir = ip_list_dir
        ensure_directory_exists(self.ip_lists_dir)
        self.latest_file = os.path.join(self.ip_lists_dir, "ip_list_latest.json")
        
        # In-memory state
        self.active_data = None  # Loaded from disk
        self.dirty = False  # Track if active data needs saving
        self.lock = threading.Lock()  # Thread safety for background updates
        
        # Load initial state
        self._load_initial_state()
    
    def _load_initial_state(self) -> None:
        """Load initial state from disk files."""
        # Load active IPs
        if os.path.exists(self.latest_file):
            try:
                with open(self.latest_file, 'r') as f:
                    self.active_data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"[WARN] Failed to load IP list: {e}")
                self.active_data = {"last_updated": None, "domains": {}}
        else:
            self.active_data = {"last_updated": None, "domains": {}}
    
    def load_latest(self) -> Dict[str, Any]:
        """Get the current active IP data.
        
        Returns:
            Dictionary with IP data (returns in-memory copy)
        """
        with self.lock:
            # Return a deep copy to prevent external modifications
            return json.loads(json.dumps(self.active_data))
    
    def save(self, ip_data: Dict[str, Any]) -> None:
        """Update in-memory data and mark as dirty.
        
        Args:
            ip_data: IP data to save
        """
        with self.lock:
            # Update in-memory data
            self.active_data = ip_data
            self.active_data["last_updated"] = datetime.now(UTC_PLUS_8).isoformat()
            self.dirty = True
    
    def save_and_sync(self, ip_data: Dict[str, Any]) -> None:
        """Update in-memory data and immediately sync to disk.
        
        This is a convenience method that combines save() and sync_to_disk()
        for critical updates that should be persisted immediately.
        
        Args:
            ip_data: IP data to save
        """
        self.save(ip_data)
        self.sync_to_disk()
    
    def _sync_active_ips(self) -> None:
        """Sync active IPs to disk if dirty (must be called with lock held)."""
        if not self.dirty:
            return
            
        # Count active IPs for logging
        domain_counts = {}
        total_ips = 0
        active_ips = 0
        for domain_name, domain_data in self.active_data.get("domains", {}).items():
            domain_active = 0
            domain_total = 0
            for ip_info in domain_data.get("ips", {}).values():
                domain_total += 1
                total_ips += 1
                domain_active += 1
                active_ips += 1
            domain_counts[domain_name] = (domain_active, domain_total)
        
        # Atomic write: write to temp file then rename
        temp_fd, temp_path = tempfile.mkstemp(dir=self.ip_lists_dir, text=True)
        try:
            with os.fdopen(temp_fd, 'w') as f:
                json.dump(self.active_data, f, indent=2)
            # Atomic rename
            os.replace(temp_path, self.latest_file)
            self.dirty = False
            
            # More detailed logging
            print(f"\n[IP Persistence] Saved to {os.path.basename(self.latest_file)}:")
            for domain, (active, total) in sorted(domain_counts.items()):
                print(f"  - {domain}: {active} IPs")
            print(f"[IP Persistence] Total: {active_ips} IPs across {len(domain_counts)} domains")
        except Exception as e:
            # Clean up temp file on error
            try:
                os.unlink(temp_path)
            except:
                pass
            raise e
    
    def sync_to_disk(self) -> None:
        """Force sync of active IPs to disk if needed."""
        with self.lock:
            self._sync_active_ips()
    
    def get_domain_ips(self, ip_data: Dict[str, Any], domain: str) -> Dict[str, Dict[str, Any]]:
        """Get IPs for a specific domain.
        
        Args:
            ip_data: IP data structure
            domain: Domain name
            
        Returns:
            Dictionary of IP -> metadata
        """
        return ip_data.get("domains", {}).get(domain, {}).get("ips", {})
    
    def update_ip(self, ip_data: Dict[str, Any], domain: str, ip: str) -> None:
        """Update IP metadata (for new IP discovery).
        
        Args:
            ip_data: IP data structure to update (can be external or internal)
            domain: Domain name
            ip: IP address
        """
        # Ensure structure exists
        if "domains" not in ip_data:
            ip_data["domains"] = {}
        if domain not in ip_data["domains"]:
            ip_data["domains"][domain] = {"ips": {}}
        if "ips" not in ip_data["domains"][domain]:
            ip_data["domains"][domain]["ips"] = {}
        
        now = datetime.now(UTC_PLUS_8).isoformat()
        
        # Get or create IP entry with time-based tracking
        ip_entry = ip_data["domains"][domain]["ips"].get(ip, {
            "first_seen": now,
            "last_validated": now  # Initialize with current time for new IPs
        })
        
        # Ensure new field exists for migration compatibility
        if "last_validated" not in ip_entry:
            ip_entry["last_validated"] = ip_entry.get("first_seen", now)
        
        ip_data["domains"][domain]["ips"][ip] = ip_entry
        
        # If updating internal data, mark as dirty
        if ip_data is self.active_data:
            self.dirty = True
    
    def update_ip_validation_time(self, ip_data: Dict[str, Any], domain: str, ip: str) -> None:
        """Update the validation timestamp for a specific IP.
        
        This is called when an IP successfully responds to validation.
        
        Args:
            ip_data: IP data structure to update
            domain: Domain name
            ip: IP address that was successfully validated
        """
        # Ensure IP exists
        if domain not in ip_data.get("domains", {}) or \
           ip not in ip_data["domains"][domain].get("ips", {}):
            # IP doesn't exist, create it first
            self.update_ip(ip_data, domain, ip)
        
        # Update last_validated timestamp
        ip_data["domains"][domain]["ips"][ip]["last_validated"] = datetime.now(UTC_PLUS_8).isoformat()
        
        # If updating internal data, mark as dirty
        if ip_data is self.active_data:
            self.dirty = True
    
    def get_all_active_ips(self, ip_data: Dict[str, Any], include_dead: bool = False) -> Dict[str, list]:
        """Get all active IPs grouped by domain.
        
        IPs are considered dead if they haven't been validated for over 1 hour.
        
        Args:
            ip_data: IP data structure
            include_dead: If True, include all IPs regardless of validation time
            
        Returns:
            Dictionary of domain -> list of IPs (only live IPs unless include_dead=True)
        """
        result = {}
        current_time = datetime.now(UTC_PLUS_8)
        dead_threshold_seconds = 3600  # 1 hour
        
        for domain, domain_data in ip_data.get("domains", {}).items():
            domain_ips = []
            
            for ip, ip_info in domain_data.get("ips", {}).items():
                if include_dead:
                    domain_ips.append(ip)
                else:
                    # Check if IP is still alive based on last validation time
                    last_validated = ip_info.get("last_validated")
                    if last_validated:
                        try:
                            last_validated_dt = datetime.fromisoformat(last_validated)
                            time_since_validation = (current_time - last_validated_dt).total_seconds()
                            
                            if time_since_validation <= dead_threshold_seconds:
                                domain_ips.append(ip)
                        except:
                            # If parsing fails, include the IP
                            domain_ips.append(ip)
                    else:
                        # No validation timestamp, include the IP
                        domain_ips.append(ip)
            
            if domain_ips:
                result[domain] = domain_ips
                
        return result
    
    def shutdown(self) -> None:
        """Gracefully shutdown and ensure all data is synced to disk."""
        with self.lock:
            if self.dirty:
                print("[IP Discovery] Syncing final changes to disk...")
                self._sync_active_ips()
        
        # Verify files exist
        if os.path.exists(self.latest_file):
            try:
                with open(self.latest_file, 'r') as f:
                    data = json.load(f)
                    total_ips = sum(len(d.get("ips", {})) for d in data.get("domains", {}).values())
                    # Count live vs dead IPs
                    active_ips_data = self.get_all_active_ips(data, include_dead=False)
                    live_count = sum(len(ips) for ips in active_ips_data.values())
                    dead_count = total_ips - live_count
                    
                    print(f"[IP Discovery] Verified {total_ips} total IPs saved to disk ({live_count} live, {dead_count} dead)")
            except Exception as e:
                print(f"[ERROR] Failed to verify IP file: {e}")
        
        print("[IP Discovery] Shutdown complete")