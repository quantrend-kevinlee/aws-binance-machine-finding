#!/usr/bin/env python3
"""
Cleanup orphaned placement groups that no longer have associated instances.
This handles cases where instances were manually terminated or the script crashed.
"""

import boto3
import json
import sys
from datetime import datetime, timezone, timedelta

# Define UTC+8 timezone
UTC_PLUS_8 = timezone(timedelta(hours=8))

def load_config():
    """Load configuration from config.json"""
    try:
        # Look for config.json in the parent directory
        import os
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
        with open(config_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: config.json not found")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing config.json: {e}")
        sys.exit(1)

def main():
    config = load_config()
    region = config['region']
    pg_base = config['placement_group_base']
    
    ec2 = boto3.client('ec2', region_name=region)
    
    print(f"Checking for orphaned placement groups with prefix: {pg_base}")
    print("=" * 80)
    
    try:
        # Get all placement groups
        response = ec2.describe_placement_groups()
        placement_groups = response['PlacementGroups']
        
        # Filter for our placement groups
        our_pgs = [pg for pg in placement_groups if pg['GroupName'].startswith(pg_base)]
        
        if not our_pgs:
            print("No placement groups found with the specified prefix.")
            return
        
        print(f"Found {len(our_pgs)} placement group(s) to check\n")
        
        # Get all running instances
        instances_response = ec2.describe_instances(
            Filters=[
                {'Name': 'instance-state-name', 'Values': ['running', 'pending', 'stopping']}
            ]
        )
        
        # Build a set of placement groups that have instances
        active_pgs = set()
        for reservation in instances_response['Reservations']:
            for instance in reservation['Instances']:
                if 'Placement' in instance and 'GroupName' in instance['Placement']:
                    active_pgs.add(instance['Placement']['GroupName'])
        
        # Find orphaned placement groups
        orphaned_pgs = []
        for pg in our_pgs:
            pg_name = pg['GroupName']
            if pg_name not in active_pgs:
                orphaned_pgs.append(pg_name)
                print(f"Orphaned: {pg_name}")
                print(f"  State: {pg['State']}")
                print(f"  Strategy: {pg['Strategy']}")
                
                # Try to extract timestamp from name
                if '-' in pg_name:
                    try:
                        timestamp_str = pg_name.split('-')[-1]
                        timestamp = int(timestamp_str)
                        created_dt = datetime.fromtimestamp(timestamp, tz=UTC_PLUS_8)
                        age = datetime.now(UTC_PLUS_8) - created_dt
                        print(f"  Created: {created_dt.strftime('%Y-%m-%d %H:%M:%S')} (age: {age})")
                    except:
                        pass
                print()
        
        if not orphaned_pgs:
            print("No orphaned placement groups found.")
            return
        
        print(f"\nFound {len(orphaned_pgs)} orphaned placement group(s)")
        
        # Ask for confirmation
        response = input("\nDo you want to delete these orphaned placement groups? (yes/no): ")
        if response.lower() != 'yes':
            print("Cleanup cancelled.")
            return
        
        # Delete orphaned placement groups
        deleted = 0
        failed = 0
        
        for pg_name in orphaned_pgs:
            try:
                print(f"Deleting {pg_name}...", end='', flush=True)
                ec2.delete_placement_group(GroupName=pg_name)
                print(" ✓")
                deleted += 1
            except Exception as e:
                print(f" ✗ ({str(e)})")
                failed += 1
        
        print(f"\nCleanup complete: {deleted} deleted, {failed} failed")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()