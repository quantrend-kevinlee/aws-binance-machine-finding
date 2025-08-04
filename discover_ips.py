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
from datetime import datetime

from core.config import Config
from core.ip_discovery import IPCollector, IPValidator, IPPersistence
from core.constants import UTC_PLUS_8


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
        
        
        # Extract existing IPs for the collector (discovery domains only)
        existing_ips = {}
        discovery_ip_count = 0
        discovery_domains_with_ips = 0
        
        for domain in self.config.discovery_domains:
            domain_data = self.ip_data.get("domains", {}).get(domain, {})
            domain_ips = set(domain_data.get("ips", {}).keys())
            existing_ips[domain] = domain_ips
            if domain_ips:
                discovery_ip_count += len(domain_ips)
                discovery_domains_with_ips += 1
        
        # Also count total IPs in file for context
        total_file_domains = len(self.ip_data.get("domains", {}))
        total_file_ips = sum(len(d.get("ips", {})) for d in self.ip_data.get("domains", {}).values())
        
        if discovery_ip_count > 0:
            print(f"[INFO] Loaded {discovery_ip_count} IPs for {discovery_domains_with_ips}/{len(self.config.discovery_domains)} discovery domains")
            if total_file_domains > len(self.config.discovery_domains):
                print(f"[INFO] (IP file contains {total_file_ips} total IPs across {total_file_domains} domains)")
        
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
        """Run validation on all IPs and update failure counts."""
        print("\n[INFO] Running IP validation...")
        
        # Get all IPs for validation (from all domains in the file)
        all_active_ips = self.persistence.get_all_active_ips(self.ip_data)
        
        # Only validate discovery domains
        discovery_ips = {domain: ips for domain, ips in all_active_ips.items() 
                        if domain in self.config.discovery_domains}
        
        if not discovery_ips:
            print("[INFO] No discovery domain IPs to validate")
            return
            
        print(f"[INFO] Validating {sum(len(ips) for ips in discovery_ips.values())} IPs from {len(discovery_ips)} discovery domains")
        validation_results = self.validator.validate_domain_ips(discovery_ips, show_progress=False)
        
        # Track statistics
        summary_stats = {"alive": 0, "failed": 0}
        
        for domain, results in validation_results.items():
            domain_stats = {"alive": 0, "failed": 0}
            
            for ip, (is_alive, latency) in results.items():
                if is_alive:
                    # Update last_validated timestamp for successful validation
                    self.persistence.update_ip_validation_time(self.ip_data, domain, ip)
                    domain_stats["alive"] += 1
                    summary_stats["alive"] += 1
                else:
                    # Failed validation - don't update timestamp
                    domain_stats["failed"] += 1
                    summary_stats["failed"] += 1
            
            # Print domain summary
            print(f"  - {domain}: {domain_stats['alive']} alive, {domain_stats['failed']} failed")
        
        # Save updated IP data
        self.persistence.save_and_sync(self.ip_data)
        
        # Check for dead IPs based on time threshold
        current_time = datetime.now(UTC_PLUS_8)
        dead_threshold_seconds = 3600  # 1 hour
        dead_count = 0
        
        for domain in self.config.discovery_domains:
            domain_data = self.ip_data.get("domains", {}).get(domain, {})
            for ip, ip_info in domain_data.get("ips", {}).items():
                last_validated = ip_info.get("last_validated")
                if last_validated:
                    try:
                        last_validated_dt = datetime.fromisoformat(last_validated)
                        time_since_validation = (current_time - last_validated_dt).total_seconds()
                        
                        if time_since_validation > dead_threshold_seconds:
                            dead_count += 1
                            minutes_dead = int(time_since_validation / 60)
                            print(f"    - {domain} {ip}: Dead (not validated for {minutes_dead} minutes)")
                    except:
                        pass
        
        # Print validation summary
        print(f"\n[Validation Summary] Total: {summary_stats['alive']} alive, " +
              f"{summary_stats['failed']} failed, {dead_count} dead (>1 hour)")
        print("[INFO] Validation complete\n")
    
    def _print_session_summary(self):
        """Print summary of the session."""
        print("\n" + "="*60)
        print("SESSION SUMMARY")
        print("="*60)
        
        # Show discovery domains only
        print("\nDiscovery Domains Status:")
        discovery_total = 0
        for domain in self.config.discovery_domains:
            domain_ips = self.persistence.get_domain_ips(self.ip_data, domain)
            domain_total = len(domain_ips)
            discovery_total += domain_total
            
            # Count IPs by time-based status
            current_time = datetime.now(UTC_PLUS_8)
            dead_threshold_seconds = 3600  # 1 hour
            warning_threshold_seconds = 2700  # 45 minutes
            
            dead_ips = []
            warning_ips = []
            
            for ip, ip_info in domain_ips.items():
                last_validated = ip_info.get("last_validated")
                if last_validated:
                    try:
                        last_validated_dt = datetime.fromisoformat(last_validated)
                        time_since_validation = (current_time - last_validated_dt).total_seconds()
                        
                        if time_since_validation > dead_threshold_seconds:
                            dead_ips.append((ip, int(time_since_validation / 60)))
                        elif time_since_validation > warning_threshold_seconds:
                            warning_ips.append((ip, int(time_since_validation / 60)))
                    except:
                        pass
            
            if dead_ips or warning_ips:
                print(f"  - {domain}: {domain_total} IPs ({len(dead_ips)} dead, {len(warning_ips)} warning)")
                # Show IPs close to being dead
                for ip, minutes in warning_ips[:3]:  # Show first 3 warnings
                    print(f"      Warning: {ip} not validated for {minutes} minutes")
                if len(warning_ips) > 3:
                    print(f"      ... and {len(warning_ips) - 3} more approaching dead status")
            else:
                print(f"  - {domain}: {domain_total} IPs (all healthy)")
        
        # Show file totals if different
        total_file_domains = len(self.ip_data.get("domains", {}))
        total_file_ips = sum(len(d.get("ips", {})) for d in self.ip_data.get("domains", {}).values())
        
        print(f"\nNew IPs discovered this session: {self.session_new_count}")
        print(f"Discovery domains tracked: {discovery_total} IPs")
        
        if total_file_domains > len(self.config.discovery_domains):
            print(f"Total in IP file: {total_file_ips} IPs across {total_file_domains} domains")
        
        print(f"Dead IP threshold: 1 hour without successful validation")
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