"""SSH client operations for the latency finder."""

import subprocess
import sys
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
    
    def _build_ssh_base_cmd(self, ip: str) -> list:
        """Build base SSH command with standard options.
        
        Args:
            ip: Target IP address
            
        Returns:
            List of SSH command components
        """
        return [
            "ssh",
            "-i", self.key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-o", "LogLevel=ERROR",
            f"ec2-user@{ip}"
        ]
    
    def _build_scp_base_cmd(self) -> list:
        """Build base SCP command with standard options.
        
        Returns:
            List of SCP command components (without source/destination)
        """
        return [
            "scp",
            "-i", self.key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-o", "LogLevel=ERROR"
        ]
    
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
        ssh_cmd = self._build_ssh_base_cmd(ip) + [command]
        
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
        """Run command via SSH with real-time stderr display and robust I/O handling.
        
        This implementation provides:
        - Real-time stderr display for progress monitoring
        - Reliable stdout collection for final results
        - Proper separation of progress (stderr) and results (stdout)
        - Robust timeout and error handling
        
        Args:
            ip: Target IP address
            command: Command to execute
            timeout: Command timeout in seconds
            
        Returns:
            Tuple of (stdout, collected_stderr, return_code)
        """
        ssh_cmd = self._build_ssh_base_cmd(ip) + [command]
        
        try:
            process = subprocess.Popen(
                ssh_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            stdout_data = []  # Collect stdout chunks for final processing
            stderr_lines = []  # Collect stderr lines for progress and final stderr
            
            # Setup non-blocking I/O if available
            try:
                import select
                import fcntl
                import os
                
                # Make both stderr and stdout non-blocking
                stderr_fd = process.stderr.fileno()
                stdout_fd = process.stdout.fileno()
                
                stderr_flags = fcntl.fcntl(stderr_fd, fcntl.F_GETFL)
                fcntl.fcntl(stderr_fd, fcntl.F_SETFL, stderr_flags | os.O_NONBLOCK)
                
                stdout_flags = fcntl.fcntl(stdout_fd, fcntl.F_GETFL)
                fcntl.fcntl(stdout_fd, fcntl.F_SETFL, stdout_flags | os.O_NONBLOCK)
                
                use_select = True
            except ImportError:
                # Fallback for systems without fcntl/select
                use_select = False
                print("[INFO] Real-time progress display not available on this system", file=sys.stderr)
            
            start_time = time.time()
            while True:
                # Check timeout
                if time.time() - start_time > timeout:
                    process.kill()
                    return "", f"Command timed out after {timeout}s\nProgress shown:\n" + "\n".join(stderr_lines), -1
                
                # Check if process has finished
                if process.poll() is not None:
                    break
                
                if use_select:
                    # Read both stderr and stdout with select
                    ready, _, _ = select.select([process.stderr, process.stdout], [], [], 0.1)
                    
                    # Handle stderr (progress) - display in real-time
                    if process.stderr in ready:
                        try:
                            line = process.stderr.readline()
                            if line:
                                line_stripped = line.rstrip()
                                stderr_lines.append(line_stripped)
                                # Display progress to local terminal
                                print(f"[REMOTE] {line_stripped}", file=sys.stderr)
                                sys.stderr.flush()
                        except:
                            pass
                    
                    # Handle stdout (results) - collect for final processing
                    if process.stdout in ready:
                        try:
                            # Read in chunks for better performance
                            data = process.stdout.read(8192)
                            if data:
                                stdout_data.append(data)
                        except:
                            pass
                else:
                    # Simple polling fallback without select
                    time.sleep(0.1)
            
            # Get any remaining output after process finished
            try:
                remaining_stdout, remaining_stderr = process.communicate(timeout=5)
                
                # Collect remaining stdout
                if remaining_stdout:
                    stdout_data.append(remaining_stdout)
                
                # Display and collect remaining stderr
                if remaining_stderr:
                    for line in remaining_stderr.splitlines():
                        stderr_lines.append(line)
                        print(f"[REMOTE] {line}", file=sys.stderr)
                        
            except subprocess.TimeoutExpired:
                # If communicate times out, kill process and use partial results
                process.kill()
                print("[WARN] Timeout while reading final output, using partial results", file=sys.stderr)
            
            # Join stdout data and return results
            full_stdout = ''.join(stdout_data)
            full_stderr = '\n'.join(stderr_lines)
            
            return full_stdout, full_stderr, process.returncode
            
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
        """Wait for instance to be ready for testing.
        
        This ensures the instance is stable by monitoring CPU load and optionally
        checking EC2 status checks. If all EC2 status checks pass (3/3), the wait
        period can end early.
        
        Args:
            ip: Target IP address
            wait_time: Maximum seconds to wait (configurable)
            instance_id: Optional EC2 instance ID for status checks
            ec2_manager: Optional EC2Manager instance for status checks
            
        Returns:
            True when instance is ready
        """
        if wait_time <= 0:
            print("[INFO] Instance readiness wait disabled (wait_time=0)")
            return True
            
        print(f"Waiting {wait_time}s for instance to stabilize...")
        
        start_time = time.time()
        check_interval = 5  # Check every 5 seconds
        last_ec2_check = 0  # Track when we last checked EC2 status
        ec2_check_interval = 10  # Check EC2 status every 10 seconds
        
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
                    print(f"  [{elapsed}s] CPU load: {load:.2f}")
                except ValueError:
                    pass
            
            # Check EC2 status if available
            if instance_id and ec2_manager and (elapsed - last_ec2_check >= ec2_check_interval):
                last_ec2_check = elapsed
                status_info = ec2_manager.get_instance_status(instance_id)
                if status_info.get("status") == "ok":
                    print(f"  [{elapsed}s] EC2 status checks: 3/3 passed (instance: {status_info.get('instance_status')}, system: {status_info.get('system_status')})")
                    print(f"[OK] Instance fully ready after {elapsed}s (EC2 checks passed)")
                    break
                elif status_info.get("status") != "unknown":
                    print(f"  [{elapsed}s] EC2 status checks: instance={status_info.get('instance_status')}, system={status_info.get('system_status')}")
            
            # Don't wait if we're about to exceed wait_time
            remaining = wait_time - (time.time() - start_time)
            if remaining > check_interval:
                time.sleep(check_interval)
            else:
                time.sleep(max(0, remaining))
                break
        
        final_elapsed = int(time.time() - start_time)
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
    
    def copy_file(self, ip: str, local_file_path: str, remote_file_path: str, timeout: int = 30) -> bool:
        """Copy file to remote instance using SCP.
        
        Args:
            ip: Target IP address
            local_file_path: Path to local file
            remote_file_path: Remote destination path
            timeout: SCP timeout in seconds
            
        Returns:
            True if successful, False otherwise
        """
        scp_cmd = self._build_scp_base_cmd() + [
            local_file_path,
            f"ec2-user@{ip}:{remote_file_path}"
        ]
        
        try:
            result = subprocess.run(
                scp_cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            if result.returncode != 0:
                print(f"[ERROR] SCP failed: {result.stderr}")
                return False
                
            return True
            
        except subprocess.TimeoutExpired:
            print(f"[ERROR] SCP timed out after {timeout}s")
            return False
        except Exception as e:
            print(f"[ERROR] SCP error: {str(e)}")
            return False
    
    def deploy_script(self, ip: str, script_content: str, script_path: str) -> bool:
        """Deploy a script to remote instance.
        
        DEPRECATED: Use copy_file() for better reliability. This method is kept
        for backward compatibility but will be removed in a future version.
        
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
