#!/usr/bin/env python3
"""
AWS Resource Setup Script for DC Machine Project
Creates all required AWS resources and outputs configuration
"""
import boto3
import os
import sys
import time
import json
from datetime import datetime

# Configuration
REGION = "ap-northeast-1"
BEST_AZ = "ap-northeast-1a"  # Binance location
PROJECT_NAME = "dc-machine"

def create_vpc(ec2_client):
    """Create VPC with DNS enabled"""
    print("\n1. Creating VPC...")
    try:
        # Create VPC
        vpc_response = ec2_client.create_vpc(CidrBlock='10.0.0.0/16')
        vpc_id = vpc_response['Vpc']['VpcId']
        print(f"   Created VPC: {vpc_id}")
        
        # Enable DNS
        ec2_client.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={'Value': True})
        ec2_client.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={'Value': True})
        print("   ✓ DNS support and hostnames enabled")
        
        # Tag the VPC
        ec2_client.create_tags(
            Resources=[vpc_id],
            Tags=[{'Key': 'Name', 'Value': f'{PROJECT_NAME}-vpc'}]
        )
        
        # Wait for VPC to be available
        waiter = ec2_client.get_waiter('vpc_available')
        waiter.wait(VpcIds=[vpc_id])
        
        return vpc_id
    except Exception as e:
        print(f"   ✗ Error creating VPC: {e}")
        return None

def create_subnet(ec2_client, vpc_id):
    """Create subnet in the specified AZ"""
    print(f"\n2. Creating subnet in {BEST_AZ}...")
    try:
        subnet_response = ec2_client.create_subnet(
            VpcId=vpc_id,
            CidrBlock='10.0.1.0/24',
            AvailabilityZone=BEST_AZ
        )
        subnet_id = subnet_response['Subnet']['SubnetId']
        print(f"   Created subnet: {subnet_id}")
        
        # Enable auto-assign public IP
        ec2_client.modify_subnet_attribute(
            SubnetId=subnet_id,
            MapPublicIpOnLaunch={'Value': True}
        )
        print("   ✓ Auto-assign public IP enabled")
        
        # Tag the subnet
        ec2_client.create_tags(
            Resources=[subnet_id],
            Tags=[{'Key': 'Name', 'Value': f'{PROJECT_NAME}-subnet-{BEST_AZ}'}]
        )
        
        return subnet_id
    except Exception as e:
        print(f"   ✗ Error creating subnet: {e}")
        return None

def create_internet_gateway(ec2_client, vpc_id):
    """Create and attach internet gateway"""
    print("\n3. Creating Internet Gateway...")
    try:
        # Create IGW
        igw_response = ec2_client.create_internet_gateway()
        igw_id = igw_response['InternetGateway']['InternetGatewayId']
        print(f"   Created IGW: {igw_id}")
        
        # Attach to VPC
        ec2_client.attach_internet_gateway(
            InternetGatewayId=igw_id,
            VpcId=vpc_id
        )
        print("   ✓ Attached to VPC")
        
        # Tag the IGW
        ec2_client.create_tags(
            Resources=[igw_id],
            Tags=[{'Key': 'Name', 'Value': f'{PROJECT_NAME}-igw'}]
        )
        
        return igw_id
    except Exception as e:
        print(f"   ✗ Error creating IGW: {e}")
        return None

def setup_route_table(ec2_client, vpc_id, subnet_id, igw_id):
    """Configure route table for internet access"""
    print("\n4. Configuring route table...")
    try:
        # Get the main route table for the VPC
        route_tables = ec2_client.describe_route_tables(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )
        route_table_id = route_tables['RouteTables'][0]['RouteTableId']
        
        # Add route to IGW
        ec2_client.create_route(
            RouteTableId=route_table_id,
            DestinationCidrBlock='0.0.0.0/0',
            GatewayId=igw_id
        )
        print(f"   ✓ Added internet route via {igw_id}")
        
        # Associate with subnet
        ec2_client.associate_route_table(
            RouteTableId=route_table_id,
            SubnetId=subnet_id
        )
        print("   ✓ Associated with subnet")
        
        return route_table_id
    except Exception as e:
        print(f"   ✗ Error configuring routes: {e}")
        return None

