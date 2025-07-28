#!/usr/bin/env python3
"""
SSH into an EC2 instance by instance ID.
Uses the instance's existing public IP (auto-assigned or EIP).

Usage: python3 ssh_instance.py <instance-id> [command]
"""

import boto3
import sys
import json
import os
import subprocess


def load_config():
    """Load configuration from config.json"""
    try:
        # Look for config.json in the parent directory
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
        with open(config_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: config.json not found in project root")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing config.json: {e}")
        sys.exit(1)


def get_instance_public_ip(ec2, instance_id):
    """Get the public IP of an instance."""
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


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 ssh_instance.py <instance-id> [command]")
        print("\nExamples:")
        print("  python3 ssh_instance.py i-0123456789abcdef0")
        print("  python3 ssh_instance.py i-0123456789abcdef0 'df -h'")
        print("  python3 ssh_instance.py i-0123456789abcdef0 'cat /tmp/latency_test.py'")
        sys.exit(1)
    
    instance_id = sys.argv[1]
    remote_command = sys.argv[2] if len(sys.argv) > 2 else None
    
    # Load configuration
    config = load_config()
    region = config['region']
    key_path = os.path.expanduser(config['key_path'])
    
    # Initialize EC2 client
    ec2 = boto3.client('ec2', region_name=region)
    
    try:
        # Get instance public IP
        public_ip, error = get_instance_public_ip(ec2, instance_id)
        
        if not public_ip:
            print(f"Error: {error}")
            sys.exit(1)
        
        # Build SSH command
        ssh_args = [
            "ssh",
            "-i", key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-o", "LogLevel=ERROR",
            f"ec2-user@{public_ip}"
        ]
        
        if remote_command:
            ssh_args.append(remote_command)
        
        print(f"\nConnecting to {instance_id} at {public_ip}...")
        if remote_command:
            print(f"Running command: {remote_command}")
        else:
            print("Starting interactive SSH session...")
            print("(SSH options set to ignore host key checking)\n")
        
        # Execute SSH
        result = subprocess.run(ssh_args)
        sys.exit(result.returncode)
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()