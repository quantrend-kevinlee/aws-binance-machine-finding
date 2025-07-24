#!/usr/bin/env python3
"""
Bind Elastic IP to an EC2 instance and provide SSH command.

Usage: python3 bind_eip.py <instance-id>
"""

import boto3
import sys
import json
import os
import time


def load_config():
    """Load configuration from config.json"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
    try:
        with open(config_path, "r") as f:
            return json.load(f), config_path
    except FileNotFoundError:
        print("Error: config.json not found in project root")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing config.json: {e}")
        sys.exit(1)


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 bind_eip.py <instance-id>")
        print("Example: python3 bind_eip.py i-0123456789abcdef0")
        sys.exit(1)
    
    instance_id = sys.argv[1]
    
    # Load configuration
    config, config_path = load_config()
    region = config['region']
    eip_allocation_id = config['eip_allocation_id']
    key_path = os.path.expanduser(config['key_path'])
    
    # Initialize EC2 client
    ec2 = boto3.client('ec2', region_name=region)
    
    print(f"Binding EIP to instance {instance_id}...")
    
    try:
        # Check if instance exists and is running
        print("Checking instance status...")
        instance_resp = ec2.describe_instances(InstanceIds=[instance_id])
        if not instance_resp['Reservations']:
            print(f"Error: Instance {instance_id} not found")
            sys.exit(1)
        
        instance = instance_resp['Reservations'][0]['Instances'][0]
        state = instance['State']['Name']
        
        if state != 'running':
            print(f"Warning: Instance is in '{state}' state (should be 'running')")
            if state in ['terminated', 'terminating']:
                print("Error: Cannot bind EIP to terminated instance")
                sys.exit(1)
        
        # Check if EIP is already associated
        eip_resp = ec2.describe_addresses(AllocationIds=[eip_allocation_id])
        if not eip_resp['Addresses']:
            print(f"Error: EIP allocation {eip_allocation_id} not found")
            sys.exit(1)
        
        eip_info = eip_resp['Addresses'][0]
        current_instance = eip_info.get('InstanceId')
        
        if current_instance:
            if current_instance == instance_id:
                print(f"EIP is already associated with {instance_id}")
            else:
                print(f"EIP is currently associated with {current_instance}")
                print("Disassociating...")
                association_id = eip_info.get('AssociationId')
                if association_id:
                    ec2.disassociate_address(AssociationId=association_id)
                    time.sleep(2)  # Give AWS a moment
        
        # Associate EIP with the instance
        print(f"Associating EIP with {instance_id}...")
        ec2.associate_address(
            InstanceId=instance_id,
            AllocationId=eip_allocation_id
        )
        
        # Get the public IP
        time.sleep(2)  # Give AWS a moment to update
        eip_resp = ec2.describe_addresses(AllocationIds=[eip_allocation_id])
        public_ip = eip_resp['Addresses'][0]['PublicIp']
        
        print(f"\n✓ Success! EIP {public_ip} is now associated with {instance_id}")
        
        # Get instance details
        instance_type = instance.get('InstanceType', 'Unknown')
        instance_name = 'Unknown'
        for tag in instance.get('Tags', []):
            if tag['Key'] == 'Name':
                instance_name = tag['Value']
                break
        
        print(f"\nInstance Details:")
        print(f"  Name: {instance_name}")
        print(f"  Type: {instance_type}")
        print(f"  State: {state}")
        
        # Provide SSH commands
        print(f"\nSSH Access:")
        print(f"  Standard SSH:")
        print(f"    ssh -i {key_path} ec2-user@{public_ip}")
        print(f"\n  SSH without host key checking (useful for dynamic IPs):")
        print(f"    ssh -i {key_path} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ec2-user@{public_ip}")
        
        # Check if this is a champion
        champion_state_file = os.path.join(os.path.dirname(config_path), "reports", "champion_state.json")
        if os.path.exists(champion_state_file):
            with open(champion_state_file, 'r') as f:
                champion_state = json.load(f)
                champions = champion_state.get('champions', {})
                champion_domains = []
                for domain, info in champions.items():
                    if info.get('instance_id') == instance_id:
                        champion_domains.append(domain)
                
                if champion_domains:
                    print(f"\n⭐ This is a CHAMPION instance for:")
                    for domain in champion_domains:
                        print(f"    - {domain}")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()