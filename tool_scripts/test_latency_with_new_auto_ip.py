#!/usr/bin/env python3
"""
Test instance latency after triggering auto-assigned public IP refresh.

This script forces AWS to assign a new auto-assigned public IP to an instance
by temporarily binding and unbinding an Elastic IP. This can be useful for:
- Testing if different auto-assigned IPs affect latency
- Refreshing a stuck or problematic auto-assigned IP
- Validating network path changes

The script:
1. Associates an EIP with the specified instance (replacing auto-assigned IP)
2. Immediately disassociates the EIP 
3. AWS automatically assigns a new public IP
4. Runs the latency test on the instance with its new IP

Usage: python3 test_latency_with_new_auto_ip.py <instance-id>
"""

import boto3
import sys
import json
import os
import subprocess
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
        print("Usage: python3 test_latency_with_new_auto_ip.py <instance-id>")
        print("Example: python3 test_latency_with_new_auto_ip.py i-0bb5bdc65afae5a13")
        sys.exit(1)
    
    instance_id = sys.argv[1]
    
    # Load configuration
    config, config_path = load_config()
    region = config['region']
    eip_allocation_id = config['eip_allocation_id']
    
    # Initialize EC2 client
    ec2 = boto3.client('ec2', region_name=region)
    
    print(f"Instance ID: {instance_id}")
    print(f"Region: {region}")
    print(f"EIP Allocation ID: {eip_allocation_id}")
    print("="*60)
    
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
        
        # Step 1: Associate EIP
        print(f"\n1. Associating EIP with instance {instance_id}...")
        associate_response = ec2.associate_address(
            InstanceId=instance_id,
            AllocationId=eip_allocation_id
        )
        
        association_id = associate_response['AssociationId']
        print(f"   ✓ EIP associated successfully")
        print(f"   Association ID: {association_id}")
        
        # Brief wait to ensure association is established
        time.sleep(2)
        
        # Get the EIP address for logging
        eip_resp = ec2.describe_addresses(AllocationIds=[eip_allocation_id])
        public_ip = eip_resp['Addresses'][0]['PublicIp']
        print(f"   Public IP: {public_ip}")
        
        # Step 2: Immediately disassociate EIP to trigger new auto-assigned IP
        print(f"\n2. Disassociating EIP to trigger new auto-assigned IP...")
        ec2.disassociate_address(AssociationId=association_id)
        print(f"   ✓ EIP disassociated successfully")
        
        # Wait for AWS to assign new public IP
        time.sleep(3)
        
        # Step 3: Run latency test with new auto-assigned IP
        print(f"\n3. Running latency test on instance {instance_id} with new auto-assigned IP...")
        
        # Run the test_instance_latency.py script (now at root level)
        test_script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_instance_latency.py")
        
        if not os.path.exists(test_script):
            print(f"Error: test_instance_latency.py not found at {test_script}")
            sys.exit(1)
        
        # Execute the latency test
        cmd = [sys.executable, test_script, instance_id]
        print(f"   Running: {' '.join(cmd)}")
        print("="*60)
        
        # Run the test and display output in real-time
        result = subprocess.run(cmd)
        
        if result.returncode != 0:
            print(f"\nError: Latency test failed with exit code {result.returncode}")
            sys.exit(result.returncode)
        
        print("\n✓ All operations completed successfully!")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main() 