def create_security_group(ec2_client, vpc_id):
    """Create security group with required rules"""
    print("\n5. Creating security group...")
    try:
        # Get your current IP
        import urllib.request
        my_ip = urllib.request.urlopen('https://api.ipify.org').read().decode('utf-8')
        print(f"   Your IP: {my_ip}")
        
        # Create security group
        sg_response = ec2_client.create_security_group(
            GroupName=f'{PROJECT_NAME}-sg',
            Description='Security group for DC machine project',
            VpcId=vpc_id
        )
        sg_id = sg_response['GroupId']
        print(f"   Created security group: {sg_id}")
        
        # Add ingress rules
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 22,
                    'ToPort': 22,
                    'IpRanges': [{'CidrIp': f'{my_ip}/32', 'Description': 'SSH from setup location'}]
                }
            ]
        )
        print("   ✓ Added SSH ingress rule")
        
        # Add egress rules (all traffic allowed by default, but let's be explicit)
        # Default egress rule allows all traffic, so we don't need to add it
        
        # Tag the security group
        ec2_client.create_tags(
            Resources=[sg_id],
            Tags=[{'Key': 'Name', 'Value': f'{PROJECT_NAME}-sg'}]
        )
        
        return sg_id
    except Exception as e:
        print(f"   ✗ Error creating security group: {e}")
        return None

def create_key_pair(ec2_client):
    """Create SSH key pair"""
    print("\n6. Creating SSH key pair...")
    key_name = PROJECT_NAME
    key_path = os.path.expanduser(f"~/.ssh/{key_name}.pem")
    
    # Check if key already exists locally
    if os.path.exists(key_path):
        print(f"   ⚠️  Key file already exists at {key_path}")
        print("   Checking if key exists in AWS...")
        
        try:
            ec2_client.describe_key_pairs(KeyNames=[key_name])
            print(f"   ✓ Key pair '{key_name}' already exists in AWS")
            return key_name
        except:
            print(f"   Key pair '{key_name}' not found in AWS, will import from local file")
            
            # Read the public key from the private key
            try:
                import subprocess
                result = subprocess.run(
                    ['ssh-keygen', '-y', '-f', key_path],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    public_key = result.stdout.strip()
                    ec2_client.import_key_pair(
                        KeyName=key_name,
                        PublicKeyMaterial=public_key
                    )
                    print(f"   ✓ Imported existing key pair to AWS")
                    return key_name
                else:
                    print(f"   ✗ Could not read public key from {key_path}")
                    return None
            except Exception as e:
                print(f"   ✗ Error importing key: {e}")
                return None
    
    # Create new key pair
    try:
        key_response = ec2_client.create_key_pair(KeyName=key_name)
        
        # Save private key
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        with open(key_path, 'w') as key_file:
            key_file.write(key_response['KeyMaterial'])
        
        # Set proper permissions
        os.chmod(key_path, 0o400)
        print(f"   ✓ Created key pair '{key_name}'")
        print(f"   ✓ Saved private key to {key_path}")
        
        return key_name
    except Exception as e:
        print(f"   ✗ Error creating key pair: {e}")
        return None

def create_placement_group(ec2_client):
    """Create cluster placement group"""
    print("\n7. Creating placement group...")
    pg_name = f"{PROJECT_NAME}-cpg"
    
    try:
        # Check if it already exists
        ec2_client.describe_placement_groups(GroupNames=[pg_name])
        print(f"   ✓ Placement group '{pg_name}' already exists")
        return pg_name
    except:
        # Create new placement group
        try:
            ec2_client.create_placement_group(
                GroupName=pg_name,
                Strategy='cluster'
            )
            print(f"   ✓ Created placement group '{pg_name}'")
            return pg_name
        except Exception as e:
            print(f"   ✗ Error creating placement group: {e}")
            return None

def allocate_eip(ec2_client):
    """Allocate Elastic IP"""
    print("\n8. Allocating Elastic IP...")
    try:
        eip_response = ec2_client.allocate_address(Domain='vpc')
        allocation_id = eip_response['AllocationId']
        public_ip = eip_response['PublicIp']
        
        print(f"   ✓ Allocated EIP: {public_ip}")
        print(f"   Allocation ID: {allocation_id}")
        
        # Tag the EIP
        ec2_client.create_tags(
            Resources=[allocation_id],
            Tags=[{'Key': 'Name', 'Value': f'{PROJECT_NAME}-eip'}]
        )
        
        return allocation_id
    except Exception as e:
        print(f"   ✗ Error allocating EIP: {e}")
        return None

def save_config(config):
    """Save configuration to file"""
    config_file = "aws_resources_config.json"
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"\n✓ Configuration saved to {config_file}")

