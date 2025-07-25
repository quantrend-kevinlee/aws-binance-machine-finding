#!/usr/bin/env python3
"""Run system optimizer on an EC2 instance."""

import subprocess
import sys
import argparse
import os

def main():
    parser = argparse.ArgumentParser(description="Run system optimizer on EC2 instance")
    parser.add_argument("instance_id", help="EC2 instance ID")
    parser.add_argument("--key-path", default="~/.ssh/dc-machine", help="SSH key path")
    parser.add_argument("--check", action="store_true", help="Check optimization status after running")
    
    args = parser.parse_args()
    
    # Expand ~ in key path
    key_path = os.path.expanduser(args.key_path)
    
    print(f"Running system optimizer on instance {args.instance_id}...")
    
    # Import modules
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.config import Config
    from core.aws import EIPManager
    from core.testing import SSHClient
    
    # Load config
    config = Config()
    
    # Get instance public IP
    eip_manager = EIPManager(config)
    
    # Associate EIP
    print("Associating EIP...")
    success = eip_manager.associate_eip(args.instance_id)
    if not success:
        print("[ERROR] Failed to associate EIP")
        sys.exit(1)
    
    # Get the EIP address
    eip_address = eip_manager.get_eip_address()
    if not eip_address:
        print("[ERROR] Could not get EIP address")
        sys.exit(1)
    
    print(f"[OK] EIP {eip_address} associated")
    
    # Create SSH client
    ssh_client = SSHClient(key_path)
    
    # Wait for SSH
    if not ssh_client.wait_for_ssh(eip_address):
        print("[ERROR] SSH connection failed")
        sys.exit(1)
    
    # Apply optimizations
    if ssh_client.apply_system_optimizations(eip_address):
        print("\n[SUCCESS] System optimizations applied!")
        
        # Check optimization log
        stdout, stderr, code = ssh_client.run_command(
            eip_address,
            "sudo tail -20 /var/log/dc-machine-optimizer.log 2>/dev/null || echo 'No log found'",
            timeout=10
        )
        
        if stdout and "No log found" not in stdout:
            print("\nOptimization log:")
            print("-" * 50)
            print(stdout)
    else:
        print("\n[ERROR] Optimization failed!")
        sys.exit(1)
    
    # Optionally check status
    if args.check:
        print("\nChecking optimization status...")
        subprocess.run([
            "python3", 
            os.path.join(os.path.dirname(__file__), "check_optimizations.py"),
            args.instance_id,
            "--key-path", key_path
        ])

if __name__ == "__main__":
    main()