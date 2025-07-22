#!/usr/bin/env python3
"""
AWS Resource Cleanup Script for DC Machine Project
Removes all resources created by setup_aws_resources.py
"""
import boto3
import json
import os
import sys
import time

def load_config():
    """Load configuration from file"""
    try:
        with open('aws_resources_config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("✗ Configuration file 'aws_resources_config.json' not found")
        print("  Run setup_aws_resources.py first or provide resource IDs manually")
        return None
    except Exception as e:
        print(f"✗ Error loading config: {e}")
        return None

def load_champion_state():
    """Load champion state to avoid terminating champion instances"""
    champion_file = "./reports/champion_state.json"
    if os.path.exists(champion_file):
        try:
            with open(champion_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️  Could not load champion state: {e}")
    return {}

def terminate_instances(ec2_client, vpc_id, champion_state):
    """Terminate all instances in the VPC except champion instances"""
    print("\n1. Terminating EC2 instances...")
    
    champion_instance_id = champion_state.get('instance_id')
    if champion_instance_id:
        print(f"   🛡️  Protected champion instance: {champion_instance_id}")
    
    try:
        instances = ec2_client.describe_instances(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]},
                {'Name': 'instance-state-name', 'Values': ['running', 'stopped', 'stopping', 'pending']}
            ]
        )
        
        instance_ids = []
        champion_skipped = []
        
        for reservation in instances['Reservations']:
            for instance in reservation['Instances']:
                instance_id = instance['InstanceId']
                
                # Skip champion instances
                if instance_id == champion_instance_id:
                    champion_skipped.append(instance_id)
                    continue
                    
                instance_ids.append(instance_id)
        
        if champion_skipped:
            print(f"   🛡️  Skipped {len(champion_skipped)} champion instance(s): {', '.join(champion_skipped)}")
        
        if instance_ids:
            print(f"   Found {len(instance_ids)} instances to terminate")
            ec2_client.terminate_instances(InstanceIds=instance_ids)
            
            # Wait for termination
            print("   Waiting for instances to terminate...")
            waiter = ec2_client.get_waiter('instance_terminated')
            waiter.wait(InstanceIds=instance_ids)
            print("   ✓ All non-champion instances terminated")
        else:
            print("   No instances to terminate (all are protected champions)")
            
    except Exception as e:
        print(f"   ✗ Error terminating instances: {e}")

def release_eip(ec2_client, allocation_id):
    """Release Elastic IP"""
    print("\n2. Releasing Elastic IP...")
    try:
        # First check if it's associated
        eip_info = ec2_client.describe_addresses(AllocationIds=[allocation_id])
        if eip_info['Addresses'][0].get('AssociationId'):
            print("   Disassociating EIP first...")
            ec2_client.disassociate_address(
                AssociationId=eip_info['Addresses'][0]['AssociationId']
            )
            time.sleep(2)
        
        ec2_client.release_address(AllocationId=allocation_id)
        print("   ✓ EIP released")
    except Exception as e:
        print(f"   ✗ Error releasing EIP: {e}")

def delete_key_pair(ec2_client, key_name):
    """Delete key pair"""
    print("\n3. Deleting key pair...")
    try:
        ec2_client.delete_key_pair(KeyName=key_name)
        print(f"   ✓ Key pair '{key_name}' deleted from AWS")
        print(f"   Note: Local key file at ~/.ssh/{key_name}.pem was not deleted")
    except Exception as e:
        print(f"   ✗ Error deleting key pair: {e}")

def delete_placement_groups(ec2_client, champion_state):
    """Delete all dc-machine placement groups except champion placement groups"""
    print("\n4. Deleting placement groups...")
    
    champion_pg = champion_state.get('placement_group')
    if champion_pg:
        print(f"   🛡️  Protected champion placement group: {champion_pg}")
    
    try:
        # List all placement groups
        pgs = ec2_client.describe_placement_groups()
        dc_pgs = [pg['GroupName'] for pg in pgs['PlacementGroups'] 
                  if pg['GroupName'].startswith('dc-machine-cpg')]
        
        if dc_pgs:
            print(f"   Found {len(dc_pgs)} placement group(s) to delete")
            deleted = 0
            in_use = 0
            
            champion_protected = 0
            
            for pg_name in dc_pgs:
                try:
                    # Skip champion placement groups
                    if pg_name == champion_pg:
                        print(f"   🛡️  Skipped champion placement group '{pg_name}'")
                        champion_protected += 1
                        continue
                    
                    # First check if any instances are using this PG
                    instances = ec2_client.describe_instances(
                        Filters=[
                            {'Name': 'placement-group-name', 'Values': [pg_name]},
                            {'Name': 'instance-state-name', 
                             'Values': ['pending', 'running', 'stopping', 'stopped', 'shutting-down']}
                        ]
                    )
                    
                    instance_count = sum(len(r['Instances']) for r in instances['Reservations'])
                    
                    if instance_count > 0:
                        print(f"   ⚠️  Placement group '{pg_name}' has {instance_count} instances - skipping")
                        in_use += 1
                    else:
                        # No instances, safe to delete
                        ec2_client.delete_placement_group(GroupName=pg_name)
                        print(f"   ✓ Deleted placement group '{pg_name}'")
                        deleted += 1
                        
                except Exception as e:
                    if 'is in use' in str(e):
                        print(f"   ⚠️  Placement group '{pg_name}' is in use - may have terminating instances")
                        in_use += 1
                    else:
                        print(f"   ✗ Error deleting placement group '{pg_name}': {e}")
            
            if in_use > 0:
                print(f"\n   Note: {in_use} placement group(s) still have instances.")
                print("   Wait for all instances to fully terminate, then run cleanup again.")
                
        else:
            print("   No placement groups found")
    except Exception as e:
        print(f"   ✗ Error listing placement groups: {e}")

