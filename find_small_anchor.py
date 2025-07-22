import boto3
import time, datetime, statistics
import os
import csv
import subprocess
import json
import threading

# ======== 使用前需要配置的變數 ========
REGION = "ap-northeast-1"
BEST_AZ = "ap-northeast-1a"             # 幣安所在 AZ
SUBNET_ID = "subnet-07954f36129e8beb1"           # 該AZ下的子網ID
SECURITY_GROUP_ID = "sg-080dea8b90091be0b"       # 安全組ID (與前階段相同)
KEY_NAME = "dc-machine"             # SSH金鑰名稱
KEY_PATH = os.path.expanduser("~/.ssh/dc-machine")  # SSH私鑰路徑
EIP_ALLOC_ID = "eipalloc-05500f18fa63990b6"  # 彈性IP Allocation ID
PLACEMENT_GROUP_BASE = "dc-machine-cpg" # Placement Group 基礎名稱
# Latency thresholds
MEDIAN_THRESHOLD_US = 122
BEST_THRESHOLD_US = 102
# 定義小型實例類型循環列表
INSTANCE_TYPES = ["c8g.medium", "c8g.large", "c8g.xlarge", "c8g.2xlarge", "c8g.4xlarge"] # , "c8g.metal-24xl", "c7i.24xlarge", "c7i.metal-24xl"
# 報告輸出資料夾
REPORT_DIR = "./reports"
CSV_FILE = f"{REPORT_DIR}/latency_log_{datetime.date.today()}.csv"
CHAMPION_STATE_FILE = f"{REPORT_DIR}/champion_state.json"
CHAMPION_LOG_FILE = f"{REPORT_DIR}/champion_log_{datetime.date.today()}.txt"

# 自動建立資料夾（若已存在不會報錯）
os.makedirs(REPORT_DIR, exist_ok=True)

# Load latency test script from binance_latency_test.py
script_path = os.path.join(os.path.dirname(__file__), "binance_latency_test.py")
with open(script_path, "r") as f:
    LATENCY_TEST_SCRIPT = f.read()

# Simple user-data to install requirements
USER_DATA_SCRIPT = """#!/bin/bash
yum install -y -q bind-utils python3
"""

# 初始化 AWS 客戶端
ec2 = boto3.client('ec2', region_name=REGION)

# Track background cleanup threads
cleanup_threads = []

def load_champion_state():
    """Load existing champion state from file and validate instance is still running"""
    if os.path.exists(CHAMPION_STATE_FILE):
        try:
            with open(CHAMPION_STATE_FILE, 'r') as f:
                state = json.load(f)
                print(f"📖 Loaded existing champion state:")
                print(f"   Instance: {state.get('instance_id', 'N/A')}")
                print(f"   Latency: {state.get('latency', 'N/A')}µs ({state.get('ip', 'N/A')})")
                print(f"   Placement Group: {state.get('placement_group', 'N/A')}")
                print(f"   Established: {state.get('timestamp', 'N/A')}")
                
                # Validate champion instance is still running
                instance_id = state.get('instance_id')
                placement_group = state.get('placement_group')
                
                if instance_id and placement_group:
                    print(f"🔍 Validating champion instance status...")
                    
                    try:
                        # Check instance status
                        response = ec2.describe_instances(InstanceIds=[instance_id])
                        
                        if response['Reservations']:
                            instance = response['Reservations'][0]['Instances'][0]
                            instance_state = instance['State']['Name']
                            
                            if instance_state == 'running':
                                print(f"   ✓ Champion instance is running and valid")
                                return state
                            else:
                                print(f"   ⚠️  Champion instance is {instance_state} - cleaning up...")
                                
                                # Terminate instance if it's not terminated already
                                if instance_state not in ['terminated', 'terminating']:
                                    try:
                                        ec2.terminate_instances(InstanceIds=[instance_id])
                                        print(f"   ✓ Terminated invalid champion instance")
                                    except Exception as term_e:
                                        print(f"   ⚠️  Could not terminate champion instance: {term_e}")
                                
                                # Schedule placement group cleanup
                                print(f"   🧹 Scheduling placement group cleanup...")
                                async_cleanup_placement_group(instance_id, placement_group)
                        else:
                            print(f"   ⚠️  Champion instance not found - cleaning up...")
                            
                        # Clear invalid champion state
                        try:
                            os.remove(CHAMPION_STATE_FILE)
                            print(f"   🗑️  Cleared invalid champion state file")
                        except Exception as rm_e:
                            print(f"   ⚠️  Could not remove champion state file: {rm_e}")
                        
                        return {}
                        
                    except Exception as e:
                        if 'InvalidInstanceID.NotFound' in str(e):
                            print(f"   ⚠️  Champion instance not found in AWS - clearing state")
                            try:
                                os.remove(CHAMPION_STATE_FILE)
                                print(f"   🗑️  Cleared invalid champion state file")
                            except:
                                pass
                            return {}
                        else:
                            print(f"   ⚠️  Error validating champion instance: {e}")
                            print(f"   📋 Keeping existing state - manual verification may be needed")
                            return state
                else:
                    print(f"   ⚠️  Champion state missing instance_id or placement_group")
                    return {}
                    
        except Exception as e:
            print(f"⚠️  Could not load champion state: {e}")
    return {}

