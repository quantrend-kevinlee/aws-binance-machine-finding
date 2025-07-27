#!/usr/bin/env python3
"""
SSH into an EC2 instance by instance ID.
Automatically binds EIP if needed and uses SSH options to avoid known_hosts issues.

Usage: python3 ssh_instance.py <instance-id> [command]
"""

import boto3
import sys
import json
import os
import subprocess
import time


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


def bind_eip_to_instance(ec2, instance_id, eip_allocation_id):
    """Bind EIP to instance if not already bound"""
    # Check current EIP association
    eip_resp = ec2.describe_addresses(AllocationIds=[eip_allocation_id])
    if not eip_resp['Addresses']:
        print(f"Error: EIP allocation {eip_allocation_id} not found")
        return None
    
    eip_info = eip_resp['Addresses'][0]
    current_instance = eip_info.get('InstanceId')
    public_ip = eip_info['PublicIp']
    
    if current_instance == instance_id:
        print(f"EIP {public_ip} already associated with {instance_id}")
        return public_ip
    
    if current_instance:
        print(f"Disassociating EIP from {current_instance}...")
        association_id = eip_info.get('AssociationId')
        if association_id:
            ec2.disassociate_address(AssociationId=association_id)
            time.sleep(2)
    
    print(f"Associating EIP with {instance_id}...")
    ec2.associate_address(
        InstanceId=instance_id,
        AllocationId=eip_allocation_id
    )
    time.sleep(2)
    
    return public_ip


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
    eip_allocation_id = config['eip_allocation_id']
    key_path = os.path.expanduser(config['key_path'])
    
    # Initialize EC2 client
    ec2 = boto3.client('ec2', region_name=region)
    
    try:
        # Check instance status
        instance_resp = ec2.describe_instances(InstanceIds=[instance_id])
        if not instance_resp['Reservations']:
            print(f"Error: Instance {instance_id} not found")
            sys.exit(1)
        
        instance = instance_resp['Reservations'][0]['Instances'][0]
        state = instance['State']['Name']
        
        if state != 'running':
            print(f"Error: Instance is in '{state}' state (must be 'running')")
            sys.exit(1)
        
        # Bind EIP
        public_ip = bind_eip_to_instance(ec2, instance_id, eip_allocation_id)
        if not public_ip:
            print("Error: Failed to get public IP")
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