"""Elastic IP management for DC Machine."""

import time
from typing import Optional
import boto3
from botocore.exceptions import ClientError

from ..config import Config


class EIPManager:
    """Manages Elastic IP operations."""
    
    def __init__(self, config: Config):
        """Initialize EIP manager.
        
        Args:
            config: Configuration object
        """
        self.config = config
        self.client = boto3.client('ec2', region_name=config.region)
    
    def associate_eip(self, instance_id: str, max_attempts: int = 3) -> bool:
        """Associate Elastic IP with instance.
        
        Args:
            instance_id: EC2 instance ID
            max_attempts: Maximum retry attempts
            
        Returns:
            True if successful, False otherwise
        """
        for attempt in range(max_attempts):
            try:
                self.client.associate_address(
                    InstanceId=instance_id,
                    AllocationId=self.config.eip_allocation_id
                )
                return True
            except ClientError as e:
                print(f"[WARN] Associate EIP failed (attempt {attempt+1}/{max_attempts}): {e}")
                if attempt < max_attempts - 1:
                    time.sleep(3)
        
        print("[ERROR] Could not attach EIP to instance.")
        return False
    
    def get_eip_address(self) -> Optional[str]:
        """Get the public IP address of the Elastic IP.
        
        Returns:
            Public IP address or None if not found
        """
        try:
            response = self.client.describe_addresses(
                AllocationIds=[self.config.eip_allocation_id]
            )
            if response['Addresses']:
                return response['Addresses'][0]['PublicIp']
        except Exception as e:
            print(f"[ERROR] Failed to get EIP address: {e}")
        
        return None
    
    def disassociate_eip(self) -> bool:
        """Disassociate Elastic IP from any instance.
        
        Returns:
            True if successful or already disassociated, False on error
        """
        try:
            # Get current association
            response = self.client.describe_addresses(
                AllocationIds=[self.config.eip_allocation_id]
            )
            
            if not response['Addresses']:
                return True
            
            address = response['Addresses'][0]
            association_id = address.get('AssociationId')
            
            if association_id:
                self.client.disassociate_address(AssociationId=association_id)
                print("[OK] Disassociated EIP")
            
            return True
            
        except Exception as e:
            print(f"[ERROR] Failed to disassociate EIP: {e}")
            return False