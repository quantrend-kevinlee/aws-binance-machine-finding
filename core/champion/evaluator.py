"""Champion evaluation logic for DC Machine."""

from typing import Dict, Set, Tuple, Any, Optional
import datetime

from ..utils import get_current_timestamp


class ChampionEvaluator:
    """Evaluates and selects champions based on latency metrics."""
    
    def evaluate_new_champions(self, domain_stats: Dict[str, Any], 
                              current_champions: Dict[str, Any],
                              instance_id: str, instance_type: str,
                              placement_group: str,
                              domains: list) -> Tuple[Dict[str, Any], Set[str]]:
        """Evaluate if current instance beats existing champions.
        
        Args:
            domain_stats: Latency statistics per domain
            current_champions: Current champion instances
            instance_id: Current instance ID
            instance_type: Current instance type
            placement_group: Current placement group name
            domains: List of domains to evaluate
            
        Returns:
            Tuple of (new_champions dict, replaced_instance_ids set)
        """
        new_champions = {}
        replaced_instances = set()
        
        for domain in domains:
            if domain not in domain_stats or domain_stats[domain]["best_median"] >= float("inf"):
                print(f"\n[WARN] No valid data for {domain} on instance {instance_id}")
                continue
            
            current_median = domain_stats[domain]["best_median"]
            current_best = domain_stats[domain]["best_best"]
            current_ip = domain_stats[domain]["best_median_ip"]
            
            # Get current champion for this domain
            current_champion = current_champions.get(domain, {})
            champion_median = current_champion.get("median_latency", float("inf"))
            
            # Check if this instance beats the current champion
            if current_median < champion_median:
                print(f"\n[CHAMPION] New {domain} champion! "
                      f"Median: {current_median:.2f}µs (best: {current_best:.2f}µs) "
                      f"on {current_ip}")
                
                # Track replaced instance
                if current_champion and current_champion.get("instance_id") != instance_id:
                    old_id = current_champion["instance_id"]
                    replaced_instances.add(old_id)
                    print(f"   Replacing: {old_id} (median: {champion_median:.2f}µs)")
                
                # Record new champion for this domain
                new_champions[domain] = {
                    "instance_id": instance_id,
                    "placement_group": placement_group,
                    "median_latency": current_median,
                    "best_latency": current_best,
                    "ip": current_ip,
                    "instance_type": instance_type,
                    "timestamp": get_current_timestamp()
                }
        
        return new_champions, replaced_instances
    
    def get_replaceable_instances(self, replaced_instances: Set[str],
                                 all_champions: Dict[str, Any]) -> Set[str]:
        """Determine which replaced instances can be terminated.
        
        Args:
            replaced_instances: Set of potentially replaced instance IDs
            all_champions: All current champions
            
        Returns:
            Set of instance IDs that can be terminated
        """
        replaceable = set()
        
        for instance_id in replaced_instances:
            # Check if instance is still champion for any domain
            champion_domains = [
                d for d, info in all_champions.items()
                if info.get("instance_id") == instance_id
            ]
            
            if not champion_domains:
                replaceable.add(instance_id)
                print(f"\n[DELETE] {instance_id} - no longer champion for any domain")
            else:
                print(f"\n[PROTECTED] Keeping {instance_id} - "
                      f"still champion for: {', '.join(champion_domains)}")
        
        return replaceable
    
    def prepare_old_champion_info(self, current_champion: Dict[str, Any],
                                 instance_id: str) -> Optional[Dict[str, Any]]:
        """Prepare old champion info for logging.
        
        Args:
            current_champion: Current champion info
            instance_id: New champion instance ID
            
        Returns:
            Old champion info dict or None
        """
        if current_champion and current_champion.get("instance_id") != instance_id:
            return {
                "instance_id": current_champion["instance_id"],
                "median_latency": current_champion.get("median_latency", float("inf"))
            }
        return None