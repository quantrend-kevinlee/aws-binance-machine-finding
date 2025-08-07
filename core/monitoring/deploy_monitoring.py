"""Deploy continuous monitoring to qualified EC2 instances.

This module handles the complete deployment of monitoring infrastructure:
1. CloudWatch dashboard setup (non-fatal if fails)
2. IAM role creation for CloudWatch metrics
3. Monitoring script and IP list deployment via SCP for optimal performance
4. Systemd service configuration with proper restart handling

Features:
- SCP for file transfer
- Simplified IP format for monitoring
- Self-contained monitoring script
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
                existing_profile_arn = instance['IamInstanceProfile']['Arn']
                existing_profile_name = existing_profile_arn.split('/')[-1]
                
                if existing_profile_name == profile_name:
                    print(f"[OK] Instance already has correct IAM profile: {existing_profile_arn}")
                    return True
                else:
                    print(f"[WARN] Instance has different IAM profile: {existing_profile_arn}")
                    print(f"[INFO] Expected profile: {profile_name}")
                    # For now, continue with existing profile
                    return True
            
            # Associate the profile (only if no existing profile)
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
        if not self._setup_systemd_service(instance_ip, instance_id):
            return False
        
        print(f"[OK] Monitoring deployed successfully to {instance_id}")
        return True
    
    def _deploy_files_via_ssh(self, instance_ip: str) -> bool:
        """Deploy monitoring files to instance via SSH."""
        try:
            # Create directory structure and install dependencies
            commands = [
                f"sudo mkdir -p {self.monitor_dir}",
                f"sudo mkdir -p /var/log/binance-latency",
                f"sudo chown ec2-user:ec2-user {self.monitor_dir}",
                f"sudo chown ec2-user:ec2-user /var/log/binance-latency",
                "sudo yum update -y",
                "sudo yum install -y python3-pip",
                "sudo pip3 install boto3"
            ]
            
            for cmd in commands:
                stdout, stderr, exit_code = self.ssh_client.run_command(instance_ip, cmd)
                if exit_code != 0:
                    print(f"[ERROR] Command failed: {cmd}")
                    print(f"        stderr: {stderr}")
                    return False
            
            # Copy monitoring script using SCP for better reliability
            monitor_script_path = os.path.join(
                os.path.dirname(__file__), 
                'continuous_latency_monitor.py'
            )
            
            remote_script_path = f"{self.monitor_dir}/monitor.py"
            
            # Use SCP to transfer the monitoring script
            if self._scp_file_to_instance(monitor_script_path, remote_script_path, instance_ip):
                print("[OK] Monitoring script transferred via SCP")
                
                # Make executable
                cmd = f"chmod +x {remote_script_path}"
                stdout, stderr, exit_code = self.ssh_client.run_command(instance_ip, cmd)
                if exit_code != 0:
                    print(f"[WARN] Failed to make script executable: {stderr}")
            else:
                print("[ERROR] Failed to transfer monitoring script via SCP")
                return False
            
            # Copy config file
            config_content = json.dumps({
                'region': self.config.region,
                'monitoring_domains': self.config.monitoring_domains,
                'tcp_connection_timeout_ms': self.config.tcp_connection_timeout_ms  # Include TCP timeout
            }, indent=2)
            
            cmd = f"echo '{config_content}' > {self.monitor_dir}/config.json"
            self.ssh_client.run_command(instance_ip, cmd)
            
            # Copy IP list using SCP for better performance and reliability
            ip_list_path = os.path.join(self.config.ip_list_dir, "ip_list_latest.json")
            if os.path.exists(ip_list_path):
                # Create simplified IP format for monitoring (remove metadata for efficiency)
                simplified_ip_list = self._create_simplified_ip_list(ip_list_path)
                
                if simplified_ip_list:
                    # Create temporary simplified IP list file
                    temp_ip_file = "/tmp/ip_list_monitoring.json"
                    try:
                        with open(temp_ip_file, 'w') as f:
                            json.dump(simplified_ip_list, f, indent=2)
                        
                        # Use SCP to transfer the file
                        if self._scp_file_to_instance(temp_ip_file, f"{self.monitor_dir}/ip_list_latest.json", instance_ip):
                            print("[OK] IP list transferred via SCP")
                        else:
                            print("[ERROR] Failed to transfer IP list via SCP")
                            return False
                            
                    finally:
                        # Clean up temporary file
                        if os.path.exists(temp_ip_file):
                            os.unlink(temp_ip_file)
                else:
                    print("[WARN] No monitoring domains found in IP list, creating empty file")
                    # Create empty IP list file on remote
                    cmd = f"echo '{{}}' > {self.monitor_dir}/ip_list_latest.json"
                    stdout, stderr, exit_code = self.ssh_client.run_command(instance_ip, cmd)
                    if exit_code != 0:
                        print(f"[ERROR] Failed to create empty IP list: {stderr}")
                        return False
            
            print("[OK] Monitoring files deployed")
            return True
            
        except Exception as e:
            print(f"[ERROR] Failed to deploy files: {e}")
            return False
    
    def _setup_systemd_service(self, instance_ip: str, instance_id: str) -> bool:
        """Set up systemd service for monitoring."""
        try:
            # Create systemd service file
            service_content = f"""[Unit]
