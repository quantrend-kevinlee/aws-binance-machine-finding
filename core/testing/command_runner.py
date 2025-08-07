"""Local command execution with real-time progress display.

This module provides LocalCommandRunner for executing local commands with
the same interface and behavior as SSH remote execution, ensuring consistent
progress display and output handling across local and remote execution.
"""

import subprocess
import sys
import time
from typing import Tuple, List, Optional


class LocalCommandRunner:
    """Execute local commands with real-time progress display.
    
    This class mirrors the SSH client's functionality for local execution,
    providing consistent behavior between local and remote command execution.
    """
    
    def run_command_with_progress(self, cmd_args: List[str], 
                                 timeout: int = 1800) -> Tuple[str, str, int]:
        """Run local command with real-time progress display.
        
        This implementation provides:
        - Real-time stderr display for progress monitoring
        - Reliable stdout collection for final results
        - Proper separation of progress (stderr) and results (stdout)
        - Robust timeout and error handling
        
        Args:
            cmd_args: Command arguments as list (e.g., ['python3', 'script.py'])
            timeout: Command timeout in seconds
            
        Returns:
            Tuple of (stdout, stderr, return_code)
        """
        try:
            process = subprocess.Popen(
                cmd_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1  # Line buffered
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
                                # Display progress to local terminal (no [REMOTE] prefix for local)
                                print(line_stripped, file=sys.stderr)
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
                        print(line, file=sys.stderr)
                        
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
            return "", f"Execution error: {str(e)}", -1
    
    def run_command(self, cmd_args: List[str], timeout: int = 1800) -> Tuple[str, str, int]:
        """Run local command with simple buffered output.
        
        Use this for quick commands where real-time progress is not needed.
        
        Args:
            cmd_args: Command arguments as list
            timeout: Command timeout in seconds
            
        Returns:
            Tuple of (stdout, stderr, return_code)
        """
        try:
            result = subprocess.run(
                cmd_args,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", f"Command timed out after {timeout}s", -1
        except Exception as e:
            return "", f"Execution error: {str(e)}", -1