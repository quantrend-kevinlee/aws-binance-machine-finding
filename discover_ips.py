#!/usr/bin/env python3
"""
Standalone IP discovery tool for Binance domains.

This tool can be run independently to collect and maintain a comprehensive
list of IPs for all Binance domains. It periodically queries DNS and validates
IP liveness.

Usage:
    python3 discover_ips.py [--continuous]
"""

import argparse
import sys
import time
import signal

from core.config import Config
from core.ip_discovery import IPCollector, IPValidator, IPPersistence
from core.constants import UTC_PLUS_8


class IPDiscoveryTool:
    """Standalone tool for IP discovery and management."""
    
    def __init__(self, config: Config):
        """Initialize IP discovery tool."""
        self.config = config
        self.persistence = IPPersistence(config.report_dir)
        self.collector = IPCollector(config.domains)
        self.validator = IPValidator()
        self.running = True
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        print(f"\n[INFO] Received signal {signum}, shutting down...")
        self.running = False
        self.collector.stop()
        self.persistence.shutdown()
    
    def run_once(self):
        """Run a single discovery cycle."""
        # Load existing IP data
        ip_data = self.persistence.load_latest()
        new_ips_count = 0
        
        def on_new_ips(domain, new_ips):
            """Callback for new IPs found."""
            nonlocal new_ips_count
            for ip in new_ips:
                self.persistence.update_ip(ip_data, domain, ip)
                new_ips_count += 1
        
        # Run collection for multiple batches
        self.collector.start(callback=on_new_ips)
        
        try:
            # Run for 5 batches (5 minutes with 60s intervals)
            for i in range(5):
                if not self.running:
                    break
                pass  # Silent batch progress
                
                # Interruptible sleep - check self.running every second
                for _ in range(self.collector.batch_interval):
                    if not self.running:
                        break
                    time.sleep(1)
        finally:
            self.collector.stop()
        
        # Get all active IPs for validation
        all_active_ips = self.persistence.get_all_active_ips(ip_data)
        validation_results = self.validator.validate_domain_ips(all_active_ips)
        
        # Update IP status based on validation
        dead_count = 0
        for domain, results in validation_results.items():
            for ip, (is_alive, latency) in results.items():
                self.persistence.update_ip(ip_data, domain, ip, alive=is_alive, validated=True)
                if not is_alive:
                    dead_count += 1
        
        # Move dead IPs to history
        if dead_count > 0:
            moved = self.persistence.remove_dead_ips(ip_data, reason="validation_failed")
        
        # Save updated IP data and sync to disk
        self.persistence.save_and_sync(ip_data)
        
        # Print summary
        total_active = sum(len(self.persistence.get_domain_ips(ip_data, d)) for d in self.config.domains)
        if new_ips_count > 0 or dead_count > 0:
            print(f"[INFO] IP Discovery: +{new_ips_count} new, -{dead_count} dead, {total_active} total active IPs")
    
    def run_continuous(self):
        """Run continuous discovery mode."""
        print("[INFO] Starting continuous IP discovery (Ctrl+C to stop)")
        
        while self.running:
            try:
                self.run_once()
                
                if self.running:
                    for _ in range(600):  # 10 minutes
                        if not self.running:
                            break
                        time.sleep(1)
                        
            except Exception as e:
                print(f"\n[ERROR] Discovery cycle failed: {e}")
                if self.running:
                    print("[INFO] Retrying in 60 seconds...")
                    time.sleep(60)
        
        print("\n[INFO] IP discovery stopped")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Discover and manage Binance IPs")
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run in continuous mode (default: run once)"
    )
    args = parser.parse_args()
    
    # Load configuration
    config = Config()
    
    # Create and run discovery tool
    tool = IPDiscoveryTool(config)
    
    try:
        if args.continuous:
            tool.run_continuous()
        else:
            tool.run_once()
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
    finally:
        # Ensure proper shutdown
        tool.persistence.shutdown()


if __name__ == "__main__":
    main()