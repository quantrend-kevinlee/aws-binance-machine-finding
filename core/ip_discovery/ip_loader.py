"""IP list loader with DNS fallback capability."""

import json
import os
import socket
from typing import Dict, List, Optional


def load_ip_list(ip_list_file: Optional[str] = None, domains: Optional[List[str]] = None) -> Optional[Dict[str, List[str]]]:
    """Load IP list from file with DNS fallback.
    
    If domains are specified, only IPs for those domains will be loaded from the file.
    This allows different components to load only the IPs they need (e.g., latency testing
    only needs latency_test_domains, monitoring only needs monitoring_domains).
    
    Args:
        ip_list_file: Path to IP list JSON file (optional)
        domains: List of domains to filter/load. If None, loads all domains from file.
                Also used for DNS fallback if file is missing.
        
    Returns:
        Dictionary mapping domain names to lists of IPs, or None if loading fails
    """
    
    # Try to load from file first
    if ip_list_file and os.path.exists(ip_list_file):
        try:
            with open(ip_list_file, 'r') as f:
                ip_data = json.load(f)
            
            # Extract IPs only for requested domains
            ip_list = {}
            all_domains = ip_data.get("domains", {})
            
            # If domains specified, filter to only those domains
            if domains:
                for domain in domains:
                    if domain in all_domains:
                        ips = list(all_domains[domain].get("ips", {}).keys())
                        if ips:
                            ip_list[domain] = ips
            else:
                # No domains specified, load all
                for domain_name, domain_data in all_domains.items():
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