Description=Binance Latency Monitor
After=network.target

[Service]
Type=simple
User=ec2-user
Environment="PYTHONUNBUFFERED=1"
ExecStart=/usr/bin/python3 {self.monitor_dir}/monitor.py --ip-list {self.monitor_dir}/ip_list_latest.json --config {self.monitor_dir}/config.json --instance-id {instance_id}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
            
            # Write service file using heredoc for better handling of special characters
            cmd = f"sudo tee /etc/systemd/system/{self.service_name}.service << 'EOF'\n{service_content}\nEOF"
            stdout, stderr, exit_code = self.ssh_client.run_command(instance_ip, cmd)
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
                stdout, stderr, exit_code = self.ssh_client.run_command(instance_ip, cmd)
                if exit_code != 0:
                    print(f"[ERROR] Command failed: {cmd}")
                    print(f"        stderr: {stderr}")
                    return False
            
            # Check service status
            cmd = f"sudo systemctl status {self.service_name}"
            stdout, stderr, exit_code = self.ssh_client.run_command(instance_ip, cmd)
            print(f"[INFO] Service status:\n{stdout}")
            
            print("[OK] Systemd service configured and started")
            return True
            
        except Exception as e:
            print(f"[ERROR] Failed to setup systemd service: {e}")
            return False
    
    def _create_simplified_ip_list(self, ip_list_path: str) -> dict:
        """Create simplified IP list format for monitoring (domain -> IP list).
        
        This removes metadata and timestamps, keeping only the essential IP mappings
        needed for monitoring, similar to test_instance_latency.py approach.
        
        Returns:
            Dict mapping domain names to IP lists, or empty dict if error
        """
        try:
            with open(ip_list_path, 'r') as f:
                ip_data = json.load(f)
            
            # Get monitoring domains from config
            monitoring_domains = self.config.monitoring_domains
            simplified_list = {}
            
            # Handle full metadata format from discover_ips.py
            if 'domains' in ip_data:
                all_domains = ip_data.get('domains', {})
                
                for domain in monitoring_domains:
                    if domain in all_domains:
                        # Extract just the IP addresses (keys from the IPs dict)
                        ips = list(all_domains[domain].get('ips', {}).keys())
                        if ips:
                            simplified_list[domain] = ips
            else:
                # Already simplified format - just filter to monitoring domains
                for domain in monitoring_domains:
                    if domain in ip_data and isinstance(ip_data[domain], list):
                        simplified_list[domain] = ip_data[domain]
            
            print(f"[INFO] Created simplified IP list with {sum(len(ips) for ips in simplified_list.values())} IPs across {len(simplified_list)} domains")
            return simplified_list
            
        except Exception as e:
            print(f"[ERROR] Failed to create simplified IP list: {e}")
            return {}
    
    def _scp_file_to_instance(self, local_file: str, remote_file: str, instance_ip: str) -> bool:
        """Transfer file to instance using SCP for better performance.
        
        Args:
            local_file: Local file path
            remote_file: Remote file path
            instance_ip: Target instance IP
            
        Returns:
            True if successful, False otherwise
        """
        try:
            import subprocess
            
            # Build SCP command
            scp_cmd = [
                "scp",
                "-i", self.config.key_path,
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR",
                local_file,
                f"ec2-user@{instance_ip}:{remote_file}"
            ]
            
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0:
                file_size = os.path.getsize(local_file)
                print(f"[OK] SCP transfer successful ({file_size} bytes)")
                return True
            else:
                print(f"[ERROR] SCP failed with exit code {result.returncode}")
                if result.stderr:
                    print(f"        stderr: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            print("[ERROR] SCP transfer timed out after 120 seconds")
            return False
        except Exception as e:
            print(f"[ERROR] SCP transfer failed: {e}")
            return False