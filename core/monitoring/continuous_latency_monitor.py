#!/usr/bin/env python3
"""
Continuous latency monitoring daemon for qualified EC2 instances.

This script runs continuously, performing latency tests and publishing metrics to CloudWatch.
Features:
- Self-contained IP loading (no core module dependencies)
- Supports both simplified IP format and full metadata format
- Tests latency to configured monitoring domains only  
- Batches all metrics and sends after test completion (efficient API usage)
- Publishes average metric per IP
- Optionally stores raw data locally when --store-raw-data-locally is specified
- Gracefully handles failed IPs without stopping the test cycle
- Designed for deployment via SCP for reliable file transfer
"""

import json
import time
import os
import sys
import socket
import statistics
import argparse
import signal
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError

# Configuration
WAIT_BETWEEN_TESTS = 0  # Wait 0 seconds after test completion before next test
ATTEMPTS_PER_TEST = 100  # Reduced from 1000 for continuous monitoring
WARMUP_ATTEMPTS = 10
DEFAULT_TIMEOUT_MS = 3000  # Default TCP timeout in milliseconds
CLOUDWATCH_NAMESPACE = "BinanceLatency"

class ContinuousLatencyMonitor:
    def __init__(self, ip_list_file, config_file, instance_id=None, raw_data_dir=None):
        self.running = True
        self.config = self._load_config(config_file)
        self.ip_list = self._load_ip_list(ip_list_file)
        self.instance_id = instance_id or self._get_instance_id()
        
        # Get TCP timeout from config, convert from ms to seconds
        self.tcp_timeout_ms = self.config.get('tcp_connection_timeout_ms', DEFAULT_TIMEOUT_MS)
        self.tcp_timeout_seconds = self.tcp_timeout_ms / 1000.0
        
        # CloudWatch setup (required)
        try:
            self.cloudwatch = boto3.client('cloudwatch', region_name=self.config['region'])
            print(f"[INFO] CloudWatch metrics enabled for region: {self.config['region']}")
            
            # Test CloudWatch connectivity
            try:
                # Test with a simple list_metrics call (doesn't create any data)
                test_response = self.cloudwatch.list_metrics(
                    Namespace=CLOUDWATCH_NAMESPACE
                )
                print(f"[INFO] CloudWatch connectivity verified")
            except Exception as test_e:
                print(f"[WARN] CloudWatch connectivity test warning: {test_e}")
                print(f"[WARN] Will still try to publish metrics")
                
        except Exception as e:
            print(f"[ERROR] Failed to create CloudWatch client: {e}")
            print(f"[ERROR] CloudWatch connection is required for monitoring")
            sys.exit(1)
        
        # Metrics buffer for batch sending after test completion
        self.metrics_buffer = []
        
        # Raw data storage (optional)
        self.store_raw_data = raw_data_dir is not None
        if self.store_raw_data:
            self.raw_data_dir = os.path.expanduser(raw_data_dir)
            os.makedirs(self.raw_data_dir, exist_ok=True)
            print(f"[INFO] Raw data will be stored in: {self.raw_data_dir}")
        else:
            print("[INFO] Raw data storage disabled")
        
    def _load_ip_list(self, ip_list_file):
        """Load IP list from file - self-contained, no core module dependencies."""
        try:
            # Load monitoring domains from config
            monitoring_domains = self.config.get('monitoring_domains', [])
            
            # Try to load IP list directly from file (self-contained approach)
            if not os.path.exists(ip_list_file):
                print(f"[ERROR] IP list file not found: {ip_list_file}")
                return {}
            
            with open(ip_list_file, 'r') as f:
                file_content = f.read().strip()
            
            if not file_content:
                print(f"[ERROR] IP list file is empty: {ip_list_file}")
                return {}
                
            ip_data = json.loads(file_content)
            
            # Handle both simplified format (domain->IP list) and full metadata format
            if 'domains' in ip_data:
                # Full metadata format from discover_ips.py
                print("[INFO] Loading IPs from metadata format")
                ip_list = {}
                all_domains = ip_data.get('domains', {})
                
                # Filter to only monitoring domains and extract active IPs
                for domain in monitoring_domains:
                    if domain in all_domains:
                        # Extract IPs (keys from the IPs dict)
                        ips = list(all_domains[domain].get('ips', {}).keys())
                        if ips:
                            ip_list[domain] = ips
                            print(f"[INFO] Loaded {len(ips)} IPs for {domain}")
                
            else:
                # Simplified format (domain -> IP list) from test scripts
                print("[INFO] Loading IPs from simplified format")
                ip_list = {}
                
                # Filter to only monitoring domains
                for domain in monitoring_domains:
                    if domain in ip_data:
                        ips = ip_data[domain]
                        if isinstance(ips, list) and ips:
                            ip_list[domain] = ips
                            print(f"[INFO] Loaded {len(ips)} IPs for {domain}")
            
            if not ip_list:
                print(f"[WARN] No IPs found for monitoring domains: {monitoring_domains}")
                
            return ip_list
            
        except json.JSONDecodeError as e:
            print(f"[ERROR] Failed to parse IP list JSON: {e}")
            return {}
        except Exception as e:
            print(f"[ERROR] Failed to load IP list: {e}")
            return {}
    
    def _load_config(self, config_file):
        """Load configuration."""
        try:
            with open(config_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"ERROR: Failed to load config: {e}")
            return {}
    
    def _get_instance_id(self):
        """Get EC2 instance ID from metadata service."""
        try:
            import urllib.request
            response = urllib.request.urlopen(
                'http://169.254.169.254/latest/meta-data/instance-id',
                timeout=2
            )
            return response.read().decode('utf-8')
        except:
            return "unknown"
    
    def test_latency(self, ip, hostname):
        """Perform latency test to a single IP."""
        # Warmup
        for _ in range(WARMUP_ATTEMPTS):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(self.tcp_timeout_seconds)
                s.connect((ip, 443))
                s.close()
            except:
                pass
        
        # Actual measurements
        latencies = []
        for _ in range(ATTEMPTS_PER_TEST):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(self.tcp_timeout_seconds)
                t0 = time.perf_counter_ns()
                s.connect((ip, 443))
                t1 = time.perf_counter_ns()
                s.close()
                latencies.append((t1 - t0) / 1000)  # ns to microseconds
            except:
                continue
        
        if not latencies:
            return None
        
        # Only calculate average since that's all we upload to CloudWatch
        return {
            "average": statistics.mean(latencies)
        }
    
    def run_test_cycle(self):
        """Run one complete test cycle for all domains and IPs."""
        timestamp = datetime.now(timezone.utc)
        results = {}
        metrics_to_send = []
        
        print(f"\n[INFO] Starting test cycle at {timestamp}")
        
        # Count domains and IPs that will actually be tested
        monitoring_domains = self.config.get('monitoring_domains', [])
        domains_to_test = [d for d in monitoring_domains if d in self.ip_list]
        total_ips_to_test = sum(len(self.ip_list[d]) for d in domains_to_test)
        
        print(f"[INFO] Testing {len(domains_to_test)} domains with {total_ips_to_test} IPs")
        if len(monitoring_domains) > len(domains_to_test):
            missing = [d for d in monitoring_domains if d not in self.ip_list]
            print(f"[WARN] Missing IPs for {len(missing)} monitoring domains: {', '.join(missing)}")
        
        for domain in monitoring_domains:
            if domain not in self.ip_list:
                continue
                
            results[domain] = {}
            for ip in self.ip_list[domain]:
                try:
                    stats = self.test_latency(ip, domain)
                    if stats:
                        results[domain][ip] = stats
                        
                        # Collect metrics for batch sending
                        metrics = self._prepare_metrics(timestamp, domain, ip, stats)
                        if metrics:
                            metrics_to_send.extend(metrics)
                except Exception as e:
                    print(f"[WARN] Failed to test {domain} {ip}: {e} - skipping")
                    continue
        
        # Calculate domain-level averages
        domain_metrics = self._calculate_domain_averages(timestamp, results)
        if domain_metrics:
            metrics_to_send.extend(domain_metrics)
        
        # Send all metrics to CloudWatch after test completion
        if metrics_to_send:
            print(f"[INFO] Sending {len(metrics_to_send)} metrics to CloudWatch")
            self._send_metrics_to_cloudwatch(metrics_to_send)
        
        # Save raw data if enabled
        if self.store_raw_data:
            self._save_raw_data(timestamp, results)
        
        tested_domains = len([d for d in results if results[d]])
        tested_ips = sum(len(results[d]) for d in results)
        print(f"[INFO] Test cycle completed: {tested_domains} domains, {tested_ips} IPs tested")
        
        return results
    
    def _prepare_metrics(self, timestamp, domain, ip, stats):
        """Prepare metrics for batch sending."""
        metric_data = []
        
        # Send average metric per IP
        if 'average' in stats:
            value = float(stats['average'])
            # Validate value is within CloudWatch limits
            if value < 0 or value > 1e20:  # CloudWatch has limits on values
                print(f"[WARN] Skipping metric average with invalid value: {value}")
                return metric_data
                
            metric_data.append({
                'MetricName': 'TCPHandshake_average',
                'Dimensions': [
                    {'Name': 'Domain', 'Value': domain},
                    {'Name': 'IP', 'Value': ip},
                    {'Name': 'InstanceId', 'Value': self.instance_id}
                ],
                'Value': value,
                'Unit': 'Microseconds',
                'Timestamp': timestamp
            })
        
        return metric_data
    
    def _calculate_domain_averages(self, timestamp, results):
        """Calculate domain-level average metrics across all IPs."""
        domain_metrics = []
        
        for domain, ip_results in results.items():
            if not ip_results:
                continue
            
            # Collect average values across IPs
            average_values = []
            
            for ip, stats in ip_results.items():
                if 'average' in stats:
                    average_values.append(float(stats['average']))
            
            # Calculate domain average
            if average_values:
                avg_value = statistics.mean(average_values)
                
                # Create domain-level metric
                domain_metrics.append({
                    'MetricName': 'TCPHandshake_average_DomainAvg',
                    'Dimensions': [
                        {'Name': 'Domain', 'Value': domain},
                        {'Name': 'InstanceId', 'Value': self.instance_id}
                    ],
                    'Value': avg_value,
                    'Unit': 'Microseconds',
                    'Timestamp': timestamp
                })
            
            # Log domain summary
            if average_values:
                print(f"[INFO] {domain}: avg={avg_value:.2f}μs across {len(ip_results)} IPs")
        
        return domain_metrics
    
    def _send_metrics_to_cloudwatch(self, metrics):
        """Send all metrics to CloudWatch in batches."""
        if not metrics:
            print(f"[DEBUG] No metrics to send")
            return
            
        # Send in batches of up to 1000 metrics (CloudWatch limit)
        batch_size = 1000
        total_sent = 0
        
        for i in range(0, len(metrics), batch_size):
            batch = metrics[i:i+batch_size]
            
            try:
                response = self.cloudwatch.put_metric_data(
                    Namespace=CLOUDWATCH_NAMESPACE,
                    MetricData=batch
                )
                total_sent += len(batch)
                print(f"[OK] Sent batch of {len(batch)} metrics to CloudWatch (total: {total_sent}/{len(metrics)})")
                
            except ClientError as e:
                print(f"[ERROR] CloudWatch error: {e.response.get('Error', {}).get('Message', 'Unknown')}")
            except Exception as e:
                print(f"[ERROR] Failed to send metrics batch: {e}")
    
    def _save_raw_data(self, timestamp, results):
        """Save raw test results to local JSONL file."""
        filename = os.path.join(
            self.raw_data_dir,
            f"latency_{timestamp.strftime('%Y%m%d')}.jsonl"
        )
        
        try:
            with open(filename, 'a') as f:
                record = {
                    'timestamp': timestamp.isoformat(),
                    'instance_id': self.instance_id,
                    'results': results
                }
                f.write(json.dumps(record) + '\n')
        except Exception as e:
            print(f"ERROR saving raw data: {e}")
    
    
    def run(self):
        """Main monitoring loop."""
        # Set up signal handlers
        signal.signal(signal.SIGTERM, lambda s, f: self.stop())
        signal.signal(signal.SIGINT, lambda s, f: self.stop())
        
        print(f"Starting continuous latency monitoring")
        print(f"Instance ID: {self.instance_id}")
        print(f"TCP connection timeout: {self.tcp_timeout_ms}ms")
        print(f"Wait between tests: {WAIT_BETWEEN_TESTS}s")
        print(f"CloudWatch: Batch sending after test completion")
        
        # Count domains that will actually be monitored
        monitoring_domains = self.config.get('monitoring_domains', [])
        domains_with_ips = [d for d in monitoring_domains if d in self.ip_list]
        print(f"Monitoring {len(domains_with_ips)}/{len(monitoring_domains)} domains (with IPs available)")
        
        if self.store_raw_data:
            print(f"Raw data storage: {self.raw_data_dir}")
        
        # Main test loop
        while self.running:
            try:
                print(f"\n[INFO] Starting new test cycle at {datetime.now(timezone.utc)}")
                start_time = time.time()
                self.run_test_cycle()
                elapsed = time.time() - start_time
                print(f"[INFO] Test cycle completed in {elapsed:.1f} seconds")
                
                # Wait fixed time after test completion
                if self.running:
                    print(f"[INFO] Waiting {WAIT_BETWEEN_TESTS} seconds before next test cycle...")
                    time.sleep(WAIT_BETWEEN_TESTS)
                    
            except Exception as e:
                print(f"ERROR in test cycle: {e}")
                import traceback
                traceback.print_exc()
                if self.running:
                    print(f"[INFO] Waiting {WAIT_BETWEEN_TESTS} seconds before retry...")
                    time.sleep(WAIT_BETWEEN_TESTS)
        
        print("Monitoring stopped")
    
    def stop(self):
        """Stop monitoring gracefully."""
        print("Stopping monitoring...")
        self.running = False


def main():
    parser = argparse.ArgumentParser(description="Continuous latency monitor")
    parser.add_argument('--ip-list', default='/opt/binance-monitor/ip_list_latest.json',
                       help='Path to IP list file')
    parser.add_argument('--config', default='/opt/binance-monitor/config.json',
                       help='Path to config file')
    parser.add_argument('--instance-id', help='Override instance ID')
    parser.add_argument('--store-raw-data-locally', nargs='?', const='.',
                       help='Store raw data locally. Optionally specify directory (default: current directory)')
    
    args = parser.parse_args()
    
    monitor = ContinuousLatencyMonitor(
        args.ip_list, 
        args.config, 
        instance_id=args.instance_id,
        raw_data_dir=args.store_raw_data_locally
    )
    monitor.run()


if __name__ == '__main__':
    main()