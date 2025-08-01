"""Deploy continuous monitoring to qualified EC2 instances.

This module handles the complete deployment of monitoring infrastructure:
1. CloudWatch dashboard setup (non-fatal if fails)
2. IAM role creation for CloudWatch metrics
3. Monitoring script deployment via SSH
4. Systemd service configuration
"""

import os
import json
import time
import subprocess
from typing import Optional, Dict, Any
import boto3
from botocore.exceptions import ClientError

from ..aws.ec2_manager import EC2Manager
from ..testing.ssh_client import SSHClient
from ..config import Config


class MonitoringDeployer:
    """Handles deployment of monitoring to qualified instances."""
    
    def __init__(self, config: Config):
        self.config = config
        self.ec2_manager = EC2Manager(config)
        self.ssh_client = SSHClient(config.key_path)
        self.iam_client = boto3.client('iam', region_name=config.region)
        
        # Monitoring configuration
        self.monitor_dir = "/opt/binance-monitor"
        self.service_name = "binance-latency-monitor"
        
    def create_or_get_iam_role(self) -> Optional[str]:
        """Create or get IAM role for CloudWatch metrics.
        
        Returns:
            Role name if successful, None otherwise
        """
        role_name = "BinanceLatencyMonitorRole"
        
        try:
            # Check if role exists
            self.iam_client.get_role(RoleName=role_name)
            print(f"[OK] IAM role {role_name} already exists")
            return role_name
        except ClientError as e:
            if e.response['Error']['Code'] != 'NoSuchEntity':
                print(f"[ERROR] Failed to check IAM role: {e}")
                return None
        
        # Create the role
        try:
            # Trust policy for EC2
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }
            
            # Create role
            self.iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Role for Binance latency monitoring on EC2"
            )
            
            # Attach CloudWatch policy
            self.iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn='arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy'
            )
            
            # Create and attach custom policy for metrics
            policy_document = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": [
                        "cloudwatch:PutMetricData",
                        "ec2:DescribeVolumes",
                        "ec2:DescribeTags"
                    ],
                    "Resource": "*"
                }]
            }
            
            policy_name = "BinanceLatencyMetricsPolicy"
            try:
                self.iam_client.create_policy(
                    PolicyName=policy_name,
                    PolicyDocument=json.dumps(policy_document),
                    Description="Custom policy for Binance latency metrics"
                )
                
                # Get account ID for policy ARN
                account_id = boto3.client('sts').get_caller_identity()['Account']
                policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
                
                self.iam_client.attach_role_policy(
                    RoleName=role_name,
                    PolicyArn=policy_arn
                )
            except ClientError as e:
                if e.response['Error']['Code'] == 'EntityAlreadyExists':
                    # Policy already exists, just attach it
                    account_id = boto3.client('sts').get_caller_identity()['Account']
                    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
                    self.iam_client.attach_role_policy(
                        RoleName=role_name,
                        PolicyArn=policy_arn
                    )
                else:
                    raise
            
            print(f"[OK] Created IAM role {role_name}")
            
            # Wait a moment for role to propagate
            time.sleep(10)
            
            return role_name
            
        except Exception as e:
            print(f"[ERROR] Failed to create IAM role: {e}")
            return None
    
    def create_or_get_instance_profile(self, role_name: str) -> Optional[str]:
        """Create or get instance profile for the role.
        
        Returns:
            Instance profile name if successful, None otherwise
        """
        profile_name = f"{role_name}Profile"
        
        try:
            # Check if profile exists
            self.iam_client.get_instance_profile(InstanceProfileName=profile_name)
            print(f"[OK] Instance profile {profile_name} already exists")
            return profile_name
        except ClientError as e:
            if e.response['Error']['Code'] != 'NoSuchEntity':
                print(f"[ERROR] Failed to check instance profile: {e}")
                return None
        
        # Create the profile
        try:
            self.iam_client.create_instance_profile(
                InstanceProfileName=profile_name
            )
            
            # Add role to profile
            self.iam_client.add_role_to_instance_profile(
                InstanceProfileName=profile_name,
                RoleName=role_name
            )
            
            print(f"[OK] Created instance profile {profile_name}")
            
            # Wait for profile to propagate
            time.sleep(10)
            
            return profile_name
            
        except Exception as e:
            print(f"[ERROR] Failed to create instance profile: {e}")
            return None
    
    def attach_iam_role_to_instance(self, instance_id: str, profile_name: str) -> bool:
        """Attach IAM instance profile to EC2 instance.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if instance already has a profile
            response = self.ec2_manager.client.describe_instances(
                InstanceIds=[instance_id]
            )
            
            instance = response['Reservations'][0]['Instances'][0]
            if 'IamInstanceProfile' in instance:
                print(f"[WARN] Instance already has IAM profile: {instance['IamInstanceProfile']['Arn']}")
                # Could optionally replace it here
            
            # Associate the profile
            self.ec2_manager.client.associate_iam_instance_profile(
                IamInstanceProfile={'Name': profile_name},
                InstanceId=instance_id
            )
            
            print(f"[OK] Attached IAM profile {profile_name} to instance {instance_id}")
            
            # Wait for association to complete
            time.sleep(10)
            
            return True
            
        except Exception as e:
            print(f"[ERROR] Failed to attach IAM profile: {e}")
            return False
    
    def setup_cloudwatch_dashboard(self, instance_id: str) -> bool:
        """Set up CloudWatch dashboard for the instance.
        
        Note: Dashboard creation failures are non-fatal. Monitoring will
        continue even if the dashboard cannot be created to ensure metrics
        are still collected.
        
        Args:
            instance_id: EC2 instance ID to use as dashboard name
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Get script path
            script_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            dashboard_script = os.path.join(script_dir, "tool_scripts", "setup_cloudwatch_dashboard.py")
            config_path = os.path.join(script_dir, "config.json")
            
            # Run dashboard setup script
            print(f"\nSetting up CloudWatch dashboard for {instance_id}...")
            result = subprocess.run(
                [
                    "python3", dashboard_script,
                    "--dashboard-name", instance_id,
                    "--config", config_path
                ],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                print("[OK] CloudWatch dashboard setup completed")
                return True
            else:
                print(f"[ERROR] Dashboard setup failed with exit code {result.returncode}")
                if result.stdout:
                    print(f"stdout: {result.stdout}")
                if result.stderr:
                    print(f"stderr: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"[ERROR] Failed to set up CloudWatch dashboard: {e}")
            return False
    
    def deploy_monitoring(self, instance_id: str, instance_ip: str) -> bool:
        """Deploy monitoring to a qualified instance.
        
        The deployment process includes:
        1. CloudWatch dashboard creation (non-fatal if fails)
        2. IAM role and instance profile setup
        3. Monitoring script deployment
        4. Systemd service configuration
        
        Args:
            instance_id: EC2 instance ID
            instance_ip: Public IP of the instance
            
        Returns:
            True if successful, False otherwise
        """
        print(f"\nDeploying monitoring to {instance_id} ({instance_ip})...")
        
        # Step 1: Set up CloudWatch dashboard (non-fatal)
        if not self.setup_cloudwatch_dashboard(instance_id):
            print("[WARN] Failed to set up CloudWatch dashboard, continuing with monitoring deployment")
            # Dashboard creation is non-fatal - monitoring can still publish metrics
        
        # Step 2: Create/verify IAM role
        role_name = self.create_or_get_iam_role()
        if not role_name:
            return False
        
        # Step 3: Create/verify instance profile
        profile_name = self.create_or_get_instance_profile(role_name)
        if not profile_name:
            return False
        
        # Step 4: Attach IAM role to instance
        if not self.attach_iam_role_to_instance(instance_id, profile_name):
            return False
        
        # Step 5: Deploy monitoring script via SSH
        if not self._deploy_files_via_ssh(instance_ip):
            return False
        
        # Step 6: Set up systemd service
        if not self._setup_systemd_service(instance_ip):
            return False
        
        print(f"[OK] Monitoring deployed successfully to {instance_id}")
        return True
    
    def _deploy_files_via_ssh(self, instance_ip: str) -> bool:
        """Deploy monitoring files to instance via SSH."""
        try:
            # Create directory structure
            commands = [
                f"sudo mkdir -p {self.monitor_dir}",
                f"sudo mkdir -p /var/log/binance-latency",
                f"sudo chown ec2-user:ec2-user {self.monitor_dir}",
                f"sudo chown ec2-user:ec2-user /var/log/binance-latency"
            ]
            
            for cmd in commands:
                stdout, stderr, exit_code = self.ssh_client.execute_command(instance_ip, cmd)
                if exit_code != 0:
                    print(f"[ERROR] Command failed: {cmd}")
                    print(f"        stderr: {stderr}")
                    return False
            
            # Copy monitoring script
            monitor_script_path = os.path.join(
                os.path.dirname(__file__), 
                'continuous_latency_monitor.py'
            )
            
            with open(monitor_script_path, 'r') as f:
                script_content = f.read()
            
            # Write script to remote
            remote_script_path = f"{self.monitor_dir}/monitor.py"
            escaped_content = script_content.replace("'", "'\"'\"'")
            cmd = f"echo '{escaped_content}' > {remote_script_path}"
            stdout, stderr, exit_code = self.ssh_client.execute_command(instance_ip, cmd)
            if exit_code != 0:
                print(f"[ERROR] Failed to write monitoring script: {stderr}")
                return False
            
            # Make executable
            cmd = f"chmod +x {remote_script_path}"
            self.ssh_client.execute_command(instance_ip, cmd)
            
            # Copy config file
            config_content = json.dumps({
                'region': self.config.region,
                'latency_test_domains': self.config.latency_test_domains
            }, indent=2)
            
            cmd = f"echo '{config_content}' > {self.monitor_dir}/config.json"
            self.ssh_client.execute_command(instance_ip, cmd)
            
            # Copy IP list
            ip_list_path = os.path.join(self.config.ip_list_dir, "ip_list_latest.json")
            if os.path.exists(ip_list_path):
                with open(ip_list_path, 'r') as f:
                    ip_list_content = f.read()
                
                escaped_content = ip_list_content.replace("'", "'\"'\"'")
                cmd = f"echo '{escaped_content}' > {self.monitor_dir}/ip_list_latest.json"
                stdout, stderr, exit_code = self.ssh_client.execute_command(instance_ip, cmd)
                if exit_code != 0:
                    print(f"[ERROR] Failed to write IP list: {stderr}")
                    return False
            
            print("[OK] Monitoring files deployed")
            return True
            
        except Exception as e:
            print(f"[ERROR] Failed to deploy files: {e}")
            return False
    
    def _setup_systemd_service(self, instance_ip: str) -> bool:
        """Set up systemd service for monitoring."""
        try:
            # Create systemd service file
            service_content = f"""[Unit]
Description=Binance Latency Monitor
After=network.target

[Service]
Type=simple
User=ec2-user
ExecStart=/usr/bin/python3 {self.monitor_dir}/monitor.py --ip-list {self.monitor_dir}/ip_list_latest.json --config {self.monitor_dir}/config.json
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
            
            # Write service file
            escaped_content = service_content.replace("'", "'\"'\"'")
            cmd = f"echo '{escaped_content}' | sudo tee /etc/systemd/system/{self.service_name}.service"
            stdout, stderr, exit_code = self.ssh_client.execute_command(instance_ip, cmd)
            if exit_code != 0:
                print(f"[ERROR] Failed to create service file: {stderr}")
                return False
            
            # Enable and start service
            commands = [
                "sudo systemctl daemon-reload",
                f"sudo systemctl enable {self.service_name}",
                f"sudo systemctl start {self.service_name}"
            ]
            
            for cmd in commands:
                stdout, stderr, exit_code = self.ssh_client.execute_command(instance_ip, cmd)
                if exit_code != 0:
                    print(f"[ERROR] Command failed: {cmd}")
                    print(f"        stderr: {stderr}")
                    return False
            
            # Check service status
            cmd = f"sudo systemctl status {self.service_name}"
            stdout, stderr, exit_code = self.ssh_client.execute_command(instance_ip, cmd)
            print(f"[INFO] Service status:\n{stdout}")
            
            print("[OK] Systemd service configured and started")
            return True
            
        except Exception as e:
            print(f"[ERROR] Failed to setup systemd service: {e}")
            return False
    
    def check_monitoring_status(self, instance_ip: str) -> Dict[str, Any]:
        """Check monitoring status on instance.
        
        Returns:
            Dict with status information
        """
        try:
            # Check service status
            cmd = f"sudo systemctl is-active {self.service_name}"
            stdout, stderr, exit_code = self.ssh_client.execute_command(instance_ip, cmd)
            service_active = stdout.strip() == "active"
            
            # Check recent logs
            cmd = f"sudo journalctl -u {self.service_name} -n 50 --no-pager"
            stdout, stderr, exit_code = self.ssh_client.execute_command(instance_ip, cmd)
            
            # Check for local data files
            cmd = f"ls -la /var/log/binance-latency/"
            files_stdout, _, _ = self.ssh_client.execute_command(instance_ip, cmd)
            
            return {
                "service_active": service_active,
                "recent_logs": stdout,
                "data_files": files_stdout
            }
            
        except Exception as e:
            return {
                "error": str(e),
                "service_active": False
            }