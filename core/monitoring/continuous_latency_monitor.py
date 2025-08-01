#!/usr/bin/env python3
"""
Continuous latency monitoring daemon for qualified EC2 instances.

This script runs continuously, performing latency tests and publishing metrics to CloudWatch.
Key features:
- Tests latency to all configured Binance domains
- Batches all metrics and sends after test completion (efficient API usage)
- Publishes 6 metrics per IP: median, min, max, p1, p99, average
- Optionally stores raw data locally when --store-raw-data-locally is specified
- Gracefully handles failed IPs without stopping the test cycle
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
WAIT_BETWEEN_TESTS = 60  # Wait 60 seconds after test completion before next test
ATTEMPTS_PER_TEST = 100  # Reduced from 1000 for continuous monitoring
WARMUP_ATTEMPTS = 10
TIMEOUT = 1
CLOUDWATCH_NAMESPACE = "BinanceLatency"

class ContinuousLatencyMonitor:
    def __init__(self, ip_list_file, config_file, instance_id=None, raw_data_dir=None):
        self.running = True
        self.ip_list = self._load_ip_list(ip_list_file)
        self.config = self._load_config(config_file)
        self.instance_id = instance_id or self._get_instance_id()
        
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
        """Load IP list from file."""
        try:
            with open(ip_list_file, 'r') as f:
                data = json.load(f)
                # Extract domain -> IP list mapping
                result = {}
                for domain, info in data.get('domains', {}).items():
                    result[domain] = list(info.get('ips', {}).keys())
                return result
        except Exception as e:
            print(f"ERROR: Failed to load IP list: {e}")
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
                s.settimeout(TIMEOUT)
                s.connect((ip, 443))
                s.close()
            except:
                pass
        
        # Actual measurements
        latencies = []
        for _ in range(ATTEMPTS_PER_TEST):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(TIMEOUT)
                t0 = time.perf_counter_ns()
                s.connect((ip, 443))
                t1 = time.perf_counter_ns()
                s.close()
                latencies.append((t1 - t0) / 1000)  # ns to microseconds
            except:
                continue
        
        if not latencies:
            return None
        
        # Calculate statistics
        sorted_latencies = sorted(latencies)
        n = len(sorted_latencies)
        
        return {
            "median": statistics.median(sorted_latencies),
            "min": sorted_latencies[0],
            "max": sorted_latencies[-1],
            "p1": sorted_latencies[int(n * 0.01)],
            "p99": sorted_latencies[min(int(n * 0.99), n-1)],
            "average": statistics.mean(sorted_latencies)
        }
    
    def run_test_cycle(self):
        """Run one complete test cycle for all domains and IPs."""
        timestamp = datetime.now(timezone.utc)
        results = {}
        metrics_to_send = []
        
        print(f"\n[INFO] Starting test cycle at {timestamp}")
        total_ips = sum(len(ips) for ips in self.ip_list.values())
        print(f"[INFO] Testing {len(self.ip_list)} domains with {total_ips} total IPs")
        
        for domain in self.config.get('latency_test_domains', []):
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
        
        # Create metric data for each statistic
        for stat_name in ['median', 'min', 'max', 'p1', 'p99', 'average']:
            if stat_name in stats:
                value = float(stats[stat_name])
                # Validate value is within CloudWatch limits
                if value < 0 or value > 1e20:  # CloudWatch has limits on values
                    print(f"[WARN] Skipping metric {stat_name} with invalid value: {value}")
                    continue
                    
                metric_data.append({
                    'MetricName': f'TCPHandshake_{stat_name}',
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
        print(f"Wait between tests: {WAIT_BETWEEN_TESTS}s")
        print(f"CloudWatch: Batch sending after test completion")
        print(f"Monitoring {len(self.ip_list)} domains")
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