#!/usr/bin/env python3
"""
Run continuous latency monitoring locally or on remote EC2 instances.

This tool:
1. For local: runs monitoring directly with CloudWatch publishing using simplified IP format
2. For remote: deploys monitoring script and sets up systemd service via SCP transfer  
3. Creates per-instance CloudWatch dashboards (continues on failure)
4. Provides manual control over monitoring deployment
5. Creates simplified IP lists for efficient monitoring (removes metadata overhead)

Features:
- Simplified IP format for efficient processing
- Self-contained deployment
- SCP-based file transfer
- Compatible with multiple IP list formats

Dashboard creation is non-fatal - monitoring continues even if dashboard
setup fails to ensure metrics are still collected.

Usage:
  python3 run_latency_monitoring.py <instance-id>    # Deploy to remote EC2 instance
  python3 run_latency_monitoring.py                 # Run locally
"""

import sys
import os
import json
import subprocess
import argparse
import boto3
from typing import Optional, Tuple


def get_instance_public_ip(instance_id: str, region: str) -> Tuple[Optional[str], Optional[str]]:
    """Get the public IP of an instance."""
    ec2 = boto3.client('ec2', region_name=region)
    
    try:
        response = ec2.describe_instances(InstanceIds=[instance_id])
        if not response['Reservations']:
            return None, "Instance not found"
        
        instance = response['Reservations'][0]['Instances'][0]
        
        # Check instance state
        state = instance['State']['Name']
        if state != 'running':
            return None, f"Instance is {state}, not running"
        
        # Get public IP
        public_ip = instance.get('PublicIpAddress')
        if not public_ip:
            # Check if it has an associated EIP
            if 'Association' in instance.get('NetworkInterfaces', [{}])[0]:
                public_ip = instance['NetworkInterfaces'][0]['Association'].get('PublicIp')
        
        if not public_ip:
            return None, "Instance has no public IP address"
        
        return public_ip, None
        
    except Exception as e:
        return None, str(e)


def get_local_machine_identifier() -> str:
    """Get a meaningful identifier for the local machine."""
    try:
        # Try to get public IP first
        import urllib.request
        import socket
        
        # Method 1: Try to get public IP via external service
        try:
            response = urllib.request.urlopen('https://api.ipify.org', timeout=3)
            public_ip = response.read().decode('utf-8').strip()
            if public_ip and '.' in public_ip:  # Basic IP validation
                return public_ip
        except:
            pass
        
        # Method 2: Try to get local IP by connecting to a remote address
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(3)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            if local_ip and local_ip != '127.0.0.1':
                return local_ip
        except:
            pass
        
        # Method 3: Use hostname as fallback
        hostname = socket.gethostname()
        if hostname and hostname != 'localhost':
            return hostname
        
    except:
        pass
    
    # Final fallback
    return "local-machine"


