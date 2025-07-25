#!/usr/bin/env python3
"""Check if system optimizations have been applied on an EC2 instance."""

import subprocess
import sys
import argparse
from typing import Tuple

def run_ssh_command(instance_id: str, key_path: str, command: str) -> Tuple[str, str, int]:
    """Run command on instance via SSH."""
    # First bind EIP
    bind_cmd = ["python3", "tool_scripts/bind_eip.py", instance_id]
    result = subprocess.run(bind_cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        return "", f"Failed to bind EIP: {result.stderr}", 1
    
    # Extract EIP from output
    eip = None
    for line in result.stdout.split('\n'):
        if "EIP" in line and "associated" in line:
            parts = line.split()
            for i, part in enumerate(parts):
                if part == "EIP" and i + 1 < len(parts):
                    eip = parts[i + 1]
                    break
    
    if not eip:
        return "", "Could not extract EIP address", 1
    
    # Run SSH command
    ssh_cmd = [
        "ssh",
        "-i", key_path,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        f"ec2-user@{eip}",
        command
    ]
    
    result = subprocess.run(ssh_cmd, capture_output=True, text=True)
    return result.stdout, result.stderr, result.returncode

def check_optimizations(instance_id: str, key_path: str = "~/.ssh/dc-machine"):
    """Check system optimizations on instance."""
    print(f"Checking optimizations on instance {instance_id}...\n")
    
    checks = [
        ("CPU Governor", "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor 2>/dev/null || echo 'Not available'"),
        ("C-States", "cat /sys/devices/system/cpu/cpu0/cpuidle/state1/disable 2>/dev/null || ls /sys/devices/system/cpu/cpu0/cpuidle/ 2>/dev/null | grep -c state || echo 'Not available'"),
        ("IRQBalance", "systemctl is-active irqbalance 2>/dev/null || echo 'Not installed'"),
        ("Busy Poll", "sysctl net.core.busy_poll 2>/dev/null | awk '{print $3}'"),
        ("TCP Low Latency", "sysctl net.ipv4.tcp_low_latency 2>/dev/null | awk '{print $3}'"),
        ("TCP Timestamps", "sysctl net.ipv4.tcp_timestamps 2>/dev/null | awk '{print $3}'"),
        ("Network Buffers", "sysctl net.core.rmem_max 2>/dev/null | awk '{print $3}'"),
        ("Tuned Profile", "tuned-adm active 2>/dev/null | grep -o 'network-latency' || echo 'No profile'"),
    ]
    
    print("System Optimization Status:")
    print("-" * 50)
    
    for name, command in checks:
        stdout, stderr, code = run_ssh_command(instance_id, key_path, command)
        value = stdout.strip() if code == 0 else "ERROR"
        
        # Interpret results
        status = "❌"
        if name == "CPU Governor" and value == "performance":
            status = "✅"
        elif name == "C-States" and value == "1":
            status = "✅"
        elif name == "IRQBalance" and "inactive" in value:
            status = "✅"
        elif name == "IRQBalance" and "Not installed" in value:
            status = "✅"
        elif name == "Busy Poll" and value == "50":
            status = "✅"
        elif name == "TCP Low Latency" and value == "1":
            status = "✅"
        elif name == "TCP Timestamps" and value == "0":
            status = "✅"
        elif name == "Network Buffers" and value == "134217728":
            status = "✅"
        elif name == "Tuned Profile" and value == "network-latency":
            status = "✅"
        
        print(f"{status} {name:<20} : {value}")
    
    print("\n✅ = Optimized, ❌ = Not optimized")
    
    # Check optimization logs
    print("\nChecking optimization logs:")
    
    # Check the optimizer log
    stdout, stderr, code = run_ssh_command(instance_id, key_path, "sudo tail -20 /var/log/dc-machine-optimizer.log 2>/dev/null || echo 'Optimizer log not found'")
    if "Optimizer log not found" not in stdout:
        print("\nOptimizer Log (/var/log/dc-machine-optimizer.log):")
        print("-" * 50)
        print(stdout)
    else:
        # Check older init log if new log not found
        stdout, stderr, code = run_ssh_command(instance_id, key_path, "sudo tail -20 /var/log/dc-machine-init.log 2>/dev/null || echo 'Init log not found'")
        if "Init log not found" not in stdout:
            print("\nInit Log (/var/log/dc-machine-init.log):")
            print("-" * 50)
            print(stdout)
    
    # Check if optimization marker exists
    stdout, stderr, code = run_ssh_command(instance_id, key_path, "[ -f /etc/dc-machine-optimized ] && echo 'Optimization marker found' || echo 'No optimization marker'")
    print(f"\nOptimization marker: {stdout.strip()}")
    
    # Check cloud-init status
    stdout, stderr, code = run_ssh_command(instance_id, key_path, "cloud-init status 2>/dev/null || echo 'cloud-init not available'")
    if "cloud-init not available" not in stdout:
        print(f"\nCloud-init status: {stdout.strip()}")

def main():
    parser = argparse.ArgumentParser(description="Check system optimizations on EC2 instance")
    parser.add_argument("instance_id", help="EC2 instance ID")
    parser.add_argument("--key-path", default="~/.ssh/dc-machine", help="SSH key path")
    
    args = parser.parse_args()
    
    # Expand ~ in key path
    import os
    key_path = os.path.expanduser(args.key_path)
    
    check_optimizations(args.instance_id, key_path)

if __name__ == "__main__":
    main()