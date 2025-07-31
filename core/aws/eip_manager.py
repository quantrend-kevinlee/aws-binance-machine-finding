"""Elastic IP management for the latency finder."""

import time
import threading
from typing import List, Optional, Tuple
import boto3
from botocore.exceptions import ClientError

from ..config import Config
from ..constants import CLEANUP_CHECK_DELAY, CLEANUP_MAX_ATTEMPTS, CLEANUP_FINAL_DELAY


class EIPManager:
    """Manages Elastic IP operations and cleanup."""
    
    def __init__(self, config: Config):
        """Initialize EIP manager.
        
        Args:
            config: Configuration object
        """
        self.config = config
        self.client = boto3.client('ec2', region_name=config.region)
        self.cleanup_threads: List[threading.Thread] = []
    
    def allocate_eip(self, name: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Allocate a new Elastic IP address.
        
        Args:
            name: Name tag for the EIP
            
        Returns:
            Tuple of (allocation_id, public_ip, error_message)
        """
        try:
            response = self.client.allocate_address(
                Domain='vpc',
                TagSpecifications=[{
                    'ResourceType': 'elastic-ip',
                    'Tags': [{'Key': 'Name', 'Value': name}]
                }]
            )
            
            allocation_id = response['AllocationId']
            public_ip = response['PublicIp']
            print(f"  [OK] Allocated EIP {public_ip}")
            return allocation_id, public_ip, None
            
        except ClientError as e:
            error_msg = str(e)
            print(f"[ERROR] Failed to allocate EIP: {error_msg}")
            return None, None, error_msg
    
    def associate_eip(self, allocation_id: str, instance_id: str) -> bool:
        """Associate an Elastic IP with an instance.
        
        Args:
            allocation_id: EIP allocation ID
            instance_id: EC2 instance ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.associate_address(
                AllocationId=allocation_id,
                InstanceId=instance_id
            )
            print(f"  [OK] Associated EIP with instance")
            return True
            
        except ClientError as e:
            print(f"[ERROR] Failed to associate EIP: {e}")
            return False
    
    def release_eip(self, allocation_id: str) -> bool:
        """Release an Elastic IP address.
        
        Args:
            allocation_id: EIP allocation ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # First check if EIP is associated and disassociate if needed
            response = self.client.describe_addresses(AllocationIds=[allocation_id])
            if response['Addresses']:
                eip_info = response['Addresses'][0]
                association_id = eip_info.get('AssociationId')
                if association_id:
                    print(f"  [INFO] Disassociating EIP before release...")
                    self.client.disassociate_address(AssociationId=association_id)
                    time.sleep(2)  # Give AWS a moment to update
            
            # Release the EIP
            self.client.release_address(AllocationId=allocation_id)
            print(f"  [OK] Released EIP")
            return True
            
        except ClientError as e:
            print(f"  [WARN] Could not release EIP: {e}")
            return False
    
    def get_eip_public_ip(self, allocation_id: str) -> Optional[str]:
        """Get the public IP address from an EIP allocation ID.
        
        Args:
            allocation_id: EIP allocation ID
            
        Returns:
            Public IP address or None if not found
        """
        try:
            response = self.client.describe_addresses(AllocationIds=[allocation_id])
            if response['Addresses']:
                return response['Addresses'][0]['PublicIp']
            return None
            
        except ClientError as e:
            print(f"[WARN] Could not get EIP public IP: {e}")
            return None
    
    def schedule_async_eip_cleanup(self, instance_id: str, allocation_id: str, eip_name: str) -> threading.Thread:
        """Schedule asynchronous cleanup of EIP after instance termination.
        
        Args:
            instance_id: EC2 instance ID
            allocation_id: EIP allocation ID
            eip_name: EIP name for logging
            
        Returns:
            The cleanup thread
        """
        def cleanup():
            print(f"[Background] Scheduled EIP cleanup for {eip_name} "
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
                
                # Now release the EIP
                try:
                    # Check if EIP is associated and disassociate if needed
                    response = thread_client.describe_addresses(AllocationIds=[allocation_id])
                    if response['Addresses']:
                        eip_info = response['Addresses'][0]
                        association_id = eip_info.get('AssociationId')
                        if association_id:
                            thread_client.disassociate_address(AssociationId=association_id)
                            time.sleep(2)
                    
                    # Release the EIP
                    thread_client.release_address(AllocationId=allocation_id)
                    print(f"[Background] [OK] Successfully released EIP {eip_name}")
                    
                except Exception as eip_error:
                    print(f"[Background] [WARN] Failed to release EIP {eip_name}: {eip_error}")
                
            except Exception as e:
                if 'Max attempts exceeded' in str(e):
                    print(f"[Background] [WARN] Timeout: Instance {instance_id} "
                          f"still terminating after 30 minutes (EIP {eip_name} not released)")
                else:
                    print(f"[Background] [WARN] Failed to cleanup EIP {eip_name}: {e}")
        
        # Start cleanup in a background thread (not daemon so we can wait on Ctrl+C)
        thread = threading.Thread(target=cleanup, daemon=False)
        thread.start()
        self.cleanup_threads.append(thread)
        return thread
    
    def wait_for_cleanup_threads(self) -> None:
        """Wait for all EIP cleanup threads to complete."""
        active_threads = [t for t in self.cleanup_threads if t.is_alive()]
        if not active_threads:
            return
        
        print(f"\n[WAIT] Waiting for {len(active_threads)} EIP cleanup task(s) to complete...")
        print("   This ensures all EIPs are released properly.")
        print("   (Checking every 10 seconds, up to 30 minutes per task)")
        
        start_wait = time.time()
        last_count = len(active_threads)
        
        while active_threads:
            # Show progress every 10 seconds to match cleanup check interval
            time.sleep(10)
            active_threads = [t for t in self.cleanup_threads if t.is_alive()]
            
            if len(active_threads) < last_count:
                print(f"   [OK] {last_count - len(active_threads)} EIP cleanup task(s) completed")
                last_count = len(active_threads)
            
            if active_threads:
                elapsed = int(time.time() - start_wait)
                mins = elapsed // 60
                secs = elapsed % 60
                print(f"   [WAIT] {len(active_threads)} EIP cleanup task(s) still running... "
                      f"(elapsed: {mins}m {secs}s)")
        
        print("   [OK] All EIP cleanup tasks completed!")
    
    def get_active_cleanup_count(self) -> int:
        """Get count of active EIP cleanup threads."""
        return sum(1 for t in self.cleanup_threads if t.is_alive())
    
    def generate_eip_name(self, timestamp: int) -> str:
        """Generate EIP name with timestamp.
        
        Args:
            timestamp: Unix timestamp
            
        Returns:
            EIP name
        """
        return f"{self.config.eip_name_base}-{timestamp}"