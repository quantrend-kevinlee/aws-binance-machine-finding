#!/usr/bin/env python3
"""
CloudWatch metrics cleanup utility for specific instances.

Note: AWS CloudWatch does not allow deleting metrics data directly.
This script helps manage metrics by showing status, stopping data sources,
and cleaning up related resources like alarms.

Metrics automatically expire from console view after 2 weeks of no new data.
Historical data is retained for 15 months but becomes invisible in console.
"""

import json
import argparse
import boto3
import sys
import os
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional


def get_instance_metrics(cloudwatch_client, instance_id: str, namespace: str = "BinanceLatency") -> List[Dict]:
    """Get all metrics for a specific instance ID."""
    try:
        paginator = cloudwatch_client.get_paginator('list_metrics')
        metrics = []
        
        for page in paginator.paginate(
            Namespace=namespace,
            Dimensions=[
                {
                    'Name': 'InstanceId',
                    'Value': instance_id
                }
            ]
        ):
            metrics.extend(page['Metrics'])
        
        return metrics
    except Exception as e:
        print(f"[ERROR] Failed to list metrics: {e}")
        return []


def get_metric_last_timestamp(cloudwatch_client, metric: Dict, days_back: int = 30) -> Optional[datetime]:
    """Get the timestamp of the last data point for a metric."""
    try:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days_back)
        
        response = cloudwatch_client.get_metric_statistics(
            Namespace=metric['Namespace'],
            MetricName=metric['MetricName'],
            Dimensions=metric['Dimensions'],
            StartTime=start_time,
            EndTime=end_time,
            Period=300,  # 5 minutes
            Statistics=['Average']
        )
        
        if response['Datapoints']:
            # Get the most recent timestamp
            latest = max(response['Datapoints'], key=lambda x: x['Timestamp'])
            return latest['Timestamp']
        return None
        
    except Exception as e:
        print(f"[WARN] Failed to get last timestamp for {metric['MetricName']}: {e}")
        return None


def calculate_console_expiration(last_timestamp: datetime) -> datetime:
    """Calculate when a metric will disappear from CloudWatch console (2 weeks after last data)."""
    return last_timestamp + timedelta(weeks=2)


def delete_instance_alarms(cloudwatch_client, instance_id: str) -> int:
    """Delete CloudWatch alarms that reference the specific instance."""
    try:
        # Get all alarms
        paginator = cloudwatch_client.get_paginator('describe_alarms')
        deleted_count = 0
        
        for page in paginator.paginate():
            for alarm in page['MetricAlarms']:
                # Check if alarm dimensions include our instance ID
                for dimension in alarm.get('Dimensions', []):
                    if dimension['Name'] == 'InstanceId' and dimension['Value'] == instance_id:
                        try:
                            cloudwatch_client.delete_alarms(AlarmNames=[alarm['AlarmName']])
                            print(f"[OK] Deleted alarm: {alarm['AlarmName']}")
                            deleted_count += 1
                        except Exception as e:
                            print(f"[ERROR] Failed to delete alarm {alarm['AlarmName']}: {e}")
                        break
        
        return deleted_count
        
    except Exception as e:
        print(f"[ERROR] Failed to delete alarms: {e}")
        return 0


def stop_monitoring_service(instance_id: str, config: Dict) -> bool:
    """Stop monitoring service on the instance if it's running."""
    try:
        # Import necessary modules
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from run_latency_monitoring import get_instance_public_ip
        
        # Get instance public IP
        public_ip, error = get_instance_public_ip(instance_id, config['region'])
        if not public_ip:
            print(f"[ERROR] Cannot get instance IP: {error}")
            return False
        
        print(f"[INFO] Instance {instance_id} has IP: {public_ip}")
        print(f"[INFO] To stop monitoring service, run:")
        print(f"ssh -i {config['key_path']} ec2-user@{public_ip} 'sudo systemctl stop binance-latency-monitor'")
        print(f"ssh -i {config['key_path']} ec2-user@{public_ip} 'sudo systemctl disable binance-latency-monitor'")
        
        return True
        
    except Exception as e:
        print(f"[ERROR] Failed to generate stop commands: {e}")
        return False


