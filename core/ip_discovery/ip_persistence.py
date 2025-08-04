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
        self.dead_file = os.path.join(self.ip_lists_dir, "ip_list_dead.jsonl")  # JSONL format
        
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
                self.active_data = {"last_updated": None, "last_validated": None, "domains": {}}
        else:
            self.active_data = {"last_updated": None, "last_validated": None, "domains": {}}
    
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
    
    def _append_dead_ip(self, domain: str, ip: str, metadata: Dict[str, Any]) -> None:
        """Append a single dead IP record to the JSONL file.
        
        Args:
            domain: Domain name
            ip: IP address
            metadata: Dead IP metadata
        """
        record = {
            "domain": domain,
            "ip": ip,
            "timestamp": datetime.now(UTC_PLUS_8).isoformat(),
            **metadata
        }
        
        # Append to JSONL file
        with open(self.dead_file, 'a') as f:
            f.write(json.dumps(record) + '\n')
    
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
        
        # Get or create IP entry with failure tracking
        ip_entry = ip_data["domains"][domain]["ips"].get(ip, {
            "first_seen": now,
            "consecutive_validation_failures": 0
        })
        
        # Ensure backward compatibility - add missing field to existing entries
        if "consecutive_validation_failures" not in ip_entry:
            ip_entry["consecutive_validation_failures"] = 0
        
        ip_data["domains"][domain]["ips"][ip] = ip_entry
        
        # If updating internal data, mark as dirty
        if ip_data is self.active_data:
            self.dirty = True
    
    def update_validation_timestamp(self, ip_data: Dict[str, Any]) -> None:
        """Update the global validation timestamp.
        
        Args:
            ip_data: IP data structure to update
        """
        ip_data["last_validated"] = datetime.now(UTC_PLUS_8).isoformat()
        
        # If updating internal data, mark as dirty
        if ip_data is self.active_data:
            self.dirty = True
    
    def remove_dead_ips(self, ip_data: Dict[str, Any], dead_ips: Dict[str, Set[str]], reason: str = "validation_failed") -> int:
        """Move specified IPs to dead history and remove from active list.
        
        This is called when IPs have exceeded the maximum consecutive failure threshold
        and are considered permanently unreachable.
        
        Args:
            ip_data: IP data structure to update
            dead_ips: Dictionary of domain -> set of IPs to remove
            reason: Reason for removal (e.g., "exceeded_6_consecutive_failures")
            
        Returns:
            Number of IPs moved to dead history
        """
        moved = 0
        now = datetime.now(UTC_PLUS_8).isoformat()
        
        with self.lock:
            for domain, domain_data in ip_data.get("domains", {}).items():
                ips = domain_data.get("ips", {})
                domain_dead_ips = dead_ips.get(domain, set())
                
                # Move each IP exceeding failure threshold to history
                for ip in domain_dead_ips:
                    if ip not in ips:
                        continue  # IP doesn't exist, skip
                        
                    ip_info = ips[ip]
                    # Calculate alive duration from first_seen to last global validation
                    first_seen = ip_info.get("first_seen", now)
                    last_validated = ip_data.get("last_validated", now)
                    try:
                        first_dt = datetime.fromisoformat(first_seen)
                        # Use global last_validated as the last known alive time
                        last_dt = datetime.fromisoformat(last_validated)
                        duration_hours = (last_dt - first_dt).total_seconds() / 3600
                    except:
                        duration_hours = 0
                    
                    # Append to removal history
                    metadata = {
                        "first_seen": first_seen,
                        "last_validated": last_validated,
                        "alive_duration_hours": round(duration_hours, 2),
                        "death_reason": reason
                    }
                    self._append_dead_ip(domain, ip, metadata)
                    
                    # Remove from active list
                    del ips[ip]
                    moved += 1
                    
                    # Mark data as dirty if it's internal
                    if ip_data is self.active_data:
                        self.dirty = True
        
        if moved > 0:
            print(f"[IP Discovery] Moved {moved} IPs exceeding failure threshold to {os.path.basename(self.dead_file)}")
        
        return moved
    
    def get_all_active_ips(self, ip_data: Dict[str, Any]) -> Dict[str, list]:
        """Get all IPs grouped by domain.
        
        Note: All IPs in the persistence file are returned, regardless of failure count.
        IPs are only removed when they exceed the configured failure threshold.
        
        Args:
            ip_data: IP data structure
            
        Returns:
            Dictionary of domain -> list of IPs
        """
        result = {}
        for domain, domain_data in ip_data.get("domains", {}).items():
            ips = list(domain_data.get("ips", {}).keys())
            if ips:
                result[domain] = ips
        return result
    
    def update_ip_failure_count(self, ip_data: Dict[str, Any], domain: str, ip: str, 
                              is_successful: bool) -> int:
        """Update IP consecutive failure count based on validation result.
        
        Args:
            ip_data: IP data structure to update
            domain: Domain name
            ip: IP address
            is_successful: Whether the validation was successful
            
        Returns:
            Current consecutive failure count after update
        """
        # Ensure IP exists
        if domain not in ip_data.get("domains", {}) or \
           ip not in ip_data["domains"][domain].get("ips", {}):
            # IP doesn't exist, create it first
            self.update_ip(ip_data, domain, ip)
        
        ip_entry = ip_data["domains"][domain]["ips"][ip]
        
        if is_successful:
            # Reset failure count on success
            ip_entry["consecutive_validation_failures"] = 0
        else:
            # Increment failure count
            ip_entry["consecutive_validation_failures"] = \
                ip_entry.get("consecutive_validation_failures", 0) + 1
        
        # If updating internal data, mark as dirty
        if ip_data is self.active_data:
            self.dirty = True
            
        return ip_entry["consecutive_validation_failures"]
    
    def get_ip_failure_counts(self, ip_data: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
        """Get failure counts for all IPs.
        
        Args:
            ip_data: IP data structure
            
        Returns:
            Dictionary of domain -> {ip -> failure_count}
        """
        result = {}
        for domain, domain_data in ip_data.get("domains", {}).items():
            domain_counts = {}
            for ip, ip_info in domain_data.get("ips", {}).items():
                domain_counts[ip] = ip_info.get("consecutive_validation_failures", 0)
            if domain_counts:
                result[domain] = domain_counts
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
                    print(f"[IP Discovery] Verified {total_ips} active IPs saved to disk")
            except Exception as e:
                print(f"[ERROR] Failed to verify active IP file: {e}")
        
        if os.path.exists(self.dead_file):
            try:
                with open(self.dead_file, 'r') as f:
                    line_count = sum(1 for line in f if line.strip())
                    print(f"[IP Discovery] Verified {line_count} removed IP records saved to disk")
            except Exception as e:
                print(f"[ERROR] Failed to verify dead IP file: {e}")
        
        print("[IP Discovery] Shutdown complete")