#!/usr/bin/env python3
"""
Terminate all champion instances and their placement groups.

This script will:
1. Load the current champion state
2. Terminate all champion instances
3. Schedule async deletion of their placement groups
4. Clear the champion state file
"""

import json
import os
import sys
import time
import glob
import boto3
from typing import Dict, Any

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(__file__))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from core.config import Config
from core.aws.ec2_manager import EC2Manager
from core.aws.placement_group import PlacementGroupManager
from core.champion.state_manager import ChampionStateManager


def confirm_termination(champions: Dict[str, Any]) -> bool:
    """Ask user to confirm termination of champions."""
    if not champions:
        print("No champions found to terminate.")
        return False
    
    print("\n⚠️  WARNING: This will terminate ALL champion instances!")
    print("\nCurrent champions:")
    print("-" * 80)
    
    # Group by instance ID
    instances = {}
    for domain, info in champions.items():
        instance_id = info.get("instance_id")
        if instance_id not in instances:
            instances[instance_id] = {
                "domains": [],
                "instance_type": info.get("instance_type"),
                "placement_group": info.get("placement_group"),
                "ip": info.get("ip")
            }
        instances[instance_id]["domains"].append(domain)
    
    # Display instance info
    for instance_id, data in instances.items():
        domains_str = ", ".join(data["domains"])
        print(f"\nInstance: {instance_id}")
        print(f"  Type: {data['instance_type']}")
        print(f"  Placement Group: {data['placement_group']}")
        print(f"  Champion IP: {data['ip']}")
        print(f"  Domains: {domains_str}")
    
    print("\n" + "-" * 80)
    response = input("\nAre you sure you want to terminate ALL champions? (yes/no): ")
    return response.lower() == "yes"


def main():
    """Main function."""
    # Load configuration
    try:
        config = Config()
    except Exception as e:
        print(f"[ERROR] Failed to load configuration: {e}")
        sys.exit(1)
    
    # Initialize managers
    ec2_manager = EC2Manager(config)
    pg_manager = PlacementGroupManager(config)
    
    # Initialize champion state manager with proper arguments
    champion_state_file = os.path.join(config.report_dir, "champion_state.json")
    champion_state_manager = ChampionStateManager(
        champion_state_file, ec2_manager, pg_manager
    )
    
    # Load current champions
    champions = champion_state_manager.get_champions()
    
    if not champions:
        print("No champions found in champion_state.json")
        return
    
    # Confirm termination
    if not confirm_termination(champions):
        print("Termination cancelled.")
        return
    
    print("\nProceeding with termination...")
    
    # Collect unique instances and their placement groups
    instances_to_terminate = {}
    for domain, info in champions.items():
        instance_id = info.get("instance_id")
        if instance_id:
            instances_to_terminate[instance_id] = info.get("placement_group")
    
    # Terminate instances and schedule placement group cleanup
    terminated_count = 0
    failed_count = 0
    
    for instance_id, placement_group in instances_to_terminate.items():
        print(f"\nTerminating instance {instance_id}...")
        
        if ec2_manager.terminate_instance(instance_id):
            print(f"[OK] Instance termination initiated for {instance_id}")
            terminated_count += 1
            
            # Schedule async cleanup of placement group (same as orchestrator)
            if placement_group:
                print(f"Scheduling placement group {placement_group} for deletion...")
                pg_manager.schedule_async_cleanup(instance_id, placement_group)
        else:
            print(f"[ERROR] Failed to terminate {instance_id}")
            failed_count += 1
    
    # Clear champion state and logs
    if terminated_count > 0:
        print("\nCleaning up champion files...")
        
        # Clear champion state
        champion_state_manager.save_champions({})
        print("[OK] Champion state cleared")
        
        # Remove champion state file
        state_file = os.path.join(config.report_dir, "champion_state.json")
        if os.path.exists(state_file):
            os.remove(state_file)
            print(f"[OK] Removed {state_file}")
        
        # Remove champion log files
        log_pattern = os.path.join(config.report_dir, "champion_log_*.txt")
        log_files = glob.glob(log_pattern)
        
        if log_files:
            print(f"\nRemoving {len(log_files)} champion log file(s)...")
            for log_file in log_files:
                try:
                    os.remove(log_file)
                    print(f"[OK] Removed {os.path.basename(log_file)}")
                except Exception as e:
                    print(f"[WARN] Failed to remove {os.path.basename(log_file)}: {e}")
        else:
            print("[INFO] No champion log files found")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY:")
    print("=" * 60)
    print(f"Champions terminated: {terminated_count}")
    print(f"Failed terminations: {failed_count}")
    
    # Wait for all cleanup threads to complete (same as orchestrator)
    active_count = pg_manager.get_active_cleanup_count()
    if active_count > 0:
        print(f"\n{active_count} background cleanup task(s) running...")
        print("Waiting for all instances to terminate and placement groups to be deleted.")
        print("This checks instance status every 10 seconds for up to 30 minutes per task.")
        
        # Always wait for cleanup completion
        pg_manager.wait_for_cleanup_threads()
        print("\nAll cleanup tasks completed!")
    
    print("\nDone!")


if __name__ == "__main__":
    main()