def delete_security_group(ec2_client, sg_id):
    """Delete security group"""
    print("\n5. Deleting security group...")
    try:
        # Wait a bit for instances to fully terminate
        time.sleep(5)
        ec2_client.delete_security_group(GroupId=sg_id)
        print(f"   ✓ Security group deleted")
    except Exception as e:
        print(f"   ✗ Error deleting security group: {e}")

def delete_subnet(ec2_client, subnet_id):
    """Delete subnet"""
    print("\n6. Deleting subnet...")
    try:
        ec2_client.delete_subnet(SubnetId=subnet_id)
        print(f"   ✓ Subnet deleted")
    except Exception as e:
        print(f"   ✗ Error deleting subnet: {e}")

def detach_and_delete_igw(ec2_client, vpc_id, igw_id):
    """Detach and delete internet gateway"""
    print("\n7. Deleting internet gateway...")
    try:
        # Detach from VPC
        ec2_client.detach_internet_gateway(
            InternetGatewayId=igw_id,
            VpcId=vpc_id
        )
        print("   ✓ Detached from VPC")
        
        # Delete IGW
        ec2_client.delete_internet_gateway(InternetGatewayId=igw_id)
        print("   ✓ Internet gateway deleted")
    except Exception as e:
        print(f"   ✗ Error deleting IGW: {e}")

def delete_vpc(ec2_client, vpc_id):
    """Delete VPC"""
    print("\n8. Deleting VPC...")
    try:
        ec2_client.delete_vpc(VpcId=vpc_id)
        print(f"   ✓ VPC deleted")
    except Exception as e:
        print(f"   ✗ Error deleting VPC: {e}")

def main():
    print("\nAWS Resource Cleanup for DC Machine Project")
    print("="*60)
    
    # Load configuration
    config = load_config()
    if not config:
        return 1
    
    # Load champion state to protect champions
    champion_state = load_champion_state()
    
    print(f"Region: {config.get('region', 'Unknown')}")
    print(f"VPC ID: {config.get('vpc_id', 'Unknown')}")
    if champion_state.get('instance_id'):
        print(f"🛡️  Protected Champion: {champion_state['instance_id']} ({champion_state.get('placement_group', 'N/A')})")
    print("="*60)
    
    # Confirm deletion
    response = input("\n⚠️  This will delete all resources. Continue? (yes/no): ")
    if response.lower() != 'yes':
        print("Cleanup cancelled.")
        return 0
    
    # Initialize boto3 client
    try:
        ec2_client = boto3.client('ec2', region_name=config['region'])
    except Exception as e:
        print(f"\n✗ Error initializing AWS client: {e}")
        return 1
    
    # Delete resources in order
    # 1. Terminate all instances first (except champions)
    terminate_instances(ec2_client, config['vpc_id'], champion_state)
    
    # 2. Release EIP
    if 'eip_allocation_id' in config:
        release_eip(ec2_client, config['eip_allocation_id'])
    
    # 3. Delete key pair
    if 'key_name' in config:
        delete_key_pair(ec2_client, config['key_name'])
    
    # 4. Delete placement groups (except champion placement groups)
    delete_placement_groups(ec2_client, champion_state)
    
    # 5. Delete security group
    if 'security_group_id' in config:
        delete_security_group(ec2_client, config['security_group_id'])
    
    # 6. Delete subnet
    if 'subnet_id' in config:
        delete_subnet(ec2_client, config['subnet_id'])
    
    # 7. Detach and delete IGW
    if 'internet_gateway_id' in config and 'vpc_id' in config:
        detach_and_delete_igw(ec2_client, config['vpc_id'], config['internet_gateway_id'])
    
    # 8. Delete VPC
    if 'vpc_id' in config:
        delete_vpc(ec2_client, config['vpc_id'])
    
    print("\n✓ Cleanup completed!")
    print("\nNote: The configuration file and local SSH key were not deleted.")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())