def setup_dashboard(dashboard_name: str, config_path: str) -> bool:
    """Set up CloudWatch dashboard for the instance.
    
    Note: Dashboard names with dots (e.g., IP addresses) are automatically
    sanitized by replacing dots with dashes.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Sanitize dashboard name - replace dots with dashes for IP addresses
        sanitized_name = dashboard_name.replace('.', '-')
        
        # Get script path
        script_dir = os.path.dirname(__file__)
        dashboard_script = os.path.join(script_dir, "tool_scripts", "setup_cloudwatch_dashboard.py")
        
        # Run dashboard setup script
        print(f"\nSetting up CloudWatch dashboard '{sanitized_name}'...")
        cmd = [
            "python3", dashboard_script,
            "--dashboard-name", sanitized_name,
            "--config", config_path
        ]
        
        # If the original name is different from sanitized, pass it as instance filter
        if dashboard_name != sanitized_name:
            cmd.extend(["--instance-filter", dashboard_name])
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            # Print the output to show dashboard URL
            if result.stdout:
                print(result.stdout.strip())
            return True
        else:
            print(f"[ERROR] Dashboard setup failed")
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(f"stderr: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"[ERROR] Failed to set up CloudWatch dashboard: {e}")
        return False


def run_local_monitoring(config: dict, raw_data_dir: str = None, machine_name: str = None):
    """Run monitoring locally.
    
    This function:
    1. Determines the machine identifier (IP, hostname, or custom name)
    2. Sets up a CloudWatch dashboard (non-fatal if fails)
    3. Runs the continuous monitoring script
    """
    print("Running continuous monitoring locally...")
    
    # Determine machine identifier
    if machine_name:
        instance_id = machine_name
        print(f"Using custom machine name: {instance_id}")
    else:
        # Check if running on EC2 instance
        try:
            import urllib.request
            response = urllib.request.urlopen(
                'http://169.254.169.254/latest/meta-data/instance-id',
                timeout=1
            )
            instance_id = response.read().decode('utf-8')
            print(f"Detected running on EC2 instance: {instance_id}")
        except:
            # Get meaningful local identifier
            instance_id = get_local_machine_identifier()
            print(f"Running on local machine: {instance_id}")
    
    # Set up CloudWatch dashboard
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if not setup_dashboard(instance_id, config_path):
        print("[WARN] Failed to set up CloudWatch dashboard, continuing anyway")
        # Continue with monitoring even if dashboard setup fails
    
    # Prepare IP list
    ip_list_file = os.path.join(config['ip_list_dir'], "ip_list_latest.json")
    if not os.path.exists(ip_list_file):
        print(f"[ERROR] IP list file not found: {ip_list_file}")
        print("[INFO] Run 'python3 discover_ips.py' to generate IP list")
        return False
    
    # Prepare config for monitoring
    monitor_config = {
        'region': config['region'],
        'monitoring_domains': config['monitoring_domains'],
        'tcp_connection_timeout_ms': config.get('tcp_connection_timeout_ms', 3000)  # Include TCP timeout
    }
    
    # Create temporary directory for monitoring
    monitor_dir = "/tmp/binance-monitor"
    os.makedirs(monitor_dir, exist_ok=True)
    
    # Copy files
    with open(os.path.join(monitor_dir, "config.json"), 'w') as f:
        json.dump(monitor_config, f, indent=2)
    
    # Create simplified IP list for monitoring (like test_instance_latency.py approach)
    simplified_ip_list = _create_simplified_ip_list_for_monitoring(ip_list_file, config['monitoring_domains'])
    with open(os.path.join(monitor_dir, "ip_list_latest.json"), 'w') as f:
        json.dump(simplified_ip_list, f, indent=2)
    
    # Get monitoring script path
    monitor_script = os.path.join(
        os.path.dirname(__file__), 
        "core", "monitoring", "continuous_latency_monitor.py"
    )
    
    # Build command
    cmd = [
        "python3", monitor_script,
        "--ip-list", os.path.join(monitor_dir, "ip_list_latest.json"),
        "--config", os.path.join(monitor_dir, "config.json"),
        "--instance-id", instance_id
    ]
    
    print("\n[INFO] Running with CloudWatch publishing enabled")
    print("[WARN] Ensure you have AWS credentials configured for CloudWatch access")
    
    if raw_data_dir:
        cmd.extend(["--store-raw-data-locally", raw_data_dir])
        print(f"[INFO] Raw data will be stored in: {raw_data_dir}")
    
    print(f"\nStarting monitoring with command: {' '.join(cmd)}")
    print("Press Ctrl+C to stop monitoring\n")
    
    try:
        # Run monitoring
        process = subprocess.Popen(cmd)
        process.wait()
    except KeyboardInterrupt:
        print("\n[INFO] Stopping monitoring...")
        process.terminate()
        process.wait()
    
    return True


def deploy_remote_monitoring(instance_id: str, config: dict, no_service: bool = False):
    """Deploy monitoring to remote instance."""
    # Import deployment module
    sys.path.insert(0, os.path.dirname(__file__))
    from core.monitoring.deploy_monitoring import MonitoringDeployer
    from core.config import Config
    
    # Create config object
    config_obj = Config()
    
    # Get instance IP
    print(f"Getting public IP for {instance_id}...")
    public_ip, error = get_instance_public_ip(instance_id, config['region'])
    
    if not public_ip:
        print(f"[ERROR] {error}")
        return False
    
    print(f"Instance public IP: {public_ip}")
    
    # Create deployer
    deployer = MonitoringDeployer(config_obj)
    
    if no_service:
        print("\n[INFO] Deploying monitoring script only (no systemd service)")
        
        # Just deploy files without service setup
        if not deployer._deploy_files_via_ssh(public_ip):
            print("[ERROR] Failed to deploy monitoring files")
            return False
        
        print("\n[OK] Monitoring files deployed successfully")
        print("\nTo run monitoring manually on the instance:")
        print(f"ssh -i {config['key_path']} ec2-user@{public_ip}")
        print("python3 /opt/binance-monitor/monitor.py")
    else:
        print("\n[INFO] Deploying full monitoring with systemd service")
        
        # Full deployment with IAM role and service
        if not deployer.deploy_monitoring(instance_id, public_ip):
            print("[ERROR] Failed to deploy monitoring")
            return False
        
        print("\n[OK] Monitoring deployed and started successfully")
        print("\nUseful commands:")
        print(f"# Check status:")
        print(f"ssh -i {config['key_path']} ec2-user@{public_ip} 'sudo systemctl status binance-latency-monitor'")
        print(f"\n# View logs:")
        print(f"ssh -i {config['key_path']} ec2-user@{public_ip} 'sudo journalctl -u binance-latency-monitor -f'")
        print(f"\n# Stop monitoring:")
        print(f"ssh -i {config['key_path']} ec2-user@{public_ip} 'sudo systemctl stop binance-latency-monitor'")
    
    return True


def _create_simplified_ip_list_for_monitoring(ip_list_file: str, monitoring_domains: list) -> dict:
    """Create simplified IP list format for monitoring (domain -> IP list).
    
    This function follows the same approach as test_instance_latency.py,
    removing metadata and timestamps to keep only essential IP mappings.
    
    Args:
        ip_list_file: Path to the full IP list file
        monitoring_domains: List of domains to include
        
    Returns:
        Dict mapping domain names to IP lists
    """
    try:
        with open(ip_list_file, 'r') as f:
            ip_data = json.load(f)
        
        simplified_list = {}
        
        # Handle full metadata format from discover_ips.py
        if 'domains' in ip_data:
            all_domains = ip_data.get('domains', {})
            
            for domain in monitoring_domains:
                if domain in all_domains:
                    # Extract just the IP addresses (keys from the IPs dict)
                    ips = list(all_domains[domain].get('ips', {}).keys())
                    if ips:
                        simplified_list[domain] = ips
        else:
            # Already simplified format - just filter to monitoring domains
            for domain in monitoring_domains:
                if domain in ip_data and isinstance(ip_data[domain], list):
                    simplified_list[domain] = ip_data[domain]
        
        print(f"[INFO] Created simplified IP list with {sum(len(ips) for ips in simplified_list.values())} IPs across {len(simplified_list)} domains")
        return simplified_list
        
    except Exception as e:
        print(f"[ERROR] Failed to create simplified IP list: {e}")
        return {}


def main():
    parser = argparse.ArgumentParser(
        description="Run continuous latency monitoring locally or on remote instances",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run monitoring locally with CloudWatch
  python3 run_latency_monitoring.py
  
  # Run monitoring locally with raw data storage
  python3 run_latency_monitoring.py --store-raw-data-locally ~/my-monitoring-data
  
  # Run monitoring locally with custom machine name
  python3 run_latency_monitoring.py --machine-name my-trading-server
  
  # Deploy to remote instance with systemd service
  python3 run_latency_monitoring.py i-1234567890abcdef0
  
  # Deploy to remote instance without systemd service
  python3 run_latency_monitoring.py i-1234567890abcdef0 --no-service
"""
    )
    
    parser.add_argument('instance_id', nargs='?', help='EC2 instance ID (optional, runs locally if not provided)')
    parser.add_argument('--no-service', action='store_true',
                       help='Deploy files only, without systemd service (remote only)')
    parser.add_argument('--config', default='config.json',
                       help='Path to config file (default: config.json)')
    parser.add_argument('--store-raw-data-locally', nargs='?', const='.',
                       help='Store raw data locally. Optionally specify directory (default: current directory)')
    parser.add_argument('--machine-name', 
                       help='Custom machine name for CloudWatch metrics (default: auto-detect public IP or EC2 instance ID)')
    
    args = parser.parse_args()
    
    # Load config
    config_path = os.path.join(os.path.dirname(__file__), args.config)
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load config: {e}")
        sys.exit(1)
    
    # Check for required directories
    if not os.path.exists(config['ip_list_dir']):
        print(f"[ERROR] IP list directory not found: {config['ip_list_dir']}")
        print("[INFO] Run initial setup: python3 discover_ips.py")
        sys.exit(1)
    
    # Run local or remote
    if args.instance_id:
        # Remote deployment
        success = deploy_remote_monitoring(args.instance_id, config, args.no_service)
    else:
        # Local execution
        if args.no_service:
            print("[ERROR] --no-service flag is only for remote deployment")
            sys.exit(1)
        
        success = run_local_monitoring(config, args.store_raw_data_locally, args.machine_name)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()