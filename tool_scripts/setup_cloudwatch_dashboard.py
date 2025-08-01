#!/usr/bin/env python3
"""
Set up per-instance CloudWatch dashboards for Binance latency monitoring.

This script creates CloudWatch dashboards to visualize latency metrics
from continuous monitoring. Each instance gets its own dashboard with:
- Average latency by domain (using Metrics Insights aggregation)
- Individual charts for each domain showing IP-level performance

The script preserves existing dashboards and continues gracefully on errors.
"""

import json
import argparse
import boto3
from datetime import datetime

def validate_dashboard_structure(cloudwatch_client, dashboard_name, expected_domains):
    """Validate existing dashboard structure matches current configuration.
    
    Note: This validation is informational only. Existing dashboards are
    preserved even if they don't match the expected structure to avoid
    disrupting custom configurations.
    
    Returns:
        tuple: (is_valid, error_message)
    """
    try:
        # Get existing dashboard
        response = cloudwatch_client.get_dashboard(DashboardName=dashboard_name)
        dashboard_body = json.loads(response['DashboardBody'])
        
        widgets = dashboard_body.get('widgets', [])
        expected_widget_count = 1 + len(expected_domains)  # 1 domain chart + N IP charts
        
        # Check widget count
        if len(widgets) != expected_widget_count:
            return False, f"Dashboard has {len(widgets)} widgets, expected {expected_widget_count}"
        
        # Check first widget is Average Latency by Domain
        if not widgets or widgets[0].get('properties', {}).get('title') != 'Average Latency by Domain':
            return False, "First widget should be 'Average Latency by Domain'"
        
        # Check domain coverage in first widget
        domain_metrics = widgets[0].get('properties', {}).get('metrics', [])
        found_domains = set()
        for metric in domain_metrics:
            if isinstance(metric, list) and metric:
                expr = metric[0].get('expression', '')
                for domain in expected_domains:
                    if f'Domain="{domain}"' in expr:
                        found_domains.add(domain)
        
        missing_domains = set(expected_domains) - found_domains
        if missing_domains:
            return False, f"Dashboard missing domains in overview chart: {', '.join(missing_domains)}"
        
        # Check individual IP charts
        expected_titles = {f"Average Latency by IP - {domain}" for domain in expected_domains}
        found_titles = set()
        
        for widget in widgets[1:]:  # Skip first widget
            title = widget.get('properties', {}).get('title', '')
            if title:
                found_titles.add(title)
        
        missing_titles = expected_titles - found_titles
        if missing_titles:
            return False, f"Dashboard missing IP charts for: {', '.join([t.split(' - ')[1] for t in missing_titles])}"
        
        return True, None
        
    except cloudwatch_client.exceptions.ResourceNotFound:
        return True, None  # Dashboard doesn't exist, which is fine
    except Exception as e:
        return False, f"Error validating dashboard: {str(e)}"


