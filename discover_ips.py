#!/usr/bin/env python3
"""
Standalone IP discovery tool for Binance domains.

This tool continuously monitors and collects IPs for Binance domains through DNS queries.
It validates IP liveness and maintains a comprehensive list with accurate timestamps.

Usage:
    python3 discover_ips.py
"""

import sys
import time
import signal

from core.config import Config
from core.ip_discovery import IPCollector, IPValidator, IPPersistence


class IPDiscoveryTool:
    """Continuous IP discovery and management tool."""
    
    def __init__(self, config: Config):
        """Initialize IP discovery tool."""
        self.config = config
        self.persistence = IPPersistence(config.ip_list_dir)
        self.validator = IPValidator()
        self.running = True
        self.collector = None
        self.ip_data = None
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        print(f"\n[INFO] Shutting down gracefully...")
        self.running = False
        if self.collector:
            self.collector.stop()
    
    def run(self):
        """Run continuous IP discovery."""
        print("[INFO] Starting IP discovery (Ctrl+C to stop)")
        print("[INFO] DNS queries every 60 seconds, validation every 10 minutes")
        
        # Load existing IP data
        self.ip_data = self.persistence.load_latest()
        
        # Extract existing IPs for the collector
        existing_ips = {}
        total_existing = 0
        for domain in self.config.discovery_domains:
            domain_data = self.ip_data.get("domains", {}).get(domain, {})
            existing_ips[domain] = set(domain_data.get("ips", {}).keys())
            total_existing += len(existing_ips[domain])
        
        if total_existing > 0:
            print(f"[INFO] Loaded {total_existing} known IPs across {len(self.config.discovery_domains)} domains")
        
        # Create collector with existing IPs
        self.collector = IPCollector(self.config.discovery_domains, existing_ips=existing_ips)
        
        # Track stats
        self.session_new_count = 0
        self.last_validation_time = time.time()
        
        def on_new_ips(domain, new_ips):
            """Callback for new IPs found - persist immediately."""
            # Update each new IP with current timestamp
            for ip in new_ips:
                self.persistence.update_ip(self.ip_data, domain, ip)
                self.session_new_count += 1
            
            # Persist immediately to capture accurate discovery time
            if new_ips:
                self.persistence.save_and_sync(self.ip_data)
                print(f"[INFO] Found and saved {len(new_ips)} new IPs for {domain}")
        
        # Start continuous collection
        self.collector.start(callback=on_new_ips)
        print("[INFO] Monitoring for new IPs...\n")
        
        try:
            while self.running:
                # Check if it's time for validation (every 10 minutes)
                current_time = time.time()
                if current_time - self.last_validation_time >= 600:  # 10 minutes
                    self._run_validation()
                    self.last_validation_time = current_time
                
                # Sleep for 1 second, checking running status
                time.sleep(1)
                
        except KeyboardInterrupt:
            pass
        finally:
            # Clean shutdown
            print("\n[INFO] Performing final validation before shutdown...")
            self.collector.stop()
            self._run_validation()
            self._print_session_summary()
            self.persistence.shutdown()
    
    def _run_validation(self):
        """Run validation on all IPs and remove dead ones."""
        print("\n[INFO] Running IP validation...")
        
        # Get all active IPs for validation
        all_active_ips = self.persistence.get_all_active_ips(self.ip_data)
        validation_results = self.validator.validate_domain_ips(all_active_ips, show_progress=False)
        
        # Track dead IPs and update validated status
        dead_ips = {}
        total_dead_count = 0
        
        for domain, results in validation_results.items():
            alive_count = 0
            domain_dead_ips = set()
            
            for ip, (is_alive, latency) in results.items():
                if is_alive:
                    alive_count += 1
                else:
                    # Track dead IPs
                    domain_dead_ips.add(ip)
                    total_dead_count += 1
            
            # Store dead IPs for this domain
            if domain_dead_ips:
                dead_ips[domain] = domain_dead_ips
            
            print(f"  - {domain}: {alive_count} alive, {len(domain_dead_ips)} dead")
        
        # Update global validation timestamp for all alive IPs
        self.persistence.update_validation_timestamp(self.ip_data)
        
        # Move dead IPs to history
        if total_dead_count > 0:
            moved = self.persistence.remove_dead_ips(self.ip_data, dead_ips, reason="validation_failed")
            print(f"[INFO] Moved {moved} dead IPs to history")
        
        # Save updated IP data
        self.persistence.save_and_sync(self.ip_data)
        print("[INFO] Validation complete\n")
    
    def _print_session_summary(self):
        """Print summary of the session."""
        print("\n" + "="*60)
        print("SESSION SUMMARY")
        print("="*60)
        
        # Current state
        tracked_total = 0
        for domain in self.config.discovery_domains:
            domain_ips = len(self.persistence.get_domain_ips(self.ip_data, domain))
            tracked_total += domain_ips
            print(f"  - {domain}: {domain_ips} active IPs")
        
        print(f"\nNew IPs discovered this session: {self.session_new_count}")
        print(f"Total tracked IPs: {tracked_total}")
        print("="*60)


def main():
    """Main entry point."""
    # Load configuration
    config = Config()
    
    # Create and run discovery tool
    tool = IPDiscoveryTool(config)
    
    try:
        tool.run()
    except KeyboardInterrupt:
        # Already handled in run()
        pass
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()