"""File deployment utilities for SSH operations."""

import json
import os
import tempfile
from contextlib import contextmanager
from typing import Dict, Any, Optional, List
from .ssh_client import SSHClient
from ..ip_discovery import load_ip_list


class TempFileManager:
    """Manages temporary files with automatic cleanup."""
    
    def __init__(self):
        """Initialize temp file manager."""
        self.temp_files = []
    
    def create_temp_file(self, content: str, suffix: str = "", prefix: str = "temp_") -> str:
        """Create a temporary file with content.
        
        Args:
            content: Content to write to file
            suffix: File suffix (e.g., '.json', '.py')
            prefix: File prefix
            
        Returns:
            Path to created temporary file
        """
        with tempfile.NamedTemporaryFile(
            mode='w', 
            suffix=suffix, 
            prefix=prefix, 
            delete=False
        ) as f:
            f.write(content)
            temp_path = f.name
        
        self.temp_files.append(temp_path)
        return temp_path
    
    def create_temp_json_file(self, data: Dict[str, Any], prefix: str = "temp_") -> str:
        """Create a temporary JSON file with data.
        
        Args:
            data: Dictionary to serialize as JSON
            prefix: File prefix
            
        Returns:
            Path to created temporary JSON file
        """
        content = json.dumps(data, indent=2)
        return self.create_temp_file(content, suffix='.json', prefix=prefix)
    
    def cleanup(self) -> None:
        """Clean up all temporary files."""
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except OSError:
                pass  # File may have been removed already
        self.temp_files.clear()
    
    def __del__(self):
        """Ensure cleanup on object destruction."""
        self.cleanup()


@contextmanager
def temp_files():
    """Context manager for automatic temporary file cleanup.
    
    Usage:
        with temp_files() as temp_mgr:
            temp_file = temp_mgr.create_temp_file("content")
            # Use temp_file
        # Files are automatically cleaned up
    """
    manager = TempFileManager()
    try:
        yield manager
    finally:
        manager.cleanup()


class FileDeployer:
    """Handles file deployment to remote instances via SSH."""
    
    def __init__(self, ssh_client: SSHClient):
        """Initialize file deployer.
        
        Args:
            ssh_client: SSH client instance
        """
        self.ssh_client = ssh_client
    
    def deploy_content_as_file(self, ip: str, content: str, remote_path: str, 
                              suffix: str = "", prefix: str = "deploy_") -> bool:
        """Deploy content as a file to remote instance.
        
        Args:
            ip: Target IP address
            content: Content to deploy
            remote_path: Remote file path
            suffix: Temporary file suffix
            prefix: Temporary file prefix
            
        Returns:
            True if successful, False otherwise
        """
        with temp_files() as temp_mgr:
            local_temp_file = temp_mgr.create_temp_file(content, suffix, prefix)
            return self.ssh_client.copy_file(ip, local_temp_file, remote_path)
    
    def deploy_json_data(self, ip: str, data: Dict[str, Any], remote_path: str) -> bool:
        """Deploy JSON data to remote instance.
        
        Args:
            ip: Target IP address
            data: Dictionary to serialize as JSON
            remote_path: Remote file path
            
        Returns:
            True if successful, False otherwise
        """
        content = json.dumps(data, indent=2)
        return self.deploy_content_as_file(ip, content, remote_path, suffix='.json', prefix='json_')
    
    def deploy_script_file(self, ip: str, script_content: str, remote_path: str) -> bool:
        """Deploy script content to remote instance.
        
        Args:
            ip: Target IP address
            script_content: Script content to deploy
            remote_path: Remote script path
            
        Returns:
            True if successful, False otherwise
        """
        return self.deploy_content_as_file(ip, script_content, remote_path, suffix='.py', prefix='script_')
    
    def deploy_ip_list(self, ip: str, ip_list: Dict[str, List[str]], 
                      remote_path: str = "/tmp/ip_list.json") -> bool:
        """Deploy IP list to remote instance.
        
        Args:
            ip: Target IP address
            ip_list: Dictionary mapping domains to IP lists
            remote_path: Remote path for IP list file
            
        Returns:
            True if successful, False otherwise
        """
        return self.deploy_json_data(ip, ip_list, remote_path)


class IPListDeployer:
    """Specialized deployer for IP lists with discovery system integration."""
    
    def __init__(self, file_deployer: FileDeployer):
        """Initialize IP list deployer.
        
        Args:
            file_deployer: FileDeployer instance
        """
        self.file_deployer = file_deployer
    
    def load_and_deploy_ip_list(self, ip: str, ip_list_file: str, domains: List[str],
                               remote_path: str = "/tmp/ip_list.json") -> Optional[Dict[str, List[str]]]:
        """Load IP list from file and deploy to remote instance.
        
        This method integrates with the core IP discovery system:
        1. Tries to load from ip_list_file using load_ip_list()
        2. Falls back to DNS resolution if file not found
        3. Deploys the loaded IP list to remote instance
        
        Args:
            ip: Target IP address
            ip_list_file: Path to local IP list file
            domains: List of domains to load/filter
            remote_path: Remote path for deployed IP list
            
        Returns:
            Loaded IP list dictionary, or None if loading failed
        """
        # Load IP list using the core discovery system
        ip_list = load_ip_list(ip_list_file, domains)
        
        if ip_list is None:
            print(f"[ERROR] Failed to load IP list from {ip_list_file}")
            return None
        
        # Deploy to remote instance
        if not self.file_deployer.deploy_ip_list(ip, ip_list, remote_path):
            print(f"[ERROR] Failed to deploy IP list to {ip}:{remote_path}")
            return None
        
        # Report what was deployed
        total_ips = sum(len(ips) for ips in ip_list.values())
        print(f"[INFO] Deployed {total_ips} IPs to {remote_path}:")
        for domain, ips in ip_list.items():
            print(f"  - {domain}: {len(ips)} IPs")
        
        return ip_list
    
    def prepare_local_ip_list(self, ip_list_file: str, domains: List[str]) -> Optional[Dict[str, List[str]]]:
        """Prepare IP list for local execution.
        
        Args:
            ip_list_file: Path to local IP list file
            domains: List of domains to load/filter
            
        Returns:
            Loaded IP list dictionary, or None if loading failed
        """
        return load_ip_list(ip_list_file, domains)


def create_file_deployer(key_path: str) -> FileDeployer:
    """Create a file deployer with SSH client.
    
    Args:
        key_path: Path to SSH private key
        
    Returns:
        FileDeployer instance
    """
    ssh_client = SSHClient(key_path)
    return FileDeployer(ssh_client)


def create_ip_list_deployer(key_path: str) -> IPListDeployer:
    """Create an IP list deployer with integrated SSH client.
    
    Args:
        key_path: Path to SSH private key
        
    Returns:
        IPListDeployer instance
    """
    file_deployer = create_file_deployer(key_path)
    return IPListDeployer(file_deployer)