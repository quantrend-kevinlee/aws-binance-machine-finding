"""Configuration management for DC Machine."""

import json
import os
import sys
from typing import Dict, List


class Config:
    """Centralized configuration management with validation."""
    
    def __init__(self, config_path: str = "config.json"):
        """Load and validate configuration from JSON file.
        
        Args:
            config_path: Path to configuration file
        """
        self._load_and_validate(config_path)
    
    def _load_and_validate(self, config_path: str) -> None:
        """Load configuration and validate required fields."""
        try:
            with open(config_path, 'r') as f:
                self._data = json.load(f)
                
            # Expand user paths
            if 'key_path' in self._data and self._data['key_path'].startswith('~'):
                self._data['key_path'] = os.path.expanduser(self._data['key_path'])
                
            # Validate required fields
            required_fields = [
                'region', 'availability_zone', 'subnet_id', 'security_group_id',
                'key_name', 'key_path', 'placement_group_base',
                'latency_thresholds', 'instance_types', 'report_dir',
                'latency_test_domains', 'discovery_domains', 'ip_list_dir',
                'max_instance_init_wait_seconds', 'timeout_per_domain_seconds', 'min_timeout_seconds'
            ]
            
            missing_fields = [field for field in required_fields if field not in self._data]
            if missing_fields:
                raise ValueError(f"Missing required configuration fields: {missing_fields}")
                
        except FileNotFoundError:
            print("[ERROR] Configuration file 'config.json' not found")
            print("   Make sure config.json exists in the current directory")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON in configuration file: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"[ERROR] Error loading config: {e}")
            sys.exit(1)
    
    @property
    def region(self) -> str:
        """AWS region."""
        return self._data['region']
    
    @property
    def availability_zone(self) -> str:
        """AWS availability zone."""
        return self._data['availability_zone']
    
    @property
    def subnet_id(self) -> str:
        """VPC subnet ID."""
        return self._data['subnet_id']
    
    @property
    def security_group_id(self) -> str:
        """Security group ID."""
        return self._data['security_group_id']
    
    @property
    def key_name(self) -> str:
        """EC2 key pair name."""
        return self._data['key_name']
    
    @property
    def key_path(self) -> str:
        """Path to SSH private key."""
        return self._data['key_path']
    
    
    @property
    def placement_group_base(self) -> str:
        """Base name for placement groups."""
        return self._data['placement_group_base']
    
    @property
    def median_threshold_us(self) -> float:
        """Median latency threshold in microseconds."""
        return self._data['latency_thresholds']['median_us']
    
    @property
    def best_threshold_us(self) -> float:
        """Best latency threshold in microseconds."""
        return self._data['latency_thresholds']['best_us']
    
    @property
    def instance_types(self) -> List[str]:
        """List of EC2 instance types to test."""
        return self._data['instance_types']
    
    @property
    def report_dir(self) -> str:
        """Directory for report files."""
        return self._data['report_dir']
    
    @property
    def ip_list_dir(self) -> str:
        """Directory for IP list files."""
        return self._data['ip_list_dir']
    
    @property
    def max_instance_init_wait_seconds(self) -> int:
        """Maximum seconds to wait for instance initialization after SSH is ready."""
        return self._data['max_instance_init_wait_seconds']
    
    @property
    def timeout_per_domain_seconds(self) -> int:
        """Timeout per domain for latency testing."""
        return self._data['timeout_per_domain_seconds']
    
    @property
    def min_timeout_seconds(self) -> int:
        """Minimum timeout for latency testing regardless of domain count."""
        return self._data['min_timeout_seconds']
    
    @property
    def latency_test_domains(self) -> List[str]:
        """List of domains to test latency against during instance testing."""
        return self._data['latency_test_domains']
    
    @property
    def discovery_domains(self) -> List[str]:
        """List of domains for IP discovery (may include more domains than tested)."""
        return self._data['discovery_domains']