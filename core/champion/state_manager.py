"""Champion state persistence and management."""

import os
import json
from typing import Dict, Any, List, Optional

from ..aws.ec2_manager import EC2Manager
from ..aws.placement_group import PlacementGroupManager


class ChampionStateManager:
    """Manages champion state persistence and validation."""
    
    FORMAT_VERSION = "2.0"
    
    def __init__(self, state_file: str, ec2_manager: EC2Manager, 
                 pg_manager: PlacementGroupManager):
        """Initialize champion state manager.
        
        Args:
            state_file: Path to champion state JSON file
            ec2_manager: EC2 manager instance
            pg_manager: Placement group manager instance
        """
        self.state_file = state_file
        self.ec2_manager = ec2_manager
        self.pg_manager = pg_manager
        self.state = self._load_and_validate_state()
    
    def _load_and_validate_state(self) -> Dict[str, Any]:
        """Load existing champion state and validate instances are still running."""
        if not os.path.exists(self.state_file):
            return {"format_version": self.FORMAT_VERSION, "champions": {}}
        
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            print(f"[LOAD] Loaded champion state (format v{state.get('format_version', '1.0')}):")
            
            champions = state.get('champions', {})
            if not champions:
                return {"format_version": self.FORMAT_VERSION, "champions": {}}
            
            # Display all champions
            for domain, info in champions.items():
                print(f"   {domain}:")
                print(f"     Instance: {info.get('instance_id', 'N/A')}")
                print(f"     Median: {info.get('median_latency', 'N/A')}µs, "
                      f"Best: {info.get('best_latency', 'N/A')}µs")
                print(f"     IP: {info.get('ip', 'N/A')}")
                print(f"     PG: {info.get('placement_group', 'N/A')}")
            
            # Validate and clean up champions
            valid_champions = self._validate_champions(champions)
            state['champions'] = valid_champions
            
            # Save cleaned state if any champions were removed
            if len(valid_champions) < len(champions):
                self._save_state(state)
                print(f"   [SAVE] Updated champion state file with "
                      f"{len(valid_champions)} valid champions")
            
            return state
            
        except Exception as e:
            print(f"[WARN] Could not load champion state: {e}")
            return {"format_version": self.FORMAT_VERSION, "champions": {}}
    
    def _validate_champions(self, champions: Dict[str, Any]) -> Dict[str, Any]:
        """Validate champion instances are still running."""
        print(f"\n[CHECK] Validating champion instances...")
        
        # Group domains by instance
        instances_to_check = {}
        for domain, info in champions.items():
            instance_id = info.get('instance_id')
            if instance_id:
                if instance_id not in instances_to_check:
                    instances_to_check[instance_id] = []
                instances_to_check[instance_id].append(domain)
        
        # Get instance states
        instance_ids = list(instances_to_check.keys())
        instance_info = self.ec2_manager.describe_instances(instance_ids)
        
        # Validate each instance
        valid_champions = {}
        for instance_id, domains in instances_to_check.items():
            if instance_id in instance_info:
                state = instance_info[instance_id]['state']
                if state == 'running':
                    print(f"   [OK] Instance {instance_id} is running "
                          f"(champions: {', '.join(domains)})")
                    # Keep this instance's champion entries
                    for domain in domains:
                        valid_champions[domain] = champions[domain]
                else:
                    print(f"   [WARN] Instance {instance_id} is {state} - "
                          f"removing from champions")
                    # Terminate if needed and schedule cleanup
                    if state not in ['terminated', 'terminating']:
                        self.ec2_manager.terminate_instance(instance_id)
                    # Schedule placement group cleanup
                    for domain in domains:
                        pg = champions[domain].get('placement_group')
                        if pg:
                            self.pg_manager.schedule_async_cleanup(instance_id, pg)
                            break  # Only need to clean up PG once
            else:
                print(f"   [WARN] Instance {instance_id} not found - removing from champions")
        
        return valid_champions
    
    def save_champions(self, domain_updates: Dict[str, Any]) -> None:
        """Save champion state for specific domains.
        
        Args:
            domain_updates: Dict of domain -> champion info to update
        """
        try:
            # Update specific domains
            for domain, info in domain_updates.items():
                self.state['champions'][domain] = info
            
            # Ensure format version
            self.state['format_version'] = self.FORMAT_VERSION
            
            # Save to file
            self._save_state(self.state)
            
            print(f"[SAVE] Champion state saved to {self.state_file}")
            print(f"   Updated domains: {', '.join(domain_updates.keys())}")
            
        except Exception as e:
            print(f"[WARN] Could not save champion state: {e}")
    
    def _save_state(self, state: Dict[str, Any]) -> None:
        """Save state to file."""
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)
    
    def get_champions(self) -> Dict[str, Any]:
        """Get current champions."""
        return self.state.get('champions', {})
    
    def get_champion_for_domain(self, domain: str) -> Optional[Dict[str, Any]]:
        """Get champion info for a specific domain."""
        return self.state.get('champions', {}).get(domain)
    
    def is_instance_champion(self, instance_id: str) -> bool:
        """Check if instance is a champion for any domain."""
        return any(
            info.get('instance_id') == instance_id 
            for info in self.state.get('champions', {}).values()
        )
    
    def get_instance_domains(self, instance_id: str) -> List[str]:
        """Get list of domains this instance champions."""
        return [
            domain for domain, info in self.state.get('champions', {}).items()
            if info.get('instance_id') == instance_id
        ]
    
    def generate_champion_name(self, domains: List[str]) -> str:
        """Generate a name for a champion instance based on domains it champions.
        
        Args:
            domains: List of domain names this instance champions
            
        Returns:
            Generated name like "DC-Champ-fstream-ws-fapi"
        """
        if not domains:
            return "DC-Champ"
        
        # Extract short names from domains
        short_names = []
        for domain in sorted(domains):  # Sort for consistent naming
            if "fstream" in domain:
                short_names.append("fstream")
            elif "ws-fapi" in domain:
                short_names.append("ws-fapi")
            elif "fapi" in domain and "ws" not in domain:
                short_names.append("fapi")
            elif "stream.binance" in domain:
                short_names.append("stream")
            elif "ws-api" in domain:
                short_names.append("ws-api")
            elif "api.binance" in domain:
                short_names.append("api")
            else:
                # Fallback: use first part of domain
                short_names.append(domain.split('.')[0])
        
        # Remove duplicates while preserving order
        seen = set()
        unique_names = []
        for name in short_names:
            if name not in seen:
                seen.add(name)
                unique_names.append(name)
        
        # Join with hyphens, limit length
        name_suffix = "-".join(unique_names[:3])  # Limit to 3 domains
        if len(domains) > 3:
            name_suffix += f"-{len(domains)-3}more"
        
        return f"DC-Champ-{name_suffix}"