def create_latency_dashboard(cloudwatch_client, region, dashboard_name, domains, instance_filter=None):
    """Create or validate latency monitoring dashboard with dynamic domains.
    
    This function:
    - Checks if a dashboard already exists
    - If it exists, preserves it regardless of structure
    - Only creates a new dashboard if none exists
    - Uses CloudWatch Metrics Insights for efficient aggregation
    
    Args:
        cloudwatch_client: Boto3 CloudWatch client
        region: AWS region
        dashboard_name: Name for the dashboard (instance ID or custom name)
        domains: List of domains from config
        instance_filter: Instance ID/name to filter metrics (defaults to dashboard_name)
    """
    # Use provided instance filter or default to dashboard name
    if instance_filter is None:
        instance_filter = dashboard_name
        
    # Check if dashboard already exists
    try:
        existing_dashboard = cloudwatch_client.get_dashboard(DashboardName=dashboard_name)
        
        # Validate structure
        is_valid, error_msg = validate_dashboard_structure(cloudwatch_client, dashboard_name, domains)
        
        if is_valid:
            # Dashboard exists and is valid
            print(f"[OK] Dashboard '{dashboard_name}' already exists with correct structure")
            dashboard_url = f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#dashboards:name={dashboard_name}"
            print(f"     URL: {dashboard_url}")
            return True
        else:
            # Dashboard exists but structure doesn't match
            print(f"[WARN] Dashboard '{dashboard_name}' exists but structure doesn't match: {error_msg}")
            print(f"[INFO] Keeping existing dashboard as-is")
            dashboard_url = f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#dashboards:name={dashboard_name}"
            print(f"     URL: {dashboard_url}")
            return True  # Return success to allow monitoring to continue
            
    except cloudwatch_client.exceptions.ResourceNotFound:
        # Dashboard doesn't exist, create it
        print(f"[INFO] Dashboard '{dashboard_name}' does not exist, creating new dashboard")
        pass
    
    # Build widgets dynamically
    widgets = []
    
    # 1. Average Latency by Domain chart (overview of all domains)
    # Use CloudWatch Metrics Insights query to avoid metric limits
    # This aggregates all IPs per domain efficiently without hitting the 500 metric limit
    # The AVG of averages is valid since all IPs use the same 100-connection sample size
    domain_metrics = [[{
        "expression": f'SELECT AVG(TCPHandshake_average) FROM SCHEMA("BinanceLatency", "Domain", IP, InstanceId) WHERE InstanceId = \'{instance_filter}\' GROUP BY "Domain"',
        "id": "q1",
        "period": 300
    }]]
    
    widgets.append({
        "type": "metric",
        "x": 0,
        "y": 0,
        "width": 24,
        "height": 8,
        "properties": {
            "metrics": domain_metrics,
            "view": "timeSeries",
            "stacked": False,
            "region": region,
            "title": "Average Latency by Domain",
            "period": 300,
            "yAxis": {
                "left": {
                    "label": "Latency (μs)",
                    "showUnits": False
                }
            },
            "legend": {
                "position": "bottom"
            }
        }
    })
    
    # 2. Individual IP charts for each domain
    for i, domain in enumerate(domains):
        row = (i // 2) + 1  # Start from row 1 (row 0 is domain chart)
        col = (i % 2) * 12   # 0 or 12 (2 columns)
        
        widgets.append({
            "type": "metric",
            "x": col,
            "y": row * 8,  # Each chart is 8 units tall
            "width": 12,
            "height": 6,
            "properties": {
                "metrics": [
                    [{
                        "expression": f'SEARCH(\'{{BinanceLatency,Domain,IP,InstanceId}} MetricName="TCPHandshake_average" Domain="{domain}" InstanceId="{instance_filter}"\', \'Average\', 300)',
                        "label": "",
                        "id": "e1"
                    }]
                ],
                "view": "timeSeries",
                "stacked": False,
                "region": region,
                "title": f"Average Latency by IP - {domain}",
                "period": 300,
                "yAxis": {
                    "left": {
                        "label": "Latency (μs)",
                        "showUnits": False
                    }
                },
                "legend": {
                    "position": "bottom"
                }
            }
        })
    
    # Create dashboard
    dashboard_body = {"widgets": widgets}
    
    try:
        response = cloudwatch_client.put_dashboard(
            DashboardName=dashboard_name,
            DashboardBody=json.dumps(dashboard_body)
        )
        print(f"[OK] Created dashboard: {dashboard_name}")
        dashboard_url = f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#dashboards:name={dashboard_name}"
        print(f"     URL: {dashboard_url}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to create dashboard: {e}")
        return False




def create_custom_metric_queries(cloudwatch_client):
    """Create example custom metric queries."""
    
    print("\nExample CloudWatch Insights queries:")
    print("-" * 60)
    
    queries = [
        {
            "name": "Average latency by IP",
            "query": """
                fields @timestamp, @message
                | filter Namespace = "BinanceLatency"
                | stats avg(TCPHandshake_median) by IP
                | sort avg desc
            """
        },
        {
            "name": "Latency trends over time",
            "query": """
                fields @timestamp, TCPHandshake_median, Domain, IP
                | filter Namespace = "BinanceLatency"
                | stats avg(TCPHandshake_median) by bin(5m)
            """
        },
        {
            "name": "Worst performing IPs",
            "query": """
                fields @timestamp, IP, Domain, TCPHandshake_p99
                | filter Namespace = "BinanceLatency"
                | filter TCPHandshake_p99 > 150
                | sort TCPHandshake_p99 desc
                | limit 20
            """
        }
    ]
    
    for query in queries:
        print(f"\n{query['name']}:")
        print(query['query'])


def main():
    parser = argparse.ArgumentParser(description="Set up CloudWatch dashboards")
    parser.add_argument('--dashboard-name', required=True, 
                       help='Dashboard name (usually instance ID or custom name)')
    parser.add_argument('--instance-filter', 
                       help='Instance ID/name to filter metrics (defaults to dashboard-name)')
    parser.add_argument('--config', default='config.json', help='Config file path')
    parser.add_argument('--region', help='AWS region (overrides config)')
    
    args = parser.parse_args()
    
    # Load config
    try:
        with open(args.config, 'r') as f:
            config = json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load config: {e}")
        return 1
    
    region = args.region or config.get('region', 'ap-northeast-1')
    domains = config.get('latency_test_domains', [])
    
    if not domains:
        print("[ERROR] No domains found in config's latency_test_domains")
        return 1
    
    # Create CloudWatch client
    cloudwatch = boto3.client('cloudwatch', region_name=region)
    
    print(f"Setting up CloudWatch dashboard '{args.dashboard_name}' in region: {region}")
    if args.instance_filter and args.instance_filter != args.dashboard_name:
        print(f"Filtering metrics for instance: {args.instance_filter}")
    print(f"Domains to monitor: {', '.join(domains)}")
    print("-" * 60)
    
    # Create or validate dashboard
    success = create_latency_dashboard(cloudwatch, region, args.dashboard_name, domains, args.instance_filter)
    
    if success:
        # Show example queries
        create_custom_metric_queries(cloudwatch)
        
        print(f"\n[INFO] CloudWatch dashboard '{args.dashboard_name}' setup complete!")
        print("[INFO] Metrics will appear after monitoring starts")
        return 0
    else:
        print(f"\n[ERROR] Failed to set up dashboard '{args.dashboard_name}'")
        return 1


if __name__ == '__main__':
    exit(main())