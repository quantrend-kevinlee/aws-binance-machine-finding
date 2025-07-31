"""Placement group management for the latency finder."""

import time
import threading
from typing import List
import boto3

from ..config import Config
from ..constants import CLEANUP_CHECK_DELAY, CLEANUP_MAX_ATTEMPTS, CLEANUP_FINAL_DELAY


class PlacementGroupManager:
    """Manages placement group operations and cleanup."""
    
    def __init__(self, config: Config):
        """Initialize placement group manager.
        
        Args:
            config: Configuration object
        """
        self.config = config
        self.client = boto3.client('ec2', region_name=config.region)
        self.cleanup_threads: List[threading.Thread] = []
    
    def create_placement_group(self, name: str) -> bool:
        """Create a cluster placement group.
        
        Args:
            name: Placement group name
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.create_placement_group(
                GroupName=name,
                Strategy='cluster'
            )
            print(f"  [OK] Created placement group")
            time.sleep(2)  # Give AWS a moment to register the PG
            return True
        except Exception as e:
            print(f"[ERROR] Failed to create placement group: {e}")
            return False
    
    def delete_placement_group(self, name: str) -> bool:
        """Delete a placement group.
        
        Args:
            name: Placement group name
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.delete_placement_group(GroupName=name)
            print(f"  [OK] Deleted placement group")
            return True
        except Exception as e:
            print(f"  [WARN] Could not delete placement group: {e}")
            return False
    
    def schedule_async_cleanup(self, instance_id: str, placement_group_name: str) -> threading.Thread:
        """Schedule asynchronous cleanup of placement group after instance termination.
        
        Args:
            instance_id: EC2 instance ID
            placement_group_name: Placement group name
            
        Returns:
            The cleanup thread
        """
        def cleanup():
            print(f"[Background] Scheduled cleanup for PG {placement_group_name} "
                  f"(checking every 10 seconds for up to 30 minutes)")
            
            # Create a new EC2 client for this thread
            thread_client = boto3.client('ec2', region_name=self.config.region)
            
            try:
                # Wait for instance to fully terminate
                waiter = thread_client.get_waiter('instance_terminated')
                waiter.wait(
                    InstanceIds=[instance_id],
                    WaiterConfig={
                        'Delay': CLEANUP_CHECK_DELAY,
                        'MaxAttempts': CLEANUP_MAX_ATTEMPTS
                    }
                )
                
                # Small delay to ensure AWS has fully updated its state
                time.sleep(CLEANUP_FINAL_DELAY)
                
                # Now delete the placement group
                thread_client.delete_placement_group(GroupName=placement_group_name)
                print(f"[Background] [OK] Successfully deleted placement group {placement_group_name}")
                
            except Exception as e:
                if 'Max attempts exceeded' in str(e):
                    print(f"[Background] [WARN] Timeout: Instance {instance_id} "
                          f"still terminating after 30 minutes")
                else:
                    print(f"[Background] [WARN] Failed to delete placement group "
                          f"{placement_group_name}: {e}")
        
        # Start cleanup in a background thread (not daemon so we can wait on Ctrl+C)
        thread = threading.Thread(target=cleanup, daemon=False)
        thread.start()
        self.cleanup_threads.append(thread)
        return thread
    
    def wait_for_cleanup_threads(self) -> None:
        """Wait for all cleanup threads to complete."""
        active_threads = [t for t in self.cleanup_threads if t.is_alive()]
        if not active_threads:
            return
        
        print(f"\n[WAIT] Waiting for {len(active_threads)} background cleanup task(s) to complete...")
        print("   This ensures all instances are terminated and placement groups are deleted.")
        print("   (Checking every 10 seconds, up to 30 minutes per task)")
        
        start_wait = time.time()
        last_count = len(active_threads)
        
        while active_threads:
            # Show progress every 10 seconds to match cleanup check interval
            time.sleep(10)
            active_threads = [t for t in self.cleanup_threads if t.is_alive()]
            
            if len(active_threads) < last_count:
                print(f"   [OK] {last_count - len(active_threads)} task(s) completed")
                last_count = len(active_threads)
            
            if active_threads:
                elapsed = int(time.time() - start_wait)
                mins = elapsed // 60
                secs = elapsed % 60
                print(f"   [WAIT] {len(active_threads)} task(s) still running... "
                      f"(elapsed: {mins}m {secs}s)")
        
        print("   [OK] All cleanup tasks completed!")
    
    def get_active_cleanup_count(self) -> int:
        """Get count of active cleanup threads."""
        return sum(1 for t in self.cleanup_threads if t.is_alive())
    
    def generate_placement_group_name(self, timestamp: int) -> str:
        """Generate placement group name with timestamp.
        
        Args:
            timestamp: Unix timestamp
            
        Returns:
            Placement group name
        """
        return f"{self.config.placement_group_name_base}-{timestamp}"