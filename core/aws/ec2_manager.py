"""EC2 instance management for DC Machine."""

import time
from typing import Dict, Optional, Tuple, Any
import boto3
from botocore.exceptions import ClientError

from ..config import Config


class EC2Manager:
    """Manages EC2 instance operations."""
    
    def __init__(self, config: Config):
        """Initialize EC2 manager.
        
        Args:
            config: Configuration object
        """
        self.config = config
        self.client = boto3.client('ec2', region_name=config.region)
    
    def launch_instance(self, instance_type: str, placement_group: str, 
                       instance_name: str) -> Tuple[Optional[str], Optional[str]]:
        """Launch an EC2 instance.
        
        Args:
            instance_type: EC2 instance type
            placement_group: Placement group name
            instance_name: Name tag for instance
            
        Returns:
            Tuple of (instance_id, error_message)
        """
        # Select AMI based on instance type
        if instance_type.startswith("c7"):
            image_id = "resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
        else:
            image_id = "resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
        
        try:
            response = self.client.run_instances(
                ImageId=image_id,
                InstanceType=instance_type,
                MinCount=1,
                MaxCount=1,
                KeyName=self.config.key_name,
                SecurityGroupIds=[self.config.security_group_id],
                SubnetId=self.config.subnet_id,
                Placement={
                    "GroupName": placement_group,
                    "AvailabilityZone": self.config.availability_zone
                },
                UserData=self._get_user_data(),
                TagSpecifications=[{
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": instance_name}]
                }]
            )
            
            instance_id = response['Instances'][0]['InstanceId']
            return instance_id, None
            
        except ClientError as e:
            return None, str(e)
    
    def wait_for_running(self, instance_id: str, max_attempts: int = 30) -> bool:
        """Wait for instance to reach running state.
        
        Args:
            instance_id: EC2 instance ID
            max_attempts: Maximum wait attempts
            
        Returns:
            True if instance is running, False on timeout
        """
        try:
            waiter = self.client.get_waiter('instance_running')
            waiter.wait(
                InstanceIds=[instance_id],
                WaiterConfig={"Delay": 5, "MaxAttempts": max_attempts}
            )
            return True
        except Exception as e:
            print(f"[WARN] Wait for running failed: {e}")
            return False
    
    def terminate_instance(self, instance_id: str) -> bool:
        """Terminate an EC2 instance.
        
        Args:
            instance_id: EC2 instance ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.terminate_instances(InstanceIds=[instance_id])
            return True
        except Exception as e:
            print(f"[ERROR] Terminating {instance_id} failed: {e}")
            return False
    
    def describe_instances(self, instance_ids: list) -> Dict[str, Dict[str, Any]]:
        """Get instance information.
        
        Args:
            instance_ids: List of instance IDs
            
        Returns:
            Dict mapping instance_id to instance info
        """
        instances = {}
        try:
            response = self.client.describe_instances(InstanceIds=instance_ids)
            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    instances[instance['InstanceId']] = {
                        'state': instance['State']['Name'],
                        'placement_group': instance.get('Placement', {}).get('GroupName'),
                        'instance_type': instance.get('InstanceType'),
                        'tags': {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])}
                    }
        except Exception as e:
            if 'InvalidInstanceID.NotFound' not in str(e):
                print(f"[WARN] Error describing instances: {e}")
        return instances
    
    def is_capacity_error(self, error_message: str) -> bool:
        """Check if error is a capacity issue.
        
        Args:
            error_message: Error message string
            
        Returns:
            True if capacity error
        """
        capacity_errors = ["Insufficient capacity", "Placement", "VcpuLimitExceeded"]
        return any(err in error_message for err in capacity_errors)
    
    def _get_user_data(self) -> str:
        """Get user data script."""
        from ..constants import USER_DATA_SCRIPT
        return USER_DATA_SCRIPT