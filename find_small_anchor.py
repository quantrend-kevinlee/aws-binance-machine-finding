import boto3
import time, datetime, statistics
import os
import csv
import subprocess
import json
import threading
import sys

# Add the current directory to Python path to import binance_latency_test
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from binance_latency_test import DOMAINS

def load_config():
    """Load shared configuration"""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
            # Expand user path for key
            if config['key_path'].startswith('~'):
                config['key_path'] = os.path.expanduser(config['key_path'])
            return config
    except FileNotFoundError:
        print("[ERROR] Configuration file 'config.json' not found")
        print("   Make sure config.json exists in the current directory")
        exit(1)
    except Exception as e:
        print(f"[ERROR] Error loading config: {e}")
        exit(1)

# Load configuration
CONFIG = load_config()

# Extract configuration values
REGION = CONFIG['region']
BEST_AZ = CONFIG['availability_zone']
SUBNET_ID = CONFIG['subnet_id']
SECURITY_GROUP_ID = CONFIG['security_group_id']
KEY_NAME = CONFIG['key_name']
KEY_PATH = CONFIG['key_path']
EIP_ALLOC_ID = CONFIG['eip_allocation_id']
PLACEMENT_GROUP_BASE = CONFIG['placement_group_base']
MEDIAN_THRESHOLD_US = CONFIG['latency_thresholds']['median_us']
BEST_THRESHOLD_US = CONFIG['latency_thresholds']['best_us']
INSTANCE_TYPES = CONFIG['instance_types']
REPORT_DIR = CONFIG['report_dir']

# Create dynamic file paths
CSV_FILE = f"{REPORT_DIR}/latency_log_{datetime.date.today()}.csv"
CHAMPION_STATE_FILE = f"{REPORT_DIR}/champion_state.json"
CHAMPION_LOG_FILE = f"{REPORT_DIR}/champion_log_{datetime.date.today()}.txt"

# Create directory automatically (won't error if exists)
os.makedirs(REPORT_DIR, exist_ok=True)

# Load latency test script from binance_latency_test.py
script_path = os.path.join(os.path.dirname(__file__), "binance_latency_test.py")
with open(script_path, "r") as f:
    LATENCY_TEST_SCRIPT = f.read()

# Simple user-data to install requirements
USER_DATA_SCRIPT = """#!/bin/bash
yum install -y -q bind-utils python3
"""

# Initialize AWS client
ec2 = boto3.client('ec2', region_name=REGION)

# Track background cleanup threads
cleanup_threads = []

def load_champion_state():
    """Load existing champion state from file and validate instances are still running"""
    if os.path.exists(CHAMPION_STATE_FILE):
        try:
            with open(CHAMPION_STATE_FILE, 'r') as f:
                state = json.load(f)
                
                print(f"[LOAD] Loaded champion state (format v{state.get('format_version', '1.0')}):")
                
                champions = state.get('champions', {})
                if not champions:
                    return {"format_version": "2.0", "champions": {}}
                
                # Display all champions
                for domain, info in champions.items():
                    print(f"   {domain}:")
                    print(f"     Instance: {info.get('instance_id', 'N/A')}")
                    print(f"     Median: {info.get('median_latency', 'N/A')}µs, Best: {info.get('best_latency', 'N/A')}µs")
                    print(f"     IP: {info.get('ip', 'N/A')}")
                    print(f"     PG: {info.get('placement_group', 'N/A')}")
                
                # Validate all champion instances
                print(f"\n[CHECK] Validating champion instances...")
                instances_to_check = {}
                for domain, info in champions.items():
                    instance_id = info.get('instance_id')
                    if instance_id and instance_id not in instances_to_check:
                        instances_to_check[instance_id] = [domain]
                    elif instance_id:
                        instances_to_check[instance_id].append(domain)
                
                valid_champions = {}
                for instance_id, domains in instances_to_check.items():
                    try:
                        response = ec2.describe_instances(InstanceIds=[instance_id])
                        if response['Reservations']:
                            instance = response['Reservations'][0]['Instances'][0]
                            instance_state = instance['State']['Name']
                            
                            if instance_state == 'running':
                                print(f"   [OK] Instance {instance_id} is running (champions: {', '.join(domains)})")
                                # Keep this instance's champion entries
                                for domain in domains:
                                    valid_champions[domain] = champions[domain]
                            else:
                                print(f"   [WARN]  Instance {instance_id} is {instance_state} - removing from champions")
                                # Schedule cleanup if needed
                                if instance_state not in ['terminated', 'terminating']:
                                    try:
                                        ec2.terminate_instances(InstanceIds=[instance_id])
                                    except:
                                        pass
                                # Schedule placement group cleanup
                                for domain in domains:
                                    pg = champions[domain].get('placement_group')
                                    if pg:
                                        async_cleanup_placement_group(instance_id, pg)
                                        break  # Only need to clean up PG once
                        else:
                            print(f"   [WARN]  Instance {instance_id} not found - removing from champions")
                    except Exception as e:
                        if 'InvalidInstanceID.NotFound' in str(e):
                            print(f"   [WARN]  Instance {instance_id} not found in AWS - removing from champions")
                        else:
                            print(f"   [WARN]  Error checking {instance_id}: {e} - keeping in champions")
                            # Keep on error to be safe
                            for domain in domains:
                                valid_champions[domain] = champions[domain]
                
                # Update state with only valid champions
                state['champions'] = valid_champions
                
                # Save cleaned state if any champions were removed
                if len(valid_champions) < len(champions):
                    with open(CHAMPION_STATE_FILE, 'w') as f:
                        json.dump(state, f, indent=2)
                    print(f"   [SAVE] Updated champion state file with {len(valid_champions)} valid champions")
                
                return state
                
        except Exception as e:
            print(f"[WARN]  Could not load champion state: {e}")
    
    # Return empty state with current format version
    return {"format_version": "2.0", "champions": {}}

def save_champions_state(current_state, domain_updates):
    """Save champion state for specific domains
    Args:
        current_state: Current state dict with format_version and champions
        domain_updates: Dict of domain -> champion info to update
    """
    try:
        # Start with current state
        state = current_state.copy()
        if 'champions' not in state:
            state['champions'] = {}
        
        # Update specific domains
        for domain, info in domain_updates.items():
            state['champions'][domain] = info
        
        # Ensure format version
        state['format_version'] = "2.0"
        
        # Save to file
        with open(CHAMPION_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        
        print(f"[SAVE] Champion state saved to {CHAMPION_STATE_FILE}")
        print(f"   Updated domains: {', '.join(domain_updates.keys())}")
        
    except Exception as e:
        print(f"[WARN]  Could not save champion state: {e}")

def log_champion_event(domain, instance_id, instance_type, median_latency, best_latency, ip, placement_group, old_champion=None):
    """Log champion events to dedicated champion log file"""
    timestamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    
    with open(CHAMPION_LOG_FILE, "a") as f:
        f.write(f"\n{timestamp}\n")
        f.write(f"  Domain: {domain}\n")
        f.write(f"  New Champion: {instance_id} ({instance_type})\n")
        f.write(f"  Median Latency: {median_latency:.2f}µs\n")
        f.write(f"  Best Latency: {best_latency:.2f}µs\n")
        f.write(f"  Optimal IP: {ip}\n")
        f.write(f"  Placement Group: {placement_group}\n")
        if old_champion:
            f.write(f"  Replaced: {old_champion['instance_id']} (median: {old_champion['median_latency']:.2f}µs)\n")
        f.write("-" * 80 + "\n")

def async_cleanup_placement_group(instance_id, placement_group_name):
    """Background task to delete placement group after instance terminates"""
    def cleanup():
        print(f"[Background] Scheduled cleanup for PG {placement_group_name} (checking every minute for up to 30 minutes)")
        
        # Create a new EC2 client for this thread
        thread_ec2 = boto3.client('ec2', region_name=REGION)
        
        try:
            # Wait for instance to fully terminate
            # Check once per minute for up to 30 minutes
            waiter = thread_ec2.get_waiter('instance_terminated')
            waiter.wait(
                InstanceIds=[instance_id],
                WaiterConfig={
                    'Delay': 60,      # Check every 60 seconds
                    'MaxAttempts': 30  # Up to 30 attempts = 30 minutes
                }
            )
            
            # Small delay to ensure AWS has fully updated its state
            time.sleep(10)
            
            # Now delete the placement group
            thread_ec2.delete_placement_group(GroupName=placement_group_name)
            print(f"[Background] [OK] Successfully deleted placement group {placement_group_name}")
            
        except Exception as e:
            if 'Max attempts exceeded' in str(e):
                print(f"[Background] [WARN] Timeout: Instance {instance_id} still terminating after 30 minutes")
            else:
                print(f"[Background] [WARN] Failed to delete placement group {placement_group_name}: {e}")
        
        # Thread will automatically terminate here when function returns
    
    # Start cleanup in a background thread
    # Not a daemon thread so we can wait for it on Ctrl+C
    thread = threading.Thread(target=cleanup, daemon=False)
    thread.start()
    cleanup_threads.append(thread)
    return thread

# CSV file date tracking
start_date = datetime.date.today()

instance_index = 0  # For rotating instance type selection

print(f"Starting small instance search in {BEST_AZ}...")
anchor_instance_id = None
anchor_instance_type = None

# Multi-domain champion tracking - load existing state
champion_state = load_champion_state()
domain_champions = champion_state.get("champions", {})

# If first execution today, write CSV header
if not os.path.exists(CSV_FILE):
    with open(CSV_FILE, "w", newline="") as f:
        csv.writer(f).writerow(
            ["timestamp", "instance_id", "instance_type", 
             "best_median_us_fapi-mm", "best_best_us_fapi-mm", "best_median_ip_fapi-mm", "best_best_ip_fapi-mm",
             "best_median_us_ws-fapi-mm", "best_best_us_ws-fapi-mm", "best_median_ip_ws-fapi-mm", "best_best_ip_ws-fapi-mm",
             "best_median_us_fstream-mm", "best_best_us_fstream-mm", "best_median_ip_fstream-mm", "best_best_ip_fstream-mm",
             "passed"]
        )

def run_ssh_command(ip, command, timeout=300):
    """Run command via SSH and return output"""
    ssh_cmd = [
        "ssh",
        "-i", KEY_PATH,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        f"ec2-user@{ip}",
        command
    ]
    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", -1
    except Exception as e:
        return "", str(e), -1

def wait_for_ssh(ip, max_attempts=30):
    """Wait for SSH to be available"""
    print(f"Waiting for SSH access to {ip}...")
    for i in range(max_attempts):
        stdout, stderr, code = run_ssh_command(ip, "echo ready", timeout=10)
        if code == 0 and "ready" in stdout:
            print("SSH is ready!")
            return True
        print(f"  Attempt {i+1}/{max_attempts}...")
        time.sleep(5)
    return False

while True:
    try:
        # Daily check: if date changed, reset CSV file
        today = datetime.date.today()
        if today != start_date:
            CSV_FILE = f"{REPORT_DIR}/latency_log_{today}.csv"
            if not os.path.exists(CSV_FILE):
                with open(CSV_FILE, "w", newline="") as f:
                    csv.writer(f).writerow(
                        ["timestamp", "instance_id", "instance_type", 
                         "best_median_us_fapi-mm", "best_best_us_fapi-mm", "best_median_ip_fapi-mm", "best_best_ip_fapi-mm",
                         "best_median_us_ws-fapi-mm", "best_best_us_ws-fapi-mm", "best_median_ip_ws-fapi-mm", "best_best_ip_ws-fapi-mm",
                         "best_median_us_fstream-mm", "best_best_us_fstream-mm", "best_median_ip_fstream-mm", "best_best_ip_fstream-mm",
                         "passed"]
                    )
            # Reset date
            start_date = today

        instance_type = INSTANCE_TYPES[instance_index]
        instance_index = (instance_index + 1) % len(INSTANCE_TYPES)

        # Create placement group with timestamp
        unix_timestamp = int(time.time())
        placement_group_name = f"{PLACEMENT_GROUP_BASE}-{unix_timestamp}"
        
        print(f"\nCreating placement group {placement_group_name}...")
        try:
            ec2.create_placement_group(GroupName=placement_group_name, Strategy='cluster')
            print(f"  [OK] Created placement group")
            time.sleep(2)  # Give AWS a moment to register the PG
        except Exception as e:
            print(f"[ERROR] Failed to create placement group: {e}")
            time.sleep(5)
            continue

        print(f"Launching test instance of type {instance_type} ...")
        # Add Unix timestamp prefix to instance name
        instance_name = f"{unix_timestamp}-DC-Search"
        
        try:
            resp = ec2.run_instances(
                ImageId=("resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64" 
                        if instance_type.startswith("c7") else 
                        "resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"),
                InstanceType=instance_type,
                MinCount=1, MaxCount=1,
                KeyName=KEY_NAME,
                SecurityGroupIds=[SECURITY_GROUP_ID],
                SubnetId=SUBNET_ID,
                Placement={"GroupName": placement_group_name, "AvailabilityZone": BEST_AZ},
                UserData=USER_DATA_SCRIPT,
                TagSpecifications=[{
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": instance_name}]
                }]
            )
        except Exception as e:
            err = str(e)
            print(f"[ERROR] run_instances failed for {instance_type}: {err}")
            
            # Clean up the placement group immediately since no instance was created
            print(f"Deleting unused placement group {placement_group_name}...")
            try:
                ec2.delete_placement_group(GroupName=placement_group_name)
                print(f"  [OK] Deleted placement group")
            except Exception as del_err:
                print(f"  [WARN]  Could not delete placement group: {del_err}")
            
            # If capacity or placement error, try next instance type
            if ("Insufficient capacity" in err) or ("Placement" in err) or ("VcpuLimitExceeded" in err):
                print(" -> Capacity/limit issue, will try next instance type.")
                instance_index = (instance_index + 1) % len(INSTANCE_TYPES)
                time.sleep(2)
                continue
            else:
                # Other errors, wait a few seconds and retry
                time.sleep(5)
                continue

        instance_id = resp['Instances'][0]['InstanceId']
        print(f"Instance {instance_id} launched.")

        # Wait for instance state to be running
        try:
            ec2.get_waiter('instance_running').wait(
                InstanceIds=[instance_id],
                WaiterConfig={"Delay": 5, "MaxAttempts": 30})
        except Exception as e:
            print(f"[WARN] Wait for running failed: {e}")
        
        # Attach EIP to instance
        associated = False
        for attempt in range(3):
            try:
                ec2.associate_address(InstanceId=instance_id,
                                    AllocationId=EIP_ALLOC_ID)
                associated = True
                break
            except ec2.exceptions.ClientError as e:
                print(f"[WARN] Associate EIP failed (attempt {attempt+1}/3): {e}")
                time.sleep(3)
        if not associated:
            print("[ERROR] Could not attach EIP to instance. Terminating instance and continuing...")
            try:
                ec2.terminate_instances(InstanceIds=[instance_id])
            except:
                pass
            # Schedule placement group cleanup
            async_cleanup_placement_group(instance_id, placement_group_name)
            time.sleep(2)
            continue

        # Get the EIP address for SSH
        eip_info = ec2.describe_addresses(AllocationIds=[EIP_ALLOC_ID])
        eip_address = eip_info['Addresses'][0]['PublicIp']
        print(f"EIP address: {eip_address}")

        # Wait for SSH to be ready
        if not wait_for_ssh(eip_address):
            print("[ERROR] SSH not available after timeout. Terminating instance...")
            try:
                ec2.terminate_instances(InstanceIds=[instance_id])
            except:
                pass
            # Schedule placement group cleanup
            async_cleanup_placement_group(instance_id, placement_group_name)
            time.sleep(2)
            continue

        # Create and run the latency test script
        print("Running latency test via SSH...")
        
        # Create the test script on the instance
        script_content = LATENCY_TEST_SCRIPT.replace("'", "'\"'\"'")  # Escape single quotes
        create_script_cmd = f"echo '{script_content}' > /tmp/latency_test.py"
        stdout, stderr, code = run_ssh_command(eip_address, create_script_cmd)
        if code != 0:
            print(f"[ERROR] Failed to create test script: {stderr}")
            try:
                ec2.terminate_instances(InstanceIds=[instance_id])
            except:
                pass
            # Schedule placement group cleanup
            async_cleanup_placement_group(instance_id, placement_group_name)
            time.sleep(2)
            continue

        # Run the test script
        print("Executing latency tests (this may take a few minutes)...")
        stdout, stderr, code = run_ssh_command(eip_address, "python3 /tmp/latency_test.py", timeout=300)
        
        if code != 0:
            print(f"[ERROR] Test script failed: {stderr}")
            try:
                ec2.terminate_instances(InstanceIds=[instance_id])
            except:
                pass
            # Schedule placement group cleanup
            async_cleanup_placement_group(instance_id, placement_group_name)
            time.sleep(2)
            continue

        # Parse JSON results
        try:
            results = json.loads(stdout)
        except json.JSONDecodeError as e:
            print(f"[ERROR] Failed to parse test results: {e}")
            print(f"Raw output: {stdout}")
            try:
                ec2.terminate_instances(InstanceIds=[instance_id])
            except:
                pass
            # Schedule placement group cleanup
            async_cleanup_placement_group(instance_id, placement_group_name)
            time.sleep(2)
            continue

        # Process results - Track best values per domain
        domain_stats = {}
        instance_passed = False
        
        print("\nLatency test results:")
        for hostname, host_data in results.items():
            if "error" in host_data:
                print(f"  {hostname}: {host_data['error']}")
                continue
            
            # Initialize domain tracking
            domain_stats[hostname] = {
                "best_median": float("inf"),
                "best_best": float("inf"),
                "best_median_ip": "",
                "best_best_ip": ""
            }
            
            print(f"  {hostname}:")
            
            for ip, ip_data in host_data["ips"].items():
                median = ip_data["median"]
                best = ip_data["best"]
                
                # Check if this IP meets our criteria
                ip_passed = (median <= MEDIAN_THRESHOLD_US) or (best <= BEST_THRESHOLD_US)
                
                print(f"    IP {ip:<15}  median={median:9.2f} µs  best={best:9.2f} µs  passed={ip_passed}")
                
                # Track best median for this domain
                if median < domain_stats[hostname]["best_median"]:
                    domain_stats[hostname]["best_median"] = median
                    domain_stats[hostname]["best_median_ip"] = ip
                
                # Track best "best" value for this domain
                if best < domain_stats[hostname]["best_best"]:
                    domain_stats[hostname]["best_best"] = best
                    domain_stats[hostname]["best_best_ip"] = ip
                
                # Instance passes if ANY IP meets criteria
                if ip_passed:
                    instance_passed = True

        # Log results
        utc_now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
        print(f"\n[{utc_now}] {instance_id}  {instance_type:<9}")
        
        # Show per-domain best results
        for hostname, stats in domain_stats.items():
            domain_short = hostname.replace(".binance.com", "")
            print(f"  {domain_short}: median={stats['best_median']:.2f}µs ({stats['best_median_ip']}), best={stats['best_best']:.2f}µs ({stats['best_best_ip']})")
        
        print(f"  Passed: {instance_passed}")

        # Write to CSV
        with open(CSV_FILE, "a", newline="") as f:
            # Prepare per-domain data for CSV
            row_data = [utc_now, instance_id, instance_type]
            
            for domain in DOMAINS:
                if domain in domain_stats:
                    stats = domain_stats[domain]
                    row_data.extend([
                        f"{stats['best_median']:.2f}",
                        f"{stats['best_best']:.2f}",
                        stats['best_median_ip'],
                        stats['best_best_ip']
                    ])
                else:
                    # Domain had errors, fill with empty values
                    row_data.extend(["", "", "", ""])
            
            row_data.append(instance_passed)
            csv.writer(f).writerow(row_data)
        
        # Write detailed results to text file
        txt_file = CSV_FILE.replace(".csv", ".txt")
        with open(txt_file, "a") as f:
            # Write summary line
            f.write(f"[{utc_now}] {instance_id}  {instance_type}\n")
            
            # Write per-domain best results
            for hostname, stats in domain_stats.items():
                domain_short = hostname.replace(".binance.com", "")
                f.write(f"  {domain_short}: median={stats['best_median']:.2f}µs ({stats['best_median_ip']}), best={stats['best_best']:.2f}µs ({stats['best_best_ip']})\n")
            
            f.write(f"  Passed: {instance_passed}\n")
            
            # Write detailed test results
            f.write("\nLatency test results:\n")
            for hostname, host_data in results.items():
                if "error" in host_data:
                    f.write(f"  {hostname}: {host_data['error']}\n")
                    continue
                
                f.write(f"  {hostname}:\n")
                
                for ip, ip_data in host_data["ips"].items():
                    median = ip_data["median"]
                    best = ip_data["best"]
                    ip_passed = (median <= MEDIAN_THRESHOLD_US) or (best <= BEST_THRESHOLD_US)
                    f.write(f"    IP {ip:<15}  median={median:9.2f} µs  best={best:9.2f} µs  passed={ip_passed}\n")
            
            # Add separator between instances
            f.write("\n" + "="*80 + "\n\n")
            
        # Check for champions across all domains (using median latency)
        new_champions = {}  # Track which domains got new champions
        replaced_instances = set()  # Track instances that were replaced
        
        for domain in DOMAINS:
            if domain not in domain_stats or domain_stats[domain]["best_median"] >= float("inf"):
                print(f"\n[WARN]  No valid data for {domain} on instance {instance_id}")
                continue
                
            current_median = domain_stats[domain]["best_median"]
            current_best = domain_stats[domain]["best_best"]
            current_ip = domain_stats[domain]["best_median_ip"]
            
            # Get current champion for this domain
            current_champion = domain_champions.get(domain, {})
            champion_median = current_champion.get("median_latency", float("inf"))
            
            # Check if this instance beats the current champion
            if current_median < champion_median:
                print(f"\n[CHAMPION] New {domain} champion! Median: {current_median:.2f}µs (best: {current_best:.2f}µs) on {current_ip}")
                
                # Prepare old champion info
                old_champion = None
                if current_champion and current_champion.get("instance_id") != instance_id:
                    old_champion = {
                        "instance_id": current_champion["instance_id"],
                        "median_latency": champion_median
                    }
                    replaced_instances.add(current_champion["instance_id"])
                    print(f"   Replacing: {old_champion['instance_id']} (median: {champion_median:.2f}µs)")
                
                # Record new champion for this domain
                new_champions[domain] = {
                    "instance_id": instance_id,
                    "placement_group": placement_group_name,
                    "median_latency": current_median,
                    "best_latency": current_best,
                    "ip": current_ip,
                    "instance_type": instance_type,
                    "timestamp": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
                }
                
                # Log champion event
                log_champion_event(domain, instance_id, instance_type, current_median, current_best, 
                                 current_ip, placement_group_name, old_champion)
        
        # Update champion state if we have new champions
        if new_champions:
            save_champions_state(champion_state, new_champions)
            # Update our local state
            domain_champions.update(new_champions)
            
        # Determine which replaced instances can be terminated
        # Helper function to check if an instance is still a champion for any domain
        def get_instance_domains(instance_id):
            return [d for d, info in domain_champions.items() if info.get("instance_id") == instance_id]
        
        # Terminate replaced instances that are no longer champions for any domain
        for old_instance_id in replaced_instances:
            remaining_domains = get_instance_domains(old_instance_id)
            if not remaining_domains:
                print(f"\n[DELETE]  Terminating {old_instance_id} - no longer champion for any domain")
                try:
                    ec2.terminate_instances(InstanceIds=[old_instance_id])
                    # Find and schedule cleanup of its placement group
                    for info in domain_champions.values():
                        if info.get("instance_id") == old_instance_id:
                            pg = info.get("placement_group")
                            if pg:
                                async_cleanup_placement_group(old_instance_id, pg)
                                break
                except Exception as e:
                    print(f"   [WARN]  Could not terminate old champion: {e}")
            else:
                print(f"\n[PROTECTED]  Keeping {old_instance_id} - still champion for: {', '.join(remaining_domains)}")
        
        # Check if current instance is a champion for any domain
        champion_instance = any(info.get("instance_id") == instance_id for info in domain_champions.values())

        if instance_passed:
            # Found anchor
            anchor_instance_id = instance_id
            anchor_instance_type = instance_type
            print(f"*** Found anchor instance {instance_id} (type {instance_type}) meeting latency criteria! ***")
            
            # Show per-domain results
            for hostname, stats in domain_stats.items():
                domain_short = hostname.replace(".binance.com", "")
                print(f"{domain_short}: median={stats['best_median']:.2f}µs ({stats['best_median_ip']}), best={stats['best_best']:.2f}µs ({stats['best_best_ip']})")
            
            # Write success report
            success_report = f"\nSuccessfully found anchor small instance!\n"
            success_report += f"- Instance ID: {anchor_instance_id}\n"
            success_report += f"- Instance Type: {anchor_instance_type}\n"
            success_report += f"- Placement Group: {placement_group_name} (AZ {BEST_AZ})\n"
            success_report += f"- Per-domain results:\n"
            for hostname, stats in domain_stats.items():
                domain_short = hostname.replace(".binance.com", "")
                success_report += f"  - {domain_short}: median={stats['best_median']:.2f}µs ({stats['best_median_ip']}), best={stats['best_best']:.2f}µs ({stats['best_best_ip']})\n"
            print(success_report)
            break
        else:
            # Did not meet target - check if it's the champion before terminating
            if champion_instance:
                # Champion is protected - EIP will be automatically moved to next test instance
                pass
            else:
                print(f"Instance {instance_id} did not meet latency target. Terminating and continuing...")
                try:
                    ec2.terminate_instances(InstanceIds=[instance_id])
                    print(f"  [OK] Instance termination initiated")
                except Exception as e:
                    print(f"[ERROR] Terminating {instance_id} failed: {e}")
                    time.sleep(5)
                    continue
                    
                # EIP will be automatically moved to next instance when we associate it
                
                # Schedule placement group deletion in background
                print(f"Scheduling placement group {placement_group_name} for deletion...")
                async_cleanup_placement_group(instance_id, placement_group_name)
            
            # Brief pause before next iteration (but don't wait for termination)
            time.sleep(2)
                
    except KeyboardInterrupt:
        print("\n[CTRL-C] Graceful shutdown requested...")
        
        # Helper to check if instance is champion for any domain
        def is_champion_instance(inst_id):
            return any(info.get("instance_id") == inst_id for info in domain_champions.values())
        
        # If current instance is running and not anchor or champion
        if 'instance_id' in locals() and instance_id:
            if instance_id == anchor_instance_id:
                print(f"-> Preserving anchor instance {instance_id} (EIP will remain associated)")
            elif is_champion_instance(instance_id):
                champion_domains = [d for d, info in domain_champions.items() if info.get("instance_id") == instance_id]
                print(f"-> Preserving champion {instance_id} for: {', '.join(champion_domains)}")
            else:
                print(f"-> Terminating pending instance {instance_id} ...")
                try:
                    ec2.terminate_instances(InstanceIds=[instance_id])
                except Exception as e:
                    print(f"  Terminate failed: {e}")
                # Schedule placement group cleanup if exists
                if 'placement_group_name' in locals() and placement_group_name:
                    print(f"-> Scheduling cleanup of placement group {placement_group_name} ...")
                    async_cleanup_placement_group(instance_id, placement_group_name)
        
        # Wait for all cleanup threads to complete
        active_threads = [t for t in cleanup_threads if t.is_alive()]
        if active_threads:
            print(f"\n[WAIT] Waiting for {len(active_threads)} background cleanup task(s) to complete...")
            print("   This ensures all instances are terminated and placement groups are deleted.")
            print("   (Checking every minute, up to 30 minutes per task)")
            
            start_wait = time.time()
            last_count = len(active_threads)
            
            while active_threads:
                # Show progress every 30 seconds
                time.sleep(30)
                active_threads = [t for t in cleanup_threads if t.is_alive()]
                
                if len(active_threads) < last_count:
                    print(f"   [OK] {last_count - len(active_threads)} task(s) completed")
                    last_count = len(active_threads)
                
                if active_threads:
                    elapsed = int(time.time() - start_wait)
                    mins = elapsed // 60
                    secs = elapsed % 60
                    print(f"   [WAIT] {len(active_threads)} task(s) still running... (elapsed: {mins}m {secs}s)")
            
            print("   [OK] All cleanup tasks completed!")
        
        break   # Exit while True

# Loop ended (anchor found or manually stopped)
if anchor_instance_id:
    print(f"Anchor instance is {anchor_instance_id} ({anchor_instance_type}). Keep it running for stage 3.")
else:
    print("Search stopped without finding an anchor instance.")

# Show all domain champions
if domain_champions:
    print(f"\n[CHAMPION] Current Domain Champions:")
    
    # Group champions by instance to show multi-domain champions
    instance_domains = {}
    for domain, info in domain_champions.items():
        instance_id = info.get("instance_id")
        if instance_id:
            if instance_id not in instance_domains:
                instance_domains[instance_id] = {
                    "domains": [],
                    "info": info
                }
            instance_domains[instance_id]["domains"].append(domain)
    
    # Display champions grouped by instance
    for instance_id, data in instance_domains.items():
        domains = data["domains"]
        info = data["info"]
        
        print(f"\n   Instance: {instance_id} ({info.get('instance_type', 'N/A')})")
        print(f"   Placement Group: {info.get('placement_group', 'N/A')}")
        print(f"   Champions for: {', '.join(domains)}")
        print(f"   Status: [PROTECTED]  PROTECTED - Will persist after script termination")
        
        # Show latency details for each domain this instance champions
        for domain in domains:
            domain_info = domain_champions[domain]
            domain_short = domain.replace(".binance.com", "")
            print(f"     {domain_short}: median={domain_info.get('median_latency', 'N/A'):.2f}µs, best={domain_info.get('best_latency', 'N/A'):.2f}µs ({domain_info.get('ip', 'N/A')})")
    
    print(f"\n   [INFO] Champion Access Instructions:")
    print(f"   1. To SSH to any champion: aws ec2 associate-address --instance-id <INSTANCE_ID> --allocation-id {EIP_ALLOC_ID}")
    print(f"   2. Then SSH to EIP address with key: {KEY_PATH}")
    print(f"   3. For production, use optimal IPs for each service:")
    
    # Show optimal IPs for production use
    for domain, info in domain_champions.items():
        domain_short = domain.replace(".binance.com", "")
        print(f"      {domain_short}: {info.get('ip', 'N/A')}")
    
    print(f"\n   [SAVE] Champion state persisted in: {CHAMPION_STATE_FILE}")
    print(f"   [LOG] Champion log available at: {CHAMPION_LOG_FILE}")
else:
    print(f"\n[WARN]  No champions found during this session.")

# Give background threads a moment to start cleanup before exiting
if cleanup_threads:
    active_threads = sum(1 for t in cleanup_threads if t.is_alive())
    if active_threads > 0:
        print(f"\n{active_threads} background cleanup task(s) still running...")
        print("These will check instance status every minute for up to 30 minutes.")
        print("Placement groups will be deleted automatically when instances terminate.")
    time.sleep(2)