def generate_python_config(config):
    """Generate Python configuration snippet"""
    print("\n" + "="*60)
    print("PYTHON CONFIGURATION")
    print("="*60)
    print("Copy and paste the following into your Python scripts:\n")
    
    print(f'REGION = "{config["region"]}"')
    print(f'BEST_AZ = "{config["availability_zone"]}"')
    print(f'SUBNET_ID = "{config["subnet_id"]}"')
    print(f'SECURITY_GROUP_ID = "{config["security_group_id"]}"')
    print(f'KEY_NAME = "{config["key_name"]}"')
    print(f'KEY_PATH = os.path.expanduser("~/.ssh/{config["key_name"]}.pem")')
    print(f'EIP_ALLOC_ID = "{config["eip_allocation_id"]}"')
    print(f'PLACEMENT_GROUP_NAME = "{config["placement_group_name"]}"')
    
    print("\n" + "="*60)

def main():
    print(f"\nAWS Resource Setup for {PROJECT_NAME}")
    print("="*60)
    print(f"Region: {REGION}")
    print(f"Availability Zone: {BEST_AZ}")
    print("="*60)
    
    # Initialize boto3 client
    try:
        ec2_client = boto3.client('ec2', region_name=REGION)
    except Exception as e:
        print(f"\n✗ Error initializing AWS client: {e}")
        print("Make sure you have AWS credentials configured!")
        return 1
    
    # Create resources
    config = {
        "region": REGION,
        "availability_zone": BEST_AZ,
        "created_at": datetime.now().isoformat()
    }
    
    # 1. Create VPC
    vpc_id = create_vpc(ec2_client)
    if not vpc_id:
        return 1
    config["vpc_id"] = vpc_id
    
    # 2. Create Subnet
    subnet_id = create_subnet(ec2_client, vpc_id)
    if not subnet_id:
        return 1
    config["subnet_id"] = subnet_id
    
    # 3. Create Internet Gateway
    igw_id = create_internet_gateway(ec2_client, vpc_id)
    if not igw_id:
        return 1
    config["internet_gateway_id"] = igw_id
    
    # 4. Setup Route Table
    route_table_id = setup_route_table(ec2_client, vpc_id, subnet_id, igw_id)
    if not route_table_id:
        return 1
    config["route_table_id"] = route_table_id
    
    # 5. Create Security Group
    sg_id = create_security_group(ec2_client, vpc_id)
    if not sg_id:
        return 1
    config["security_group_id"] = sg_id
    
    # 6. Create Key Pair
    key_name = create_key_pair(ec2_client)
    if not key_name:
        return 1
    config["key_name"] = key_name
    
    # 7. Create Placement Group
    pg_name = create_placement_group(ec2_client)
    if not pg_name:
        return 1
    config["placement_group_name"] = pg_name
    
    # 8. Allocate EIP
    eip_id = allocate_eip(ec2_client)
    if not eip_id:
        return 1
    config["eip_allocation_id"] = eip_id
    
    # Save configuration
    save_config(config)
    
    # Generate Python config
    generate_python_config(config)
    
    print("\n✓ All resources created successfully!")
    print(f"\nNext steps:")
    print(f"1. Copy the Python configuration above into your scripts")
    print(f"2. Run: python3 find_small_anchor_ssh.py")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())