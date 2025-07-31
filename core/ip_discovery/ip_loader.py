"""IP list loader with DNS fallback capability."""

import json
import os
import socket
from typing import Dict, List, Optional


def load_ip_list(ip_list_file: Optional[str] = None, domains: Optional[List[str]] = None) -> Optional[Dict[str, List[str]]]:
    """Load IP list from file with DNS fallback.
    
    Args:
        ip_list_file: Path to IP list JSON file (optional)
        domains: List of domains for DNS fallback (optional)
        
    Returns:
        Dictionary mapping domain names to lists of IPs, or None if loading fails
    """
    
    # Try to load from file first
    if ip_list_file and os.path.exists(ip_list_file):
        try:
            with open(ip_list_file, 'r') as f:
                ip_data = json.load(f)
            
            # Extract all IPs from the data (all IPs are considered active)
            ip_list = {}
            for domain_name, domain_data in ip_data.get("domains", {}).items():
                ips = list(domain_data.get("ips", {}).keys())
                if ips:
                    ip_list[domain_name] = ips
            
            if ip_list:
                return ip_list
                
        except (json.JSONDecodeError, IOError):
            pass  # Fall through to DNS resolution
    
    # If no file or empty file, try DNS resolution as fallback
    if domains:
        print("[INFO] IP list not found, falling back to DNS resolution")
        ip_list = {}
        
        for domain in domains:
            try:
                # Get IPs from DNS
                ips = []
                for info in socket.getaddrinfo(domain, 443, socket.AF_INET, socket.SOCK_STREAM):
                    ip = info[4][0]
                    if ip not in ips:
                        ips.append(ip)
                
                if ips:
                    ip_list[domain] = ips
                    print(f"  - {domain}: {len(ips)} IPs from DNS")
                    
            except socket.gaierror as e:
                print(f"  - {domain}: DNS resolution failed: {e}")
                continue
        
        if ip_list:
            print("[INFO] Using DNS-resolved IPs (limited subset - run discover_ips.py for comprehensive list)")
            return ip_list
    
    return None