def show_metrics_status(cloudwatch_client, instance_id: str, show_expiration: bool = False):
    """Show status of all metrics for an instance."""
    print(f"CloudWatch metrics for instance: {instance_id}")
    print("=" * 60)
    
    # Get metrics
    metrics = get_instance_metrics(cloudwatch_client, instance_id)
    
    if not metrics:
        print("[INFO] No CloudWatch metrics found for this instance.")
        print("[INFO] Instance may not be publishing metrics or metrics may have expired.")
        return
    
    print(f"Found {len(metrics)} metrics:")
    print()
    
    active_metrics = 0
    expired_metrics = 0
    now = datetime.now(timezone.utc)
    
    for metric in metrics:
        metric_name = metric['MetricName']
        
        # Get dimensions for display
        dims = {d['Name']: d['Value'] for d in metric['Dimensions']}
        domain = dims.get('Domain', 'N/A')
        ip = dims.get('IP', 'N/A')
        
        # Get last timestamp
        last_timestamp = get_metric_last_timestamp(cloudwatch_client, metric)
        
        if last_timestamp:
            time_since_last = now - last_timestamp
            console_expiry = calculate_console_expiration(last_timestamp)
            
            if time_since_last <= timedelta(weeks=2):
                status = "ACTIVE"
                active_metrics += 1
            else:
                status = "EXPIRED_FROM_CONSOLE"
                expired_metrics += 1
            
            print(f"• {metric_name}")
            print(f"  Domain: {domain}, IP: {ip}")
            print(f"  Status: {status}")
            print(f"  Last data: {last_timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print(f"  Time since last: {time_since_last.days} days, {time_since_last.seconds//3600} hours")
            
            if show_expiration:
                if console_expiry > now:
                    days_until_expiry = (console_expiry - now).days
                    print(f"  Console expiry: {console_expiry.strftime('%Y-%m-%d %H:%M:%S UTC')} ({days_until_expiry} days)")
                else:
                    print(f"  Console expiry: Already expired from console view")
        else:
            print(f"• {metric_name}")
            print(f"  Domain: {domain}, IP: {ip}")
            print(f"  Status: NO_RECENT_DATA")
            print(f"  Last data: No data found in last 30 days")
            expired_metrics += 1
        
        print()
    
    # Summary
    print("Summary:")
    print(f"- Active metrics (visible in console): {active_metrics}")
    print(f"- Expired/inactive metrics: {expired_metrics}")
    
    if active_metrics > 0:
        print(f"\n[INFO] {active_metrics} metrics are still active and receiving data.")
        print("[INFO] To stop new data, use --stop-monitoring flag.")
    
    if expired_metrics > 0:
        print(f"\n[INFO] {expired_metrics} metrics have no recent data and may be expired from console.")
    
    print("\n[INFO] CloudWatch metrics cannot be deleted manually.")
    print("[INFO] Metrics disappear from console 2 weeks after last data point.")
    print("[INFO] Historical data is retained for 15 months but becomes invisible in console.")


def main():
    parser = argparse.ArgumentParser(
        description="CloudWatch metrics cleanup utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show all metrics for instance
  python3 tool_scripts/cleanup_cloudwatch_metrics.py i-1234567890abcdef0
  
  # Show metrics with expiration predictions
  python3 tool_scripts/cleanup_cloudwatch_metrics.py i-1234567890abcdef0 --show-expiration
  
  # Delete alarms referencing the instance
  python3 tool_scripts/cleanup_cloudwatch_metrics.py i-1234567890abcdef0 --delete-alarms
  
  # Show commands to stop monitoring on instance
  python3 tool_scripts/cleanup_cloudwatch_metrics.py i-1234567890abcdef0 --stop-monitoring

Important Notes:
  - CloudWatch metrics cannot be deleted manually
  - Metrics expire from console view 2 weeks after last data point
  - Historical data is retained for 15 months
  - Stop publishing new data to prevent metric accumulation
"""
    )
    
    parser.add_argument('instance_id', help='EC2 instance ID to clean up metrics for')
    parser.add_argument('--show-expiration', action='store_true',
                       help='Show when metrics will expire from console view')
    parser.add_argument('--delete-alarms', action='store_true',
                       help='Delete CloudWatch alarms referencing the instance')
    parser.add_argument('--stop-monitoring', action='store_true',
                       help='Show commands to stop monitoring service on instance')
    parser.add_argument('--config', default='config.json',
                       help='Path to config file (default: config.json)')
    parser.add_argument('--region', help='AWS region (overrides config)')
    
    args = parser.parse_args()
    
    # Load config if needed for stop-monitoring
    config = {}
    if args.stop_monitoring:
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), args.config)
            with open(config_path, 'r') as f:
                config = json.load(f)
        except Exception as e:
            print(f"[ERROR] Failed to load config for stop-monitoring: {e}")
            return 1
    
    # Determine region
    region = args.region or config.get('region', 'ap-northeast-1')
    
    # Create CloudWatch client
    try:
        cloudwatch = boto3.client('cloudwatch', region_name=region)
    except Exception as e:
        print(f"[ERROR] Failed to create CloudWatch client: {e}")
        return 1
    
    print(f"Region: {region}")
    print()
    
    # Show metrics status (always do this)
    show_metrics_status(cloudwatch, args.instance_id, args.show_expiration)
    
    # Delete alarms if requested
    if args.delete_alarms:
        print("\nDeleting CloudWatch alarms...")
        print("-" * 60)
        deleted_count = delete_instance_alarms(cloudwatch, args.instance_id)
        if deleted_count > 0:
            print(f"\n[OK] Deleted {deleted_count} alarm(s)")
        else:
            print("\n[INFO] No alarms found for this instance")
    
    # Show stop monitoring commands if requested
    if args.stop_monitoring:
        print("\nStopping monitoring service...")
        print("-" * 60)
        stop_monitoring_service(args.instance_id, config)
    
    print("\n" + "=" * 60)
    print("IMPORTANT NOTES:")
    print("• AWS CloudWatch does not support deleting metrics data")
    print("• Metrics automatically expire from console after 2 weeks of inactivity") 
    print("• To clean up: stop publishing new data and wait for natural expiration")
    print("• Use --stop-monitoring to get commands to stop data publishing")
    
    return 0


if __name__ == '__main__':
    exit(main())