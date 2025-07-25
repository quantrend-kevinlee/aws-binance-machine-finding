"""Main orchestration logic for DC Machine."""

import sys
import time
import datetime
import os
from typing import Optional, Dict, Any

# Add parent directory to path to import binance_latency_test
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from binance_latency_test import DOMAINS

from .config import Config
from .aws import EC2Manager, PlacementGroupManager, EIPManager
from .champion import ChampionStateManager, ChampionEvaluator, ChampionEventLogger
from .testing import SSHClient, LatencyTestRunner, ResultProcessor
from .logging import JSONLLogger, TextLogger
from .utils import get_current_timestamp, get_log_file_paths, ensure_directory_exists


class Orchestrator:
    """Main orchestration class for finding low-latency instances."""
    
    def __init__(self, config: Config):
        """Initialize orchestrator with all required components.
        
        Args:
            config: Configuration object
        """
        self.config = config
        self.running = True
        self.instance_index = 0
        self.anchor_instance_id = None
        self.anchor_instance_type = None
        
        # Ensure report directory exists
        ensure_directory_exists(config.report_dir)
        
        # Initialize AWS managers
        self.ec2_manager = EC2Manager(config)
        self.pg_manager = PlacementGroupManager(config)
        self.eip_manager = EIPManager(config)
        
        # Initialize champion management
        champion_state_file = os.path.join(config.report_dir, "champion_state.json")
        self.champion_state_manager = ChampionStateManager(
            champion_state_file, self.ec2_manager, self.pg_manager
        )
        self.champion_evaluator = ChampionEvaluator()
        
        # Initialize testing components
        self.ssh_client = SSHClient(config.key_path)
        # Pass the number of domains and timeout configuration
        # Timeouts are configurable via timeout_per_domain_seconds and min_timeout_seconds
        self.latency_runner = LatencyTestRunner(
            self.ssh_client, 
            num_domains=len(DOMAINS),
            timeout_per_domain=config.timeout_per_domain_seconds,
            min_timeout=config.min_timeout_seconds
        )
        self.latency_runner.load_test_script()
        self.result_processor = ResultProcessor(
            config.median_threshold_us, config.best_threshold_us
        )
        
        # Initialize logging
        self._update_log_files()
        
        # Track start date for daily rotation
        self.start_date = datetime.date.today()
    
    def _update_log_files(self) -> None:
        """Update log file paths for current date."""
        jsonl_file, text_file, champion_log_file = get_log_file_paths(self.config.report_dir)
        
        self.jsonl_logger = JSONLLogger(jsonl_file)
        self.text_logger = TextLogger(text_file)
        self.champion_event_logger = ChampionEventLogger(champion_log_file)
    
    def run(self) -> None:
        """Run the main orchestration loop."""
        print(f"Starting small instance search in {self.config.availability_zone}...")
        
        try:
            while self.running:
                self._run_iteration()
        except KeyboardInterrupt:
            self._handle_shutdown()
        
        self._show_final_summary()
    
    def _run_iteration(self) -> None:
        """Run a single iteration of the main loop."""
        # Check for daily log rotation
        self._check_daily_rotation()
        
        # Select instance type
        instance_type = self.config.instance_types[self.instance_index]
        self.instance_index = (self.instance_index + 1) % len(self.config.instance_types)
        
        # Create placement group
        unix_timestamp = int(time.time())
        placement_group_name = self.pg_manager.generate_placement_group_name(unix_timestamp)
        
        print(f"\nCreating placement group {placement_group_name}...")
        if not self.pg_manager.create_placement_group(placement_group_name):
            time.sleep(5)
            return
        
        # Launch instance
        instance_name = f"{unix_timestamp}-DC-Search"
        print(f"Launching test instance of type {instance_type} ...")
        
        instance_id, error = self.ec2_manager.launch_instance(
            instance_type, placement_group_name, instance_name
        )
        
        if not instance_id:
            self._handle_launch_error(error, placement_group_name)
            return
        
        print(f"Instance {instance_id} launched.")
        
        # Process the instance
        success = self._process_instance(
            instance_id, instance_type, placement_group_name
        )
        
        if not success:
            # Instance failed somewhere in processing
            return
        
        # Check if we found an anchor
        if self.anchor_instance_id:
            self.running = False
    
    def _handle_launch_error(self, error: str, placement_group_name: str) -> None:
        """Handle instance launch error."""
        print(f"[ERROR] run_instances failed: {error}")
        
        # Clean up the placement group immediately
        print(f"Deleting unused placement group {placement_group_name}...")
        self.pg_manager.delete_placement_group(placement_group_name)
        
        # Check if it's a capacity error
        if self.ec2_manager.is_capacity_error(error):
            print(" -> Capacity/limit issue, will try next instance type.")
            self.instance_index = (self.instance_index + 1) % len(self.config.instance_types)
            time.sleep(2)
        else:
            time.sleep(5)
    
    def _process_instance(self, instance_id: str, instance_type: str, 
                         placement_group_name: str) -> bool:
        """Process a launched instance through all steps.
        
        Returns:
            True if processing completed successfully
        """
        # Wait for instance to be running
        if not self.ec2_manager.wait_for_running(instance_id):
            pass  # Continue anyway
        
        # Associate EIP
        if not self.eip_manager.associate_eip(instance_id):
            self.ec2_manager.terminate_instance(instance_id)
            self.pg_manager.schedule_async_cleanup(instance_id, placement_group_name)
            time.sleep(2)
            return False
        
        # Get EIP address
        eip_address = self.eip_manager.get_eip_address()
        if not eip_address:
            self.ec2_manager.terminate_instance(instance_id)
            self.pg_manager.schedule_async_cleanup(instance_id, placement_group_name)
            time.sleep(2)
            return False
        
        print(f"EIP address: {eip_address}")
        
        # Wait for SSH
        if not self.ssh_client.wait_for_ssh(eip_address):
            print("[ERROR] SSH not available after timeout. Terminating instance...")
            self.ec2_manager.terminate_instance(instance_id)
            self.pg_manager.schedule_async_cleanup(instance_id, placement_group_name)
            time.sleep(2)
            return False
        
        # Check or wait for EC2 status checks if configured
        if self.config.check_status_before_test or self.config.wait_for_status_checks:
            if self.config.wait_for_status_checks:
                # Wait for status checks to pass (blocking)
                if not self.ec2_manager.wait_for_status_checks(instance_id):
                    print("[WARN] Proceeding despite status checks not passing")
            else:
                # Just check status without waiting (non-blocking)
                self.ec2_manager.check_instance_status(instance_id)
        
        # Wait for network stack to fully initialize
        # This ensures accurate latency measurements by waiting for:
        # - CPU load from boot processes to settle
        # - Network stack optimization to complete
        # - ARP cache to populate
        # - Kernel network parameters to load
        # Wait time is configurable via network_init_wait_seconds in config.json
        self.ssh_client.wait_for_network_ready(
            eip_address, 
            wait_time=self.config.network_init_wait_seconds
        )
        
        # Run latency test
        results = self.latency_runner.run_latency_test(eip_address)
        if not results:
            self.ec2_manager.terminate_instance(instance_id)
            self.pg_manager.schedule_async_cleanup(instance_id, placement_group_name)
            time.sleep(2)
            return False
        
        # Process results
        self.latency_runner.display_results(
            results, self.config.median_threshold_us, self.config.best_threshold_us
        )
        domain_stats, instance_passed = self.result_processor.process_results(results)
        
        # Log results
        timestamp = get_current_timestamp()
        print(f"\n[{timestamp}] {instance_id}  {instance_type:<9}")
        print(self.result_processor.format_summary(
            instance_id, instance_type, domain_stats, instance_passed
        ))
        
        # Write logs
        self.jsonl_logger.log_test_result(
            timestamp, instance_id, instance_type, instance_passed, domain_stats
        )
        self.text_logger.log_test_result(
            timestamp, instance_id, instance_type, instance_passed, domain_stats,
            results, self.config.median_threshold_us, self.config.best_threshold_us
        )
        
        # Evaluate champions
        self._evaluate_champions(
            domain_stats, instance_id, instance_type, placement_group_name
        )
        
        # Check if this is an anchor instance
        if instance_passed:
            self._handle_anchor_instance(
                instance_id, instance_type, placement_group_name, domain_stats
            )
        else:
            self._handle_failed_instance(instance_id, placement_group_name)
        
        return True
    
    def _evaluate_champions(self, domain_stats: Dict[str, Any], instance_id: str,
                           instance_type: str, placement_group_name: str) -> None:
        """Evaluate and update champions."""
        current_champions = self.champion_state_manager.get_champions()
        
        new_champions, replaced_instances = self.champion_evaluator.evaluate_new_champions(
            domain_stats, current_champions, instance_id, instance_type,
            placement_group_name, DOMAINS
        )
        
        if new_champions:
            # Save new champions
            self.champion_state_manager.save_champions(new_champions)
            
            # Log champion events
            for domain, info in new_champions.items():
                old_champion = self.champion_evaluator.prepare_old_champion_info(
                    current_champions.get(domain, {}), instance_id
                )
                self.champion_event_logger.log_event(
                    domain, instance_id, instance_type,
                    info["median_latency"], info["best_latency"],
                    info["ip"], placement_group_name, old_champion
                )
            
            # Update instance name for the new champion
            # Get all domains this instance now champions
            instance_domains = self.champion_state_manager.get_instance_domains(instance_id)
            if instance_domains:
                new_name = self.champion_state_manager.generate_champion_name(instance_domains)
                self.ec2_manager.update_instance_name(instance_id, new_name)
            
            # Handle replaced instances
            all_champions = self.champion_state_manager.get_champions()
            replaceable = self.champion_evaluator.get_replaceable_instances(
                replaced_instances, all_champions
            )
            
            for old_instance_id in replaceable:
                self.ec2_manager.terminate_instance(old_instance_id)
                # Find and schedule cleanup of its placement group
                for info in all_champions.values():
                    if info.get("instance_id") == old_instance_id:
                        pg = info.get("placement_group")
                        if pg:
                            self.pg_manager.schedule_async_cleanup(old_instance_id, pg)
                            break
            
            # Update names for any instances that lost champion status but still champion other domains
            for old_instance_id in replaced_instances:
                if old_instance_id not in replaceable:
                    # This instance still champions some domains
                    remaining_domains = self.champion_state_manager.get_instance_domains(old_instance_id)
                    if remaining_domains:
                        new_name = self.champion_state_manager.generate_champion_name(remaining_domains)
                        self.ec2_manager.update_instance_name(old_instance_id, new_name)
    
    def _handle_anchor_instance(self, instance_id: str, instance_type: str,
                               placement_group_name: str, domain_stats: Dict[str, Any]) -> None:
        """Handle finding an anchor instance."""
        self.anchor_instance_id = instance_id
        self.anchor_instance_type = instance_type
        
        # Update instance name to reflect it's an anchor
        # Check if it's also a champion
        champion_domains = self.champion_state_manager.get_instance_domains(instance_id)
        if champion_domains:
            # It's both anchor and champion
            base_name = self.champion_state_manager.generate_champion_name(champion_domains)
            new_name = f"{base_name}-ANCHOR"
        else:
            # Just an anchor
            new_name = "DC-ANCHOR"
        
        self.ec2_manager.update_instance_name(instance_id, new_name)
        
        print(self.result_processor.format_anchor_report(
            instance_id, instance_type, placement_group_name,
            self.config.availability_zone, domain_stats
        ))
    
    def _handle_failed_instance(self, instance_id: str, placement_group_name: str) -> None:
        """Handle instance that didn't meet criteria."""
        if self.champion_state_manager.is_instance_champion(instance_id):
            # Champion is protected
            pass
        else:
            print(f"Instance {instance_id} did not meet latency target. "
                  f"Terminating and continuing...")
            if self.ec2_manager.terminate_instance(instance_id):
                print("  [OK] Instance termination initiated")
            
            # Schedule placement group deletion
            print(f"Scheduling placement group {placement_group_name} for deletion...")
            self.pg_manager.schedule_async_cleanup(instance_id, placement_group_name)
        
        time.sleep(2)
    
    def _check_daily_rotation(self) -> None:
        """Check if date changed and rotate log files."""
        today = datetime.date.today()
        if today != self.start_date:
            self._update_log_files()
            self.start_date = today
    
    def _handle_shutdown(self) -> None:
        """Handle graceful shutdown on Ctrl+C."""
        print("\n[CTRL-C] Graceful shutdown requested...")
        
        # Check if we have a current instance to handle
        current_instance = getattr(self, '_current_instance_id', None)
        if current_instance:
            if current_instance == self.anchor_instance_id:
                print(f"-> Preserving anchor instance {current_instance} "
                      f"(EIP will remain associated)")
            elif self.champion_state_manager.is_instance_champion(current_instance):
                domains = self.champion_state_manager.get_instance_domains(current_instance)
                print(f"-> Preserving champion {current_instance} for: {', '.join(domains)}")
            else:
                print(f"-> Terminating pending instance {current_instance} ...")
                self.ec2_manager.terminate_instance(current_instance)
                # Schedule placement group cleanup if exists
                current_pg = getattr(self, '_current_placement_group', None)
                if current_pg:
                    print(f"-> Scheduling cleanup of placement group {current_pg} ...")
                    self.pg_manager.schedule_async_cleanup(current_instance, current_pg)
        
        # Wait for cleanup threads
        self.pg_manager.wait_for_cleanup_threads()
    
    def _show_final_summary(self) -> None:
        """Show final summary after loop ends."""
        if self.anchor_instance_id:
            print(f"Anchor instance is {self.anchor_instance_id} "
                  f"({self.anchor_instance_type}). Keep it running for stage 3.")
        else:
            print("Search stopped without finding an anchor instance.")
        
        # Show champion summary
        champions = self.champion_state_manager.get_champions()
        summary = self.champion_event_logger.format_champion_summary(
            champions, self.config.eip_allocation_id, self.config.key_path
        )
        print(summary)
        
        # Show cleanup thread status
        active_count = self.pg_manager.get_active_cleanup_count()
        if active_count > 0:
            print(f"\n{active_count} background cleanup task(s) still running...")
            print("These will check instance status every minute for up to 30 minutes.")
            print("Placement groups will be deleted automatically when instances terminate.")
        time.sleep(2)