def save_champion_state(instance_id, placement_group, latency, ip, instance_type):
    """Save champion state to file"""
    state = {
        "instance_id": instance_id,
        "placement_group": placement_group, 
        "latency": latency,
        "ip": ip,
        "instance_type": instance_type,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    }
    try:
        with open(CHAMPION_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        print(f"💾 Champion state saved to {CHAMPION_STATE_FILE}")
    except Exception as e:
        print(f"⚠️  Could not save champion state: {e}")

def log_champion_event(instance_id, instance_type, latency, ip, placement_group, old_champion=None):
    """Log champion events to dedicated champion log file"""
    timestamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    
    with open(CHAMPION_LOG_FILE, "a") as f:
        f.write(f"\n{timestamp}\n")
        f.write(f"  New Champion: {instance_id} ({instance_type})\n")
        f.write(f"  fstream-mm Best Latency: {latency:.2f}µs\n")
        f.write(f"  fstream-mm Optimal IP: {ip}\n")
        f.write(f"  Placement Group: {placement_group}\n")
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
            print(f"[Background] ✓ Successfully deleted placement group {placement_group_name}")
            
        except Exception as e:
            if 'Max attempts exceeded' in str(e):
                print(f"[Background] ⚠️ Timeout: Instance {instance_id} still terminating after 30 minutes")
            else:
                print(f"[Background] ⚠️ Failed to delete placement group {placement_group_name}: {e}")
        
        # Thread will automatically terminate here when function returns
    
    # Start cleanup in a background thread
    # Not a daemon thread so we can wait for it on Ctrl+C
    thread = threading.Thread(target=cleanup, daemon=False)
    thread.start()
    cleanup_threads.append(thread)
    return thread

# 統計數據初始化
start_date = datetime.date.today()
daily_counts = 0
daily_medians = []     # 收集當天每台實例的「全域中位數」延遲
daily_best_latencies = []  # 收集當天每台實例的最低單次延遲
daily_types = {t: 0 for t in INSTANCE_TYPES}  # 各類型嘗試計數

instance_index = 0  # 用於輪流選取實例類型

print(f"Starting small instance search in {BEST_AZ}...")
anchor_instance_id = None
anchor_instance_type = None

# fstream-mm champion tracking - load existing state
champion_state = load_champion_state()
best_fstream_instance_id = champion_state.get("instance_id")
best_fstream_placement_group = champion_state.get("placement_group")
best_fstream_latency = champion_state.get("latency", float("inf"))
best_fstream_ip = champion_state.get("ip")
best_fstream_instance_type = champion_state.get("instance_type")

# 若第一次執行今天的檔案 → 寫入 header
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
        # 每日檢查：如跨天則輸出昨天報告並重置計數
        today = datetime.date.today()
        if today != start_date:
            # 輸出昨天的報告
            report_date = start_date
            report_filename = f"{REPORT_DIR}/report-{report_date}.md"
            try:
                with open(report_filename, "w") as rpt:
                    rpt.write(f"# Search Report {report_date}\n\n")
                    rpt.write(f"## Small Instance Search (Placement Group base: '{PLACEMENT_GROUP_BASE}')\n")
                    rpt.write(f"- **Instances tested**: {daily_counts}\n")
                    for t, count in daily_types.items():
                        rpt.write(f"  - {t}: {count} instances\n")
                    if daily_medians:
                        fastest = min(daily_medians)
                        fastest_index = daily_medians.index(fastest)
                        fastest_best = daily_best_latencies[fastest_index]
                        rpt.write(f"- **Fastest instance median latency**: {fastest:.2f} µs (best single handshake {fastest_best:.2f} µs)\n")
                        median_of_medians = statistics.median(daily_medians)
                        rpt.write(f"- **Median of all medians**: {median_of_medians:.2f} µs\n")
                        # 分佈: 加上樣本不足保護
                        if len(daily_medians) >= 4:
                            d0, d1, d2 = statistics.quantiles(daily_medians, n=4)
                            rpt.write(f"- **Latency distribution**: {d0:.1f}/{d1:.1f}/{d2:.1f} µs\n")
                        else:
                            rpt.write("- **Latency distribution**: N/A (< 4 samples)\n")

                    else:
                        rpt.write("- No instances tested today.\n")
                print(f"[REPORT] Daily report generated: {report_filename}")
            except Exception as e:
                print(f"[ERROR] 無法寫入日報表: {e}")

            # 產生新 CSV
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
            # 重置統計
            start_date = today
            daily_counts = 0
            daily_medians = []
            daily_best_latencies = []
            daily_types = {t: 0 for t in INSTANCE_TYPES}

        instance_type = INSTANCE_TYPES[instance_index]
        instance_index = (instance_index + 1) % len(INSTANCE_TYPES)
        daily_types[instance_type] += 1

        # Create placement group with timestamp
        unix_timestamp = int(time.time())
        placement_group_name = f"{PLACEMENT_GROUP_BASE}-{unix_timestamp}"
        
        print(f"\nCreating placement group {placement_group_name}...")
        try:
            ec2.create_placement_group(GroupName=placement_group_name, Strategy='cluster')
            print(f"  ✓ Created placement group")
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
                print(f"  ✓ Deleted placement group")
            except Exception as del_err:
                print(f"  ⚠️  Could not delete placement group: {del_err}")
            
            # 若是容量或佈局錯誤，換下一個實例類型重試
            if ("Insufficient capacity" in err) or ("Placement" in err) or ("VcpuLimitExceeded" in err):
                print(" -> Capacity/limit issue, will try next instance type.")
                instance_index = (instance_index + 1) % len(INSTANCE_TYPES)
                time.sleep(2)
                continue
            else:
                # 其他錯誤，等待幾秒再重試
                time.sleep(5)
                continue

        instance_id = resp['Instances'][0]['InstanceId']
        print(f"Instance {instance_id} launched.")

        # 等待實例狀態為 running
        try:
            ec2.get_waiter('instance_running').wait(
                InstanceIds=[instance_id],
                WaiterConfig={"Delay": 5, "MaxAttempts": 30})
        except Exception as e:
            print(f"[WARN] Wait for running failed: {e}")
        
        # 附加 EIP 到該實例
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
        
        # First, create the test script on the instance
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
            # Prepare per-domain data for CSV (in order: fapi-mm, ws-fapi-mm, fstream-mm)
            domains = ["fapi-mm.binance.com", "ws-fapi-mm.binance.com", "fstream-mm.binance.com"]
            row_data = [utc_now, instance_id, instance_type]
            
            for domain in domains:
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
            
        # Save daily statistics (use overall best values across all domains)
        overall_best_median = min((stats["best_median"] for stats in domain_stats.values() if stats["best_median"] < float("inf")), default=float("inf"))
        overall_best_latency = min((stats["best_best"] for stats in domain_stats.values() if stats["best_best"] < float("inf")), default=float("inf"))
        
        if overall_best_median < float("inf"):
            daily_medians.append(overall_best_median)
        if overall_best_latency < float("inf"):
            daily_best_latencies.append(overall_best_latency)
        daily_counts += 1

        # Check for fstream-mm champion
        fstream_domain = "fstream-mm.binance.com"
        current_fstream_latency = float("inf")
        current_fstream_ip = None
        
        if fstream_domain in domain_stats and domain_stats[fstream_domain]["best_best"] < float("inf"):
            current_fstream_latency = domain_stats[fstream_domain]["best_best"]
            current_fstream_ip = domain_stats[fstream_domain]["best_best_ip"]
            
            # Check if this instance is a new fstream-mm champion
            if current_fstream_latency < best_fstream_latency:
                print(f"\n🏆 New fstream-mm champion! {current_fstream_latency:.2f}µs ({current_fstream_ip})")
                
                # Prepare old champion info for logging
                old_champion = None
                if best_fstream_instance_id and best_fstream_instance_id != instance_id:
                    old_champion = {
                        "instance_id": best_fstream_instance_id,
                        "latency": best_fstream_latency
                    }
                    try:
                        ec2.terminate_instances(InstanceIds=[best_fstream_instance_id])
                        # Schedule cleanup of old champion's placement group
                        async_cleanup_placement_group(best_fstream_instance_id, best_fstream_placement_group)
                    except Exception as e:
                        print(f"   ⚠️  Could not terminate old champion: {e}")
                
                # Update champion tracking
                best_fstream_instance_id = instance_id
                best_fstream_placement_group = placement_group_name
                best_fstream_latency = current_fstream_latency
                best_fstream_ip = current_fstream_ip
                best_fstream_instance_type = instance_type
                
                # Save champion state to persist after script termination
                save_champion_state(instance_id, placement_group_name, current_fstream_latency, current_fstream_ip, instance_type)
                
                # Log champion event
                log_champion_event(instance_id, instance_type, current_fstream_latency, current_fstream_ip, placement_group_name, old_champion)
                
                # Don't terminate this instance - it's the new champion!
                champion_instance = True
            else:
                champion_instance = False
        else:
            print(f"\n⚠️  No valid fstream-mm data for instance {instance_id}")
            champion_instance = False

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
            success_report = f"\n成功找到錨點小型實例！\n"
            success_report += f"- Instance ID: {anchor_instance_id}\n"
            success_report += f"- Instance Type: {anchor_instance_type}\n"
            success_report += f"- Placement Group: {placement_group_name} (AZ {BEST_AZ})\n"
            success_report += f"- Per-domain results:\n"
            for hostname, stats in domain_stats.items():
                domain_short = hostname.replace(".binance.com", "")
                success_report += f"  - {domain_short}: median={stats['best_median']:.2f}µs ({stats['best_median_ip']}), best={stats['best_best']:.2f}µs ({stats['best_best_ip']})\n"
            print(success_report)
            
            # Append to today's report
            report_date = start_date
            report_filename = f"{REPORT_DIR}/report-{report_date}.md"
            with open(report_filename, "a") as rpt:
                rpt.write("\n**Anchor instance found, stopping small instance search.**\n")
                rpt.write(success_report)
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
                    print(f"  ✓ Instance termination initiated")
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
        print("\n[CTRL‑C] Graceful shutdown requested…")
        # If current instance is running and not anchor or champion
        if 'instance_id' in locals() and instance_id and instance_id != anchor_instance_id and instance_id != best_fstream_instance_id:
            print(f"→ Terminating pending instance {instance_id} …")
            try:
                ec2.terminate_instances(InstanceIds=[instance_id])
            except Exception as e:
                print(f"  Terminate failed: {e}")
            # EIP will remain with champion or be available for next instance
            # Schedule placement group cleanup if exists
            if 'placement_group_name' in locals() and placement_group_name:
                print(f"→ Scheduling cleanup of placement group {placement_group_name} …")
                async_cleanup_placement_group(instance_id, placement_group_name)
        elif 'instance_id' in locals() and instance_id == best_fstream_instance_id:
            print(f"→ Preserving fstream-mm champion {instance_id} (EIP will remain associated)")
        elif 'instance_id' in locals() and instance_id == anchor_instance_id:
            print(f"→ Preserving anchor instance {instance_id} (EIP will remain associated)")
        
        # Wait for all cleanup threads to complete
        active_threads = [t for t in cleanup_threads if t.is_alive()]
        if active_threads:
            print(f"\n⏳ Waiting for {len(active_threads)} background cleanup task(s) to complete...")
            print("   This ensures all instances are terminated and placement groups are deleted.")
            print("   (Checking every minute, up to 30 minutes per task)")
            
            start_wait = time.time()
            last_count = len(active_threads)
            
            while active_threads:
                # Show progress every 30 seconds
                time.sleep(30)
                active_threads = [t for t in cleanup_threads if t.is_alive()]
                
                if len(active_threads) < last_count:
                    print(f"   ✓ {last_count - len(active_threads)} task(s) completed")
                    last_count = len(active_threads)
                
                if active_threads:
                    elapsed = int(time.time() - start_wait)
                    mins = elapsed // 60
                    secs = elapsed % 60
                    print(f"   ⏳ {len(active_threads)} task(s) still running... (elapsed: {mins}m {secs}s)")
            
            print("   ✓ All cleanup tasks completed!")
        
        break   # Exit while True

# Loop ended (anchor found or manually stopped)
if anchor_instance_id:
    print(f"Anchor instance is {anchor_instance_id} ({anchor_instance_type}). Keep it running for stage 3.")
else:
    print("Search stopped without finding an anchor instance.")

# Show fstream-mm champion status
if best_fstream_instance_id:
    print(f"\n🏆 Current fstream-mm champion: {best_fstream_instance_id} ({best_fstream_instance_type})")
    print(f"   Best latency: {best_fstream_latency:.2f}µs ({best_fstream_ip})")
    print(f"   Placement Group: {best_fstream_placement_group}")
    print(f"   Status: 🛡️  PROTECTED - Will persist after script termination")
    print(f"")
    print(f"   📋 Champion Access Instructions:")
    print(f"   1. To SSH to champion: aws ec2 associate-address --instance-id {best_fstream_instance_id} --allocation-id {EIP_ALLOC_ID}")
    print(f"   2. Then SSH to EIP address with key: ~/.ssh/dc-machine")
    print(f"   3. For production: Use IP {best_fstream_ip} for fstream-mm.binance.com connections")
    print(f"")
    print(f"   💾 Champion state persisted in: {CHAMPION_STATE_FILE}")
    print(f"   📜 Champion log available at: {CHAMPION_LOG_FILE}")
else:
    print(f"\n⚠️  No fstream-mm champion found during this session.")

# Give background threads a moment to start cleanup before exiting
if cleanup_threads:
    active_threads = sum(1 for t in cleanup_threads if t.is_alive())
    if active_threads > 0:
        print(f"\n{active_threads} background cleanup task(s) still running...")
        print("These will check instance status every minute for up to 30 minutes.")
        print("Placement groups will be deleted automatically when instances terminate.")
    time.sleep(2)