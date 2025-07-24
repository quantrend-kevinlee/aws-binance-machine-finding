"""SSH client operations for DC Machine."""

import subprocess
import time
from typing import Tuple
from ..constants import DEFAULT_SSH_TIMEOUT, DEFAULT_SSH_MAX_ATTEMPTS, DEFAULT_SSH_RETRY_DELAY


class SSHClient:
    """Manages SSH operations to EC2 instances."""
    
    def __init__(self, key_path: str):
        """Initialize SSH client.
        
        Args:
            key_path: Path to SSH private key
        """
        self.key_path = key_path
    
    def run_command(self, ip: str, command: str, timeout: int = DEFAULT_SSH_TIMEOUT) -> Tuple[str, str, int]:
        """Run command via SSH and return output.
        
        Args:
            ip: Target IP address
            command: Command to execute
            timeout: Command timeout in seconds
            
        Returns:
            Tuple of (stdout, stderr, return_code)
        """
        ssh_cmd = [
            "ssh",
            "-i", self.key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            f"ec2-user@{ip}",
            command
        ]
        
        try:
            result = subprocess.run(
                ssh_cmd, 
                capture_output=True, 
                text=True, 
                timeout=timeout
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", "Command timed out", -1
        except Exception as e:
            return "", str(e), -1
    
    def wait_for_ssh(self, ip: str, max_attempts: int = DEFAULT_SSH_MAX_ATTEMPTS) -> bool:
        """Wait for SSH to be available.
        
        Args:
            ip: Target IP address
            max_attempts: Maximum connection attempts
            
        Returns:
            True if SSH is ready, False on timeout
        """
        print(f"Waiting for SSH access to {ip}...")
        
        for i in range(max_attempts):
            stdout, stderr, code = self.run_command(ip, "echo ready", timeout=10)
            if code == 0 and "ready" in stdout:
                print("SSH is ready!")
                return True
            print(f"  Attempt {i+1}/{max_attempts}...")
            time.sleep(DEFAULT_SSH_RETRY_DELAY)
        
        return False
    
    def deploy_script(self, ip: str, script_content: str, script_path: str) -> bool:
        """Deploy a script to remote instance.
        
        Args:
            ip: Target IP address
            script_content: Script content to deploy
            script_path: Remote path for script
            
        Returns:
            True if successful, False otherwise
        """
        # Escape single quotes in script content
        escaped_content = script_content.replace("'", "'\"'\"'")
        create_script_cmd = f"echo '{escaped_content}' > {script_path}"
        
        stdout, stderr, code = self.run_command(ip, create_script_cmd)
        if code != 0:
            print(f"[ERROR] Failed to create script: {stderr}")
            return False
        
        return True