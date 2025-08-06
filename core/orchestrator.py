"""Main orchestration logic for the latency finder."""

import time
import os
from typing import Dict, Any

from .config import Config
from .aws import EC2Manager, PlacementGroupManager, EIPManager
from .testing import SSHClient, LatencyTestRunner, ResultProcessor
from .logging import JSONLLogger, TextLogger, DetailedJSONLLogger
from .utils import get_current_timestamp, get_log_file_paths, get_run_timestamp, ensure_directory_exists
from .ip_discovery import load_ip_list
from .monitoring import MonitoringDeployer


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
        self.qualified_instances = []  # List of (instance_id, instance_type, placement_group, eip_allocation_id) tuples
        
        # Track current instance for cleanup on Ctrl+C
        self._current_instance_id = None
        self._current_placement_group = None
        self._current_eip_allocation_id = None
        self._current_eip_name = None
        self._current_eip_associated = False  # Track if EIP is associated with instance
        
        # Ensure report directory exists
        ensure_directory_exists(config.report_dir)
        
        # Initialize AWS managers
        self.ec2_manager = EC2Manager(config)
        self.pg_manager = PlacementGroupManager(config)
        self.eip_manager = EIPManager(config)
        self.monitoring_deployer = MonitoringDeployer(config)
        
        # Initialize testing components
        self.ssh_client = SSHClient(config.key_path)
        # Initialize latency test runner with TCP timeout from config
        self.latency_runner = LatencyTestRunner(
            self.ssh_client, 
            domains=config.latency_test_domains,
            tcp_timeout_ms=config.tcp_connection_timeout_ms
        )
        self.latency_runner.load_test_script()
        self.result_processor = ResultProcessor(
            config.median_threshold_us, config.best_threshold_us
        )
        
        # Generate run timestamp and initialize logging
        self.run_timestamp = get_run_timestamp()
        self._initialize_loggers()
        
        # IP list loaded from file
        self.ip_list = None
    
    def _initialize_loggers(self) -> None:
        """Initialize all three loggers for current run."""
        summary_jsonl_file, text_file, detailed_jsonl_file = get_log_file_paths(
            self.config.report_dir,
            int(self.config.median_threshold_us),
            int(self.config.best_threshold_us),
            self.run_timestamp
        )
        
        self.jsonl_logger = JSONLLogger(summary_jsonl_file)
        self.text_logger = TextLogger(text_file)
        self.detailed_jsonl_logger = DetailedJSONLLogger(detailed_jsonl_file)
    
    def _load_ip_list(self) -> None:
        """Load IP list from file with DNS fallback."""
        ip_list_file = os.path.join(self.config.ip_list_dir, "ip_list_latest.json")
        self.ip_list = load_ip_list(ip_list_file, self.config.latency_test_domains)
        
        if self.ip_list is None:
            print(f"[ERROR] Failed to load IPs from file or DNS")
            print("[INFO] Run 'python3 discover_ips.py' for comprehensive IP discovery")
            self.ip_list = {}
        else:
            # Report what we loaded
            total_count = sum(len(ips) for ips in self.ip_list.values())
            if total_count > 0:
                print(f"[INFO] Loaded {total_count} IPs:")
                for domain, ips in self.ip_list.items():
                    print(f"  - {domain}: {len(ips)} IPs")
            else:
                print("[WARN] No IPs found")
                self.ip_list = {}
        
        print("="*60 + "\n")
    
    def run(self) -> None:
        """Run the main orchestration loop."""
        print(f"Starting AWS instance search in AZ {self.config.availability_zone}...")
        
        # Log IP assignment mode
        if self.config.use_eip:
            print(f"IP Mode: Elastic IP (EIP) - Each instance will get a dedicated EIP")
            print(f"         EIPs will be preserved with qualified instances")
        else:
            print(f"IP Mode: Auto-assigned - Using subnet's auto-assigned public IPs")
        print(f"Latency thresholds: median ≤ {self.config.median_threshold_us}μs OR best ≤ {self.config.best_threshold_us}μs")
        print("="*60)
        
        # Load IP list from file
        self._load_ip_list()
        
        try:
            while self.running:
                self._run_iteration()
        except KeyboardInterrupt:
            self._handle_shutdown()
        
        self._show_final_summary()
    
    def _run_iteration(self) -> None:
        """Run a single iteration of the main loop."""
        
        # Select instance type
        instance_type = self.config.instance_types[self.instance_index]
        self.instance_index = (self.instance_index + 1) % len(self.config.instance_types)
        
        # Create placement group
        unix_timestamp = int(time.time())
        placement_group_name = self.pg_manager.generate_placement_group_name(unix_timestamp)
        
        # Track placement group for cleanup
        self._current_placement_group = placement_group_name
        
        print(f"\nCreating placement group {placement_group_name}...")
        if not self.pg_manager.create_placement_group(placement_group_name):
            self._current_placement_group = None
            time.sleep(5)
            return
        
        # Allocate EIP if needed
        eip_allocation_id = None
        eip_name = None
        if self.config.use_eip:
            eip_name = self.eip_manager.generate_eip_name(unix_timestamp)
            self._current_eip_name = eip_name
            
            print(f"Allocating EIP {eip_name}...")
            eip_allocation_id, eip_public_ip, eip_error = self.eip_manager.allocate_eip(eip_name)
            
            if not eip_allocation_id:
                print(f"[ERROR] EIP allocation failed: {eip_error}")
                print(f"Deleting placement group {placement_group_name}...")
                self.pg_manager.delete_placement_group(placement_group_name)
                self._current_placement_group = None
                self._current_eip_name = None
                time.sleep(5)
                return
            
            # Track EIP for cleanup
            self._current_eip_allocation_id = eip_allocation_id
        
        # Launch instance
        instance_name = f"Search_{unix_timestamp}_{int(self.config.median_threshold_us)}/{int(self.config.best_threshold_us)}"
        print(f"Launching test instance of type {instance_type} ...")
        
        instance_id, error = self.ec2_manager.launch_instance(
            instance_type, placement_group_name, instance_name
        )
        
        if not instance_id:
            self._handle_launch_error(error, placement_group_name, eip_allocation_id, eip_name)
            self._current_placement_group = None
            self._current_eip_allocation_id = None
            self._current_eip_name = None
            self._current_eip_associated = False
            return
        
        # Track instance for cleanup
        self._current_instance_id = instance_id
        print(f"[OK] Instance {instance_id} launched.")
        
        # Process the instance
        success = self._process_instance(
            instance_id, instance_type, placement_group_name, eip_allocation_id, eip_name
        )
        
        # Clear tracking after processing
        self._current_instance_id = None
        self._current_placement_group = None
        self._current_eip_allocation_id = None
        self._current_eip_name = None
        self._current_eip_associated = False
        
        if not success:
            # Instance failed somewhere in processing
            return
        
    
    def _handle_launch_error(self, error: str, placement_group_name: str, 
                           eip_allocation_id: str, eip_name: str) -> None:
        """Handle instance launch error."""
        print(f"[ERROR] run_instances failed: {error}")
        
        # Clean up the EIP immediately (only if using EIP mode)
        if eip_allocation_id:
            print(f"Releasing unused EIP {eip_name}...")
            self.eip_manager.release_eip(eip_allocation_id)
        
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
                         placement_group_name: str, eip_allocation_id: str, eip_name: str) -> bool:
        """Process a launched instance through all steps.
        
        Returns:
            True if processing completed successfully
        """
        # Wait for instance to be running
        if not self.ec2_manager.wait_for_running(instance_id):
            print("[WARN] Instance not running within timeout, terminating...")
            self.ec2_manager.terminate_instance(instance_id)
            self.pg_manager.schedule_async_cleanup(instance_id, placement_group_name)
            if self.config.use_eip and eip_allocation_id:
                self.eip_manager.schedule_async_eip_cleanup(instance_id, eip_allocation_id, eip_name)
            return False
        
        # Get public IP - either from EIP or auto-assigned
        if self.config.use_eip:
            # Associate EIP with instance
            print(f"Associating EIP with instance...")
            if not self.eip_manager.associate_eip(eip_allocation_id, instance_id):
                print("[ERROR] Failed to associate EIP with instance. Terminating...")
                self.ec2_manager.terminate_instance(instance_id)
                self.pg_manager.schedule_async_cleanup(instance_id, placement_group_name)
                self.eip_manager.schedule_async_eip_cleanup(instance_id, eip_allocation_id, eip_name)
                time.sleep(2)
                return False
            
            # Mark EIP as associated
            self._current_eip_associated = True
            
            # Get EIP public IP for testing
            public_ip = self.eip_manager.get_eip_public_ip(eip_allocation_id)
            if not public_ip:
                print("[ERROR] Could not get EIP public IP. Terminating...")
                self.ec2_manager.terminate_instance(instance_id)
                self.pg_manager.schedule_async_cleanup(instance_id, placement_group_name)
                self.eip_manager.schedule_async_eip_cleanup(instance_id, eip_allocation_id, eip_name)
                time.sleep(2)
                return False
            
            print(f"[OK] Instance has EIP: {public_ip}")
        else:
            # Get auto-assigned public IP
            print(f"Getting auto-assigned public IP...")
            # Wait a moment for AWS to assign the public IP
            time.sleep(5)
            public_ip = self.ec2_manager.get_instance_public_ip(instance_id)
            if not public_ip:
                print("[ERROR] Instance has no auto-assigned public IP. Terminating...")
                print("       Check subnet auto-assign public IP setting")
                self.ec2_manager.terminate_instance(instance_id)
                self.pg_manager.schedule_async_cleanup(instance_id, placement_group_name)
                time.sleep(2)
                return False
            
            print(f"[OK] Instance has auto-assigned IP: {public_ip}")
        
        test_ip = public_ip
        
        # Wait for SSH
        if not self.ssh_client.wait_for_ssh(test_ip):
            print("[ERROR] SSH not available after timeout. Terminating instance...")
            self.ec2_manager.terminate_instance(instance_id)
            self.pg_manager.schedule_async_cleanup(instance_id, placement_group_name)
            if self.config.use_eip and eip_allocation_id:
                self.eip_manager.schedule_async_eip_cleanup(instance_id, eip_allocation_id, eip_name)
            time.sleep(2)
            return False
        
        # Wait for instance to be ready for testing
        # This ensures the instance is stable by monitoring CPU load
        # Can exit early if EC2 status checks pass (3/3)
        # Wait time is configurable via max_instance_init_wait_seconds in config.json
        self.ssh_client.wait_for_instance_ready(
            test_ip, 
            wait_time=self.config.max_instance_init_wait_seconds,
            instance_id=instance_id,
            ec2_manager=self.ec2_manager
        )
        
        # Run latency test with current IP list (already includes any newly discovered IPs)
        results = self.latency_runner.run_latency_test(test_ip, ip_list=self.ip_list)
        if not results:
            self.ec2_manager.terminate_instance(instance_id)
            self.pg_manager.schedule_async_cleanup(instance_id, placement_group_name)
            if self.config.use_eip and eip_allocation_id:
                self.eip_manager.schedule_async_eip_cleanup(instance_id, eip_allocation_id, eip_name)
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
        
        # Determine IP mode for logging
        ip_mode = "eip" if self.config.use_eip else "auto-assigned"
        
        # Write logs
        self.jsonl_logger.log_test_result(
            timestamp, instance_id, instance_type, instance_passed, domain_stats,
            ip_mode, test_ip
        )
        self.text_logger.log_test_result(
            timestamp, instance_id, instance_type, instance_passed, domain_stats,
            results, self.config.median_threshold_us, self.config.best_threshold_us,
            ip_mode, test_ip
        )
        self.detailed_jsonl_logger.log_test_result(
            timestamp, instance_id, instance_type, instance_passed, results,
            self.config.median_threshold_us, self.config.best_threshold_us,
            ip_mode, test_ip
        )
        
        # Check if this is a qualified instance
        if instance_passed:
            self._handle_qualified_instance(
                instance_id, instance_type, placement_group_name, eip_allocation_id, domain_stats
            )
        else:
            self._handle_failed_instance(instance_id, placement_group_name, eip_allocation_id, eip_name)
        
        return True
    
    def _handle_qualified_instance(self, instance_id: str, instance_type: str,
                                   placement_group_name: str, eip_allocation_id: str, 
                                   domain_stats: Dict[str, Any]) -> None:
        """Handle finding a qualified instance."""
        # Track this qualified instance (including EIP)
        self.qualified_instances.append((instance_id, instance_type, placement_group_name, eip_allocation_id))
        
        # Update instance name to reflect it's qualified with criteria
        timestamp = int(time.time())
        new_name = f"Qualified_{timestamp}_{int(self.config.median_threshold_us)}/{int(self.config.best_threshold_us)}"
        self.ec2_manager.update_instance_name(instance_id, new_name)
        
        # Enable stop protection for qualified instances
        # Note: Stop protection prevents both stop AND termination via console/CLI/API
        self.ec2_manager.enable_stop_protection(instance_id)
        
        print(self.result_processor.format_qualified_report(
            instance_id, instance_type, placement_group_name,
            self.config.availability_zone, domain_stats
        ))
        
        # Deploy continuous monitoring
        print("\n[INFO] Deploying continuous monitoring...")
        
        # Get instance public IP
        if self.config.use_eip and eip_allocation_id:
            instance_ip = self.eip_manager.get_eip_public_ip(eip_allocation_id)
        else:
            instance_ip = self.ec2_manager.get_instance_public_ip(instance_id)
        
        if instance_ip:
            if self.monitoring_deployer.deploy_monitoring(instance_id, instance_ip):
                print("[OK] Continuous monitoring deployed successfully")
            else:
                print("[WARN] Failed to deploy monitoring - instance is still qualified")
        else:
            print("[WARN] Could not get instance IP for monitoring deployment")
    
    def _handle_failed_instance(self, instance_id: str, placement_group_name: str, 
                               eip_allocation_id: str, eip_name: str) -> None:
        """Handle instance that didn't meet criteria."""
        print(f"Instance {instance_id} did not meet latency target. "
              f"Terminating and continuing...")
        
        if self.ec2_manager.terminate_instance(instance_id):
            print("  [OK] Instance termination initiated")
        
        # Schedule placement group deletion
        print(f"Scheduling placement group {placement_group_name} for deletion...")
        self.pg_manager.schedule_async_cleanup(instance_id, placement_group_name)
        
        # Schedule EIP release if using EIP
        if self.config.use_eip and eip_allocation_id:
            print(f"Scheduling EIP {eip_name} for release...")
            self.eip_manager.schedule_async_eip_cleanup(instance_id, eip_allocation_id, eip_name)
        
        time.sleep(2)
    
    
    def _handle_shutdown(self) -> None:
        """Handle graceful shutdown on Ctrl+C."""
        print("\n[CTRL-C] Graceful shutdown requested...")
        
        # Check if we have a current instance to handle
        if self._current_instance_id:
            # Check if current instance is a qualified instance
            is_qualified = any(instance_id == self._current_instance_id for instance_id, _, _, _ in self.qualified_instances)
            if is_qualified:
                print(f"-> Preserving qualified instance {self._current_instance_id}")
            else:
                print(f"-> Terminating pending instance {self._current_instance_id} ...")
                self.ec2_manager.terminate_instance(self._current_instance_id)
                # Schedule placement group cleanup if exists
                if self._current_placement_group:
                    print(f"-> Scheduling cleanup of placement group {self._current_placement_group} ...")
                    self.pg_manager.schedule_async_cleanup(self._current_instance_id, self._current_placement_group)
                # Handle EIP cleanup based on association status
                if self._current_eip_allocation_id and self._current_eip_name:
                    if self._current_eip_associated:
                        # EIP is associated with instance, schedule async cleanup
                        print(f"-> Scheduling cleanup of EIP {self._current_eip_name} (associated with instance)...")
                        self.eip_manager.schedule_async_eip_cleanup(self._current_instance_id, self._current_eip_allocation_id, self._current_eip_name)
                    else:
                        # EIP was allocated but never associated, release immediately
                        print(f"-> Releasing unassociated EIP {self._current_eip_name} immediately...")
                        self.eip_manager.release_eip(self._current_eip_allocation_id)
        else:
            # No instance but resources may exist - clean up directly
            if self._current_placement_group:
                print(f"-> Deleting unused placement group {self._current_placement_group} ...")
                self.pg_manager.delete_placement_group(self._current_placement_group)
            if self._current_eip_allocation_id and self._current_eip_name:
                print(f"-> Releasing unused EIP {self._current_eip_name} ...")
                self.eip_manager.release_eip(self._current_eip_allocation_id)
        
        # Wait for cleanup threads
        self.pg_manager.wait_for_cleanup_threads()
        self.eip_manager.wait_for_cleanup_threads()
    
    def _show_final_summary(self) -> None:
        """Show final summary after loop ends."""
        if self.qualified_instances:
            print(f"\nFound {len(self.qualified_instances)} qualified instance(s):")
            for i, (instance_id, instance_type, placement_group, eip_allocation_id) in enumerate(self.qualified_instances, 1):
                print(f"  {i}. {instance_id} ({instance_type}) in {placement_group}")
                if self.config.use_eip and eip_allocation_id:
                    eip_public_ip = self.eip_manager.get_eip_public_ip(eip_allocation_id)
                    print(f"     EIP: {eip_public_ip} ({eip_allocation_id})")
                else:
                    # Get current auto-assigned IP
                    auto_ip = self.ec2_manager.get_instance_public_ip(instance_id)
                    print(f"     Auto-assigned IP: {auto_ip}")
            print("\nKeep these instances running for production use.")
            print("Stop protection has been enabled on all qualified instances.")
            if self.config.use_eip:
                print("Both placement groups and EIPs are preserved for qualified instances.")
            else:
                print("Placement groups are preserved for qualified instances.")
                print("Note: Auto-assigned IPs may change if instances are stopped/started.")
        else:
            print("Search stopped without finding any qualified instances.")
        
        # Show cleanup thread status
        pg_active_count = self.pg_manager.get_active_cleanup_count()
        eip_active_count = self.eip_manager.get_active_cleanup_count()
        total_active = pg_active_count + eip_active_count
        
        if total_active > 0:
            print(f"\n{total_active} background cleanup task(s) still running...")
            if pg_active_count > 0:
                print(f"  - {pg_active_count} placement group cleanup task(s)")
            if eip_active_count > 0:
                print(f"  - {eip_active_count} EIP cleanup task(s)")
            print("These will check instance status every 10 seconds for up to 30 minutes.")
            print("Resources will be cleaned up automatically when instances terminate.")
        time.sleep(2)