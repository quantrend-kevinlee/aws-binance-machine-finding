"""IP list persistence management for the latency finder."""

import json
import os
import sys
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
        self.dead_ips_file = os.path.join(self.ip_lists_dir, "dead_ips.json")
        
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
                    
                # Validate format
                if not isinstance(self.active_data, dict) or 'domains' not in self.active_data:
                    print(f"[ERROR] Invalid IP list format in {self.latest_file}")
                    print("[ERROR] Expected format: {'domains': {domain: {'ips': {ip: {...}}}}")
                    sys.exit(1)
                    
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
            for ip_info in domain_data["ips"].values():
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
            ip_data["domains"][domain] = {"count": 0, "ips": {}}
        
        now = datetime.now(UTC_PLUS_8).isoformat()
        
        # Get or create IP entry with time-based tracking
        if ip not in ip_data["domains"][domain]["ips"]:
            ip_data["domains"][domain]["ips"][ip] = {
                "first_seen": now,
                "last_validated": now
            }
            # Update count
            ip_data["domains"][domain]["count"] = len(ip_data["domains"][domain]["ips"])
        
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
        if domain not in ip_data["domains"] or ip not in ip_data["domains"][domain]["ips"]:
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
            
            for ip, ip_info in domain_data["ips"].items():
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
    
    def move_dead_ips_to_history(self) -> None:
        """Move dead IPs from active list to dead IP file."""
        with self.lock:
            current_time = datetime.now(UTC_PLUS_8)
            dead_threshold_seconds = 3600  # 1 hour
            
            # Load existing dead IPs
            dead_ips = {}
            if os.path.exists(self.dead_ips_file):
                try:
                    with open(self.dead_ips_file, 'r') as f:
                        dead_data = json.load(f)
                        dead_ips = dead_data.get("ips", {})
                except:
                    dead_ips = {}
            
            # Find and move dead IPs
            moved_count = 0
            for domain, domain_data in self.active_data.get("domains", {}).items():
                ips_to_remove = []
                
                for ip, ip_info in domain_data["ips"].items():
                    last_validated = ip_info.get("last_validated")
                    if last_validated:
                        try:
                            last_validated_dt = datetime.fromisoformat(last_validated)
                            time_since_validation = (current_time - last_validated_dt).total_seconds()
                            
                            if time_since_validation > dead_threshold_seconds:
                                # Calculate lifespan
                                first_seen = ip_info.get("first_seen")
                                lifespan_hours = None
                                if first_seen:
                                    try:
                                        first_seen_dt = datetime.fromisoformat(first_seen)
                                        lifespan = last_validated_dt - first_seen_dt
                                        lifespan_hours = lifespan.total_seconds() / 3600
                                    except:
                                        pass
                                
                                # Add to dead IPs
                                dead_key = f"{domain}:{ip}"
                                dead_ips[dead_key] = {
                                    "domain": domain,
                                    "ip": ip,
                                    "first_seen": first_seen,
                                    "last_validated": last_validated,
                                    "declared_dead": current_time.isoformat(),
                                    "lifespan_hours": round(lifespan_hours, 2) if lifespan_hours else None
                                }
                                ips_to_remove.append(ip)
                                moved_count += 1
                        except:
                            pass
                
                # Remove dead IPs from active list
                for ip in ips_to_remove:
                    del self.active_data["domains"][domain]["ips"][ip]
                    self.dirty = True
                
                # Update count for this domain
                if domain in self.active_data["domains"]:
                    self.active_data["domains"][domain]["count"] = len(self.active_data["domains"][domain]["ips"])
            
            # Save dead IPs file if any were moved
            if moved_count > 0:
                dead_data = {
                    "last_updated": current_time.isoformat(),
                    "total_count": len(dead_ips),
                    "ips": dead_ips
                }
                
                # Atomic write
                temp_fd, temp_path = tempfile.mkstemp(dir=self.ip_lists_dir, text=True)
                try:
                    with os.fdopen(temp_fd, 'w') as f:
                        json.dump(dead_data, f, indent=2)
                    os.replace(temp_path, self.dead_ips_file)
                    print(f"[IP Persistence] Moved {moved_count} dead IPs to dead_ips.json")
                except Exception as e:
                    try:
                        os.unlink(temp_path)
                    except:
                        pass
                    print(f"[ERROR] Failed to save dead IPs: {e}")
    
    def shutdown(self) -> None:
        """Gracefully shutdown and ensure all data is synced to disk."""
        # Move dead IPs before final sync
        self.move_dead_ips_to_history()
        
        with self.lock:
            if self.dirty:
                print("[IP Discovery] Syncing final changes to disk...")
                self._sync_active_ips()
        
        # Verify files exist
        if os.path.exists(self.latest_file):
            try:
                with open(self.latest_file, 'r') as f:
                    data = json.load(f)
                    total_ips = sum(len(d["ips"]) for d in data["domains"].values())
                    print(f"[IP Discovery] Verified {total_ips} active IPs saved to disk")
            except Exception as e:
                print(f"[ERROR] Failed to verify IP file: {e}")
        
        print("[IP Discovery] Shutdown complete")