"""IP collection through DNS queries for DC Machine."""

import subprocess
import time
import threading
from datetime import datetime
from typing import Dict, List, Set, Optional
from ..constants import UTC_PLUS_8


class IPCollector:
    """Collects IPs through periodic DNS queries."""
    
    def __init__(self, domains: List[str], queries_per_batch: int = 5, 
                 batch_interval: int = 60, dns_timeout: int = 10):
        """Initialize IP collector.
        
        Args:
            domains: List of domains to query
            queries_per_batch: Number of queries per domain in each batch
            batch_interval: Seconds to wait between batches (to bypass DNS cache)
            dns_timeout: Timeout for DNS queries in seconds
        """
        self.domains = domains
        self.queries_per_batch = queries_per_batch
        self.batch_interval = batch_interval
        self.dns_timeout = dns_timeout
        self.running = False
        self.thread = None
        self.collected_ips: Dict[str, Set[str]] = {domain: set() for domain in domains}
        self.lock = threading.Lock()
    
    def resolve_domain(self, domain: str) -> List[str]:
        """Resolve a domain to its IP addresses.
        
        Args:
            domain: Domain name to resolve
            
        Returns:
            List of IP addresses
        """
        ips = []
        try:
            result = subprocess.run(
                ["host", domain],
                capture_output=True,
                text=True,
                timeout=self.dns_timeout
            )
            
            for line in result.stdout.splitlines():
                parts = line.split()
                if "has address" in line and len(parts) >= 4:
                    ip = parts[-1]
                    # Basic IP validation
                    if '.' in ip and ip.count('.') == 3:
                        try:
                            octets = ip.split('.')
                            if all(0 <= int(octet) <= 255 for octet in octets):
                                ips.append(ip)
                        except ValueError:
                            pass
        except subprocess.TimeoutExpired:
            print(f"[WARN] DNS query timeout for {domain}")
        except Exception as e:
            print(f"[WARN] DNS query failed for {domain}: {e}")
        
        return ips
    
    def collect_batch(self) -> Dict[str, Set[str]]:
        """Perform one batch of DNS queries.
        
        Returns:
            Dictionary of domain -> set of new IPs found
        """
        new_ips = {domain: set() for domain in self.domains}
        
        # Silently query DNS
        
        for domain in self.domains:
            domain_new_ips = set()
            
            # Perform multiple queries
            for i in range(self.queries_per_batch):
                ips = self.resolve_domain(domain)
                domain_new_ips.update(ips)
                
                # Small delay between queries to the same domain
                if i < self.queries_per_batch - 1:
                    time.sleep(0.5)
            
            # Update collected IPs
            with self.lock:
                before_count = len(self.collected_ips[domain])
                self.collected_ips[domain].update(domain_new_ips)
                after_count = len(self.collected_ips[domain])
                
                # Track new IPs
                new_count = after_count - before_count
                if new_count > 0:
                    new_ips[domain] = domain_new_ips - set(list(self.collected_ips[domain])[:before_count])
                    print(f"[INFO] {domain}: +{new_count} IPs found in this batch (session total: {after_count})")
        
        return new_ips
    
    def start(self, callback=None):
        """Start collecting IPs in background thread.
        
        Args:
            callback: Optional function called with (domain, new_ips) when new IPs found
        """
        if self.running:
            print("[WARN] IP collector already running")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._collect_loop, args=(callback,))
        self.thread.daemon = True
        self.thread.start()
        # Silently start collector
    
    def stop(self):
        """Stop collecting IPs."""
        if not self.running:
            return
        
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
    
    def _collect_loop(self, callback):
        """Main collection loop running in background thread."""
        batch_count = 0
        last_status_time = time.time()
        
        while self.running:
            try:
                # Collect batch
                batch_count += 1
                # Silent batch start
                new_ips = self.collect_batch()
                
                # Notify callback of new IPs
                if callback:
                    for domain, ips in new_ips.items():
                        if ips:
                            callback(domain, ips)
                
                # Update status time
                current_time = time.time()
                if current_time - last_status_time > 300:
                    last_status_time = current_time
                
                # Wait for next batch (unless stopping)
                if self.running:
                    for i in range(self.batch_interval):
                        if not self.running:
                            break
                        time.sleep(1)
                        
            except Exception as e:
                print(f"[ERROR] IP collection error: {e}")
                time.sleep(5)  # Brief pause before retry
    
    def get_collected_ips(self) -> Dict[str, List[str]]:
        """Get all collected IPs.
        
        Returns:
            Dictionary of domain -> list of IPs
        """
        with self.lock:
            return {domain: list(ips) for domain, ips in self.collected_ips.items()}
    
    def get_stats(self) -> Dict[str, int]:
        """Get collection statistics.
        
        Returns:
            Dictionary of domain -> IP count
        """
        with self.lock:
            return {domain: len(ips) for domain, ips in self.collected_ips.items()}