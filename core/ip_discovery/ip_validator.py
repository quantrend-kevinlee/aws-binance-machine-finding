"""IP validation and liveness checking for DC Machine."""

import socket
import time
from typing import Dict, Tuple, List
from concurrent.futures import ThreadPoolExecutor, as_completed


class IPValidator:
    """Validates IP addresses by checking connectivity."""
    
    def __init__(self, port: int = 443, timeout: float = 2.0, max_workers: int = 10):
        """Initialize IP validator.
        
        Args:
            port: Port to test connectivity (default: 443 for HTTPS)
            timeout: Connection timeout in seconds
            max_workers: Maximum concurrent validation threads
        """
        self.port = port
        self.timeout = timeout
        self.max_workers = max_workers
    
    def validate_ip(self, ip: str) -> Tuple[bool, float]:
        """Validate a single IP by attempting TCP connection.
        
        Args:
            ip: IP address to validate
            
        Returns:
            Tuple of (is_alive, latency_ms), where latency_ms is -1 if failed
        """
        try:
            start_time = time.perf_counter()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((ip, self.port))
            sock.close()
            elapsed = (time.perf_counter() - start_time) * 1000  # Convert to ms
            
            if result == 0:
                return True, elapsed
            else:
                return False, -1
        except (socket.error, socket.timeout):
            return False, -1
    
    def validate_ips(self, ips: List[str], show_progress: bool = True) -> Dict[str, Tuple[bool, float]]:
        """Validate multiple IPs concurrently.
        
        Args:
            ips: List of IP addresses to validate
            show_progress: Whether to print progress messages
            
        Returns:
            Dictionary of ip -> (is_alive, latency_ms)
        """
        results = {}
        
        if show_progress:
            print(f"[INFO] Validating {len(ips)} IPs...")
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all validation tasks
            future_to_ip = {
                executor.submit(self.validate_ip, ip): ip 
                for ip in ips
            }
            
            # Collect results
            completed = 0
            for future in as_completed(future_to_ip):
                ip = future_to_ip[future]
                try:
                    is_alive, latency = future.result()
                    results[ip] = (is_alive, latency)
                    
                    completed += 1
                    if show_progress and completed % 10 == 0:
                        print(f"[INFO] Validated {completed}/{len(ips)} IPs...")
                        
                except Exception as e:
                    print(f"[WARN] Failed to validate {ip}: {e}")
                    results[ip] = (False, -1)
        
        if show_progress:
            alive_count = sum(1 for alive, _ in results.values() if alive)
            print(f"[INFO] Validation complete: {alive_count}/{len(ips)} IPs are alive")
        
        return results
    
    def validate_domain_ips(self, domain_ips: Dict[str, List[str]], 
                          show_progress: bool = True) -> Dict[str, Dict[str, Tuple[bool, float]]]:
        """Validate IPs grouped by domain.
        
        Args:
            domain_ips: Dictionary of domain -> list of IPs
            show_progress: Whether to print progress messages
            
        Returns:
            Dictionary of domain -> {ip -> (is_alive, latency_ms)}
        """
        results = {}
        
        for domain, ips in domain_ips.items():
            if show_progress:
                print(f"\n[INFO] Validating {domain} IPs...")
            
            domain_results = self.validate_ips(ips, show_progress=False)
            results[domain] = domain_results
            
            if show_progress:
                alive_count = sum(1 for alive, _ in domain_results.values() if alive)
                print(f"[INFO] {domain}: {alive_count}/{len(ips)} IPs alive")
        
        return results