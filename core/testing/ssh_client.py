"""SSH client operations for DC Machine."""

import subprocess
import sys
import time
import os
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
    
    def run_command(self, ip: str, command: str, timeout: int = DEFAULT_SSH_TIMEOUT, 
                   capture_stderr: bool = True) -> Tuple[str, str, int]:
        """Run command via SSH and return output.
        
        Args:
            ip: Target IP address
            command: Command to execute
            timeout: Command timeout in seconds
            capture_stderr: Whether to capture stderr separately
            
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
    
    def run_command_with_progress(self, ip: str, command: str, 
                                 timeout: int = DEFAULT_SSH_TIMEOUT) -> Tuple[str, str, int]:
        """Run command via SSH with real-time stderr display.
        
        This is useful for long-running commands that output progress to stderr.
        
        Args:
            ip: Target IP address
            command: Command to execute
            timeout: Command timeout in seconds
            
        Returns:
            Tuple of (stdout, collected_stderr, return_code)
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
            process = subprocess.Popen(
                ssh_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1  # Line buffered
            )
            
            stdout_lines = []
            stderr_lines = []
            
            # Read stderr in real-time and display it
            try:
                import select
                import fcntl
                import os
                
                # Make stderr non-blocking
                stderr_fd = process.stderr.fileno()
                flags = fcntl.fcntl(stderr_fd, fcntl.F_GETFL)
                fcntl.fcntl(stderr_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                use_select = True
            except ImportError:
                # Fallback for systems without fcntl/select
                use_select = False
                print("[INFO] Real-time progress display not available on this system", file=sys.stderr)
            
            start_time = time.time()
            while True:
                if time.time() - start_time > timeout:
                    process.kill()
                    return "", "Command timed out after showing progress:\n" + "\n".join(stderr_lines), -1
                
                # Check if process has finished
                if process.poll() is not None:
                    break
                
                if use_select:
                    # Read any available stderr
                    ready, _, _ = select.select([process.stderr], [], [], 0.1)
                    if ready:
                        try:
                            line = process.stderr.readline()
                            if line:
                                stderr_lines.append(line.rstrip())
                                # Display progress to local terminal
                                print(f"[REMOTE] {line.rstrip()}", file=sys.stderr)
                                sys.stderr.flush()
                        except:
                            pass
                else:
                    # Simple polling without select
                    time.sleep(0.1)
            
            # Get remaining output
            stdout, remaining_stderr = process.communicate()
            if stdout:
                stdout_lines.append(stdout)
            if remaining_stderr:
                for line in remaining_stderr.splitlines():
                    stderr_lines.append(line)
                    print(f"[REMOTE] {line}", file=sys.stderr)
            
            return ''.join(stdout_lines), '\n'.join(stderr_lines), process.returncode
            
        except subprocess.TimeoutExpired:
            process.kill()
            return "", "Command timed out", -1
        except Exception as e:
            return "", f"SSH error: {str(e)}", -1
    
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
                print("[OK] SSH is ready!")
                return True
            print(f"  Attempt {i+1}/{max_attempts}...")
            time.sleep(DEFAULT_SSH_RETRY_DELAY)
        
        return False
    
    def wait_for_instance_ready(self, ip: str, wait_time: int = 30, 
                               instance_id: str = None, ec2_manager = None) -> bool:
        """Wait for EC2 instance to be fully ready for testing.
        
        This ensures the instance is healthy and stable by:
        - Monitoring EC2 status checks (system and instance health)
        - Checking CPU load to ensure boot processes have settled
        - Verifying network connectivity
        - Allowing time for kernel parameters and optimizations to load
        
        Will break early if:
        - EC2 status checks pass (both system and instance show 'ok')
        - EC2 status checks fail (either shows 'impaired')
        
        Args:
            ip: Target IP address
            wait_time: Maximum seconds to wait (configurable)
            instance_id: EC2 instance ID (optional, for status checks)
            ec2_manager: EC2Manager instance (optional, for status checks)
            
        Returns:
            True if instance is ready, False if status checks failed (instance should be terminated)
        """
        if wait_time <= 0:
            print("[INFO] Instance readiness wait disabled (wait_time=0)")
            return True
            
        print(f"Waiting up to {wait_time}s for instance to be fully ready...")
        print("  Will break early if EC2 status checks pass or fail")
        
        start_time = time.time()
        check_interval = 5  # Check every 5 seconds
        status_check_interval = 15  # Check EC2 status every 15 seconds
        last_status_check = 0
        status_checks_passed = False
        
        while time.time() - start_time < wait_time:
            elapsed = int(time.time() - start_time)
            
            # Check CPU load
            stdout, stderr, code = self.run_command(
                ip, 
                "uptime | awk '{print $(NF-2)}' | sed 's/,//'", 
                timeout=5
            )
            if code == 0 and stdout.strip():
                try:
                    load = float(stdout.strip())
                    status_msg = f"  [{elapsed}s] CPU load: {load:.2f}"
                    
                    # Check EC2 status if enabled
                    if (instance_id and ec2_manager and 
                        elapsed - last_status_check >= status_check_interval):
                        last_status_check = elapsed
                        
                        try:
                            status = ec2_manager.get_instance_status(instance_id)
                            system_ok = status.get('system_status') == 'ok'
                            instance_ok = status.get('instance_status') == 'ok'
                            
                            # Check for EBS status (3rd check)
                            # If no EBS info, assume it's ok (not all instances have EBS)
                            ebs_ok = True
                            for detail in status.get('system_details', []):
                                if detail.get('Name') == 'reachability':
                                    ebs_ok = detail.get('Status') == 'passed'
                                    break
                            
                            checks_passed = system_ok and instance_ok and ebs_ok
                            status_msg += f" | Status checks: System={status.get('system_status')}, "
                            status_msg += f"Instance={status.get('instance_status')}"
                            
                            # Check if status is impaired (failed)
                            system_impaired = status.get('system_status') == 'impaired'
                            instance_impaired = status.get('instance_status') == 'impaired'
                            
                            if system_impaired or instance_impaired:
                                print(f"\n  [{elapsed}s] ❌ EC2 status check FAILED!")
                                print(f"  System: {status.get('system_status')}, Instance: {status.get('instance_status')}")
                                print("  This instance has failed status checks and should be terminated.")
                                return False  # Signal that instance should be terminated
                            
                            if checks_passed and not status_checks_passed:
                                status_checks_passed = True
                                print(f"\n  [{elapsed}s] ✅ All EC2 status checks passed (3/3)!")
                                print("  Breaking early - instance is fully ready")
                                break
                        except Exception as e:
                            # Don't fail if status check fails, just continue waiting
                            pass
                    
                    print(status_msg)
                except ValueError:
                    pass
            
            # Don't wait if we're about to exceed wait_time
            remaining = wait_time - (time.time() - start_time)
            if remaining > check_interval:
                time.sleep(check_interval)
            else:
                time.sleep(max(0, remaining))
                break
        
        final_elapsed = int(time.time() - start_time)
        
        # Do one final status check if we have the capability
        if instance_id and ec2_manager and not status_checks_passed:
            try:
                status = ec2_manager.get_instance_status(instance_id)
                if status.get('system_status') == 'impaired' or status.get('instance_status') == 'impaired':
                    print(f"\n[ERROR] Final status check FAILED after {final_elapsed}s")
                    print(f"  System: {status.get('system_status')}, Instance: {status.get('instance_status')}")
                    return False  # Signal termination needed
            except:
                pass
        
        if status_checks_passed:
            print(f"\n[OK] Instance ready after {final_elapsed}s (status checks passed)")
        else:
            print(f"\n[OK] Instance readiness wait complete after {final_elapsed}s")
        
        # Final check - ensure basic network operations work
        stdout, stderr, code = self.run_command(
            ip,
            "ping -c 1 -W 1 8.8.8.8 >/dev/null 2>&1 && echo 'Network ready'",
            timeout=5
        )
        
        if code == 0 and "Network ready" in stdout:
            print("[OK] Network connectivity verified!")
        else:
            print("[WARN] Network connectivity check failed, proceeding anyway")
        
        return True
    
    def check_optimization_status(self, ip: str) -> bool:
        """Check if system optimizations were applied by user data.
        
        Args:
            ip: Target IP address
            
        Returns:
            True if optimizations were applied
        """
        print("\n[Machine Optimization] Checking optimization status...")
        
        # Check if optimization marker exists
        stdout, stderr, code = self.run_command(
            ip,
            "[ -f /etc/dc-machine-optimized ] && echo 'OPTIMIZED' || echo 'NOT_OPTIMIZED'",
            timeout=5
        )
        
        if code == 0 and "OPTIMIZED" in stdout:
            print("[Machine Optimization] ✅ System optimizations already applied by user data")
            
            # Show last few lines of optimization log
            stdout, stderr, code = self.run_command(
                ip,
                "sudo tail -5 /var/log/dc-machine-optimizer.log 2>/dev/null | grep -E '(completed|WARN|ERROR)'",
                timeout=5
            )
            if stdout.strip():
                print(f"[Machine Optimization] Recent log entries:\n{stdout}")
            
            return True
        else:
            print("[Machine Optimization] ⚠️  Optimizations not detected")
            print("[Machine Optimization] This instance may not have the latest user data script")
            print("[Machine Optimization] To apply optimizations manually, use: python3 tool_scripts/run_optimizer.py <instance-id>")
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
    
    def apply_system_optimizations(self, ip: str) -> bool:
        """Apply system optimizations manually if not already applied.
        
        This method is kept for backward compatibility and manual optimization.
        New instances should have optimizations applied via user data.
        
        Args:
            ip: Target IP address
            
        Returns:
            True if optimizations were successful
        """
        # First check if already optimized
        if self.check_optimization_status(ip):
            return True
        
        print("\n[Machine Optimization] Applying system optimizations manually...")
        
        # Import the script generator from system_optimizer module
        try:
            from ..system_optimizer import generate_optimization_script
            optimizer_script = generate_optimization_script()
        except ImportError:
            print("[Machine Optimization] ERROR: system_optimizer module not found")
            return False
        
        # Deploy and run system optimizer
        deploy_success = self.deploy_script(ip, optimizer_script, "/tmp/optimize_system.sh")
        
        if not deploy_success:
            print("[Machine Optimization] WARN: Failed to deploy optimization script")
            print("[Machine Optimization] This may happen if SSH is still initializing. The instance will run without optimizations.")
            print("[Machine Optimization] To apply optimizations later, use: python3 tool_scripts/run_optimizer.py <instance-id>")
            return False
        
        # Make script executable and run it
        stdout, stderr, code = self.run_command(
            ip,
            "chmod +x /tmp/optimize_system.sh && sudo /tmp/optimize_system.sh",
            timeout=30
        )
        
        if code == 0:
            print("[Machine Optimization] OK: System optimizations applied successfully!")
            return True
        else:
            print(f"[Machine Optimization] WARN: Some optimizations may have failed: {stderr}")
            return False