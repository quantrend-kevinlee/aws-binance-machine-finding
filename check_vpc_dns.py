#!/usr/bin/env python3
"""
Check and enable DNS settings for VPC
"""
import boto3
import sys

# Configuration
REGION = "ap-northeast-1"
SUBNET_ID = "subnet-07954f36129e8beb1"  # From your config

def main():
    ec2 = boto3.client('ec2', region_name=REGION)
    
    # Get VPC ID from subnet
    print(f"Getting VPC ID from subnet {SUBNET_ID}...")
    try:
        subnet_response = ec2.describe_subnets(SubnetIds=[SUBNET_ID])
        vpc_id = subnet_response['Subnets'][0]['VpcId']
        print(f"Found VPC ID: {vpc_id}")
    except Exception as e:
        print(f"Error getting VPC ID: {e}")
        return 1
    
    # Check current DNS settings
    print(f"\nChecking DNS settings for VPC {vpc_id}...")
    try:
        vpc_response = ec2.describe_vpcs(VpcIds=[vpc_id])
        vpc = vpc_response['Vpcs'][0]
        
        # Get DNS attributes
        dns_support = ec2.describe_vpc_attribute(
            VpcId=vpc_id,
            Attribute='enableDnsSupport'
        )
        dns_hostnames = ec2.describe_vpc_attribute(
            VpcId=vpc_id,
            Attribute='enableDnsHostnames'
        )
        
        support_enabled = dns_support['EnableDnsSupport']['Value']
        hostnames_enabled = dns_hostnames['EnableDnsHostnames']['Value']
        
        print(f"Current settings:")
        print(f"  - enableDnsSupport: {support_enabled}")
        print(f"  - enableDnsHostnames: {hostnames_enabled}")
        
        # Check if both are enabled
        if support_enabled and hostnames_enabled:
            print("\n✓ DNS is properly configured for this VPC!")
            return 0
        
        # Enable DNS settings if needed
        print("\n⚠️  DNS settings need to be enabled.")
        
        # First enable DNS support (required before hostnames)
        if not support_enabled:
            print("Enabling DNS support...")
            try:
                ec2.modify_vpc_attribute(
                    VpcId=vpc_id,
                    EnableDnsSupport={'Value': True}
                )
                print("✓ DNS support enabled")
            except Exception as e:
                print(f"✗ Error enabling DNS support: {e}")
                return 1
        
        # Then enable DNS hostnames
        if not hostnames_enabled:
            print("Enabling DNS hostnames...")
            try:
                ec2.modify_vpc_attribute(
                    VpcId=vpc_id,
                    EnableDnsHostnames={'Value': True}
                )
                print("✓ DNS hostnames enabled")
            except Exception as e:
                print(f"✗ Error enabling DNS hostnames: {e}")
                return 1
        
        # Verify the changes
        print("\nVerifying changes...")
        dns_support = ec2.describe_vpc_attribute(
            VpcId=vpc_id,
            Attribute='enableDnsSupport'
        )
        dns_hostnames = ec2.describe_vpc_attribute(
            VpcId=vpc_id,
            Attribute='enableDnsHostnames'
        )
        
        support_enabled = dns_support['EnableDnsSupport']['Value']
        hostnames_enabled = dns_hostnames['EnableDnsHostnames']['Value']
        
        print(f"New settings:")
        print(f"  - enableDnsSupport: {support_enabled}")
        print(f"  - enableDnsHostnames: {hostnames_enabled}")
        
        if support_enabled and hostnames_enabled:
            print("\n✓ DNS is now properly configured for this VPC!")
            print("\nNote: EC2 instances will use the Amazon DNS server at:")
            print("  - 169.254.169.253 (IPv4)")
            print("  - VPC CIDR base + 2 (e.g., 10.0.0.2 for 10.0.0.0/16)")
        else:
            print("\n✗ Failed to enable DNS settings properly")
            return 1
            
    except Exception as e:
        print(f"Error checking/modifying VPC attributes: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())