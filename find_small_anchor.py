import boto3
import time, datetime, statistics
import os
import csv

# ======== 使用前需要配置的變數 ========
REGION = "ap-northeast-1"
BEST_AZ = "ap-northeast-1a"             # 幣安所在 AZ
SUBNET_ID = "subnet-07954f36129e8beb1"           # 該AZ下的子網ID
SECURITY_GROUP_ID = "sg-080dea8b90091be0b"       # 安全組ID (與前階段相同)
KEY_NAME = "dc-machine"             # SSH金鑰名稱
EIP_ALLOC_ID = "eipalloc-05500f18fa63990b6"  # 彈性IP Allocation ID
PLACEMENT_GROUP_NAME = "dc-machine-cpg" # Placement Group 名稱 (若不存在將自動創建)
# 定義小型實例類型循環列表 (交替使用不同家族)
INSTANCE_TYPES = ["c7i.large", "c8g.large"]
# 報告輸出資料夾
REPORT_DIR = "./reports"
CSV_FILE = f"{REPORT_DIR}/latency_log_{datetime.date.today()}.csv"

# 自動建立資料夾（若已存在不會報錯）
os.makedirs(REPORT_DIR, exist_ok=True)

USER_DATA_SCRIPT = """#!/bin/bash
# 安裝測試所需的工具
yum install -y -q bind-utils
# 將 Python 延遲測試腳本寫入並執行
cat > /tmp/latency_test.py << 'PYCODE'
import socket, subprocess, statistics, time, sys
ATTEMPTS = 10000
TIMEOUT = 1
MEDIAN_THRESHOLD_US = 122
BEST_THRESHOLD_US = 102
HOSTNAMES = ["fapi-mm.binance.com", "ws-fapi-mm.binance.com", "fstream-mm.binance.com"]
def resolve_ips(hostname):
    ips = []
    result = subprocess.run(["host", hostname], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        parts = line.split()
        if "address" in parts:
            ips.append(parts[-1])
    return ips
def test_latency(ip):
    latencies = []
    for _ in range(ATTEMPTS):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(TIMEOUT)
            t0 = time.perf_counter_ns()
            s.connect((ip, 443))
            t1 = time.perf_counter_ns()
            s.close()
            latencies.append((t1 - t0) / 1000)  # ns轉微秒
        except socket.error:
            continue
    if not latencies:
        return float("inf"), float("inf"), False
    median_us = statistics.median(latencies)
    best_us = min(latencies)
    passed = (median_us <= MEDIAN_THRESHOLD_US) or (best_us <= BEST_THRESHOLD_US)
    return median_us, best_us, passed
def main():
    overall_passed = True
    for hostname in HOSTNAMES:
        ips = resolve_ips(hostname)
        print(f"{hostname}:")
        if not ips:
            print(f"  [WARN] 無法解析 {hostname}")
            overall_passed = False
            continue
        for ip in ips:
            median_us, best_us, passed = test_latency(ip)
            print(f"  IP {ip:<15}  median={median_us:9.2f} µs  best={best_us:9.2f} µs  passed={passed}")
            # 這裡原始passed計算是逐IP &，我們稍後在外部解析，不在腳本內決定overall_passed
    # main函數不判斷passed，退出碼固定0，實際passed與否由外部解析
    return overall_passed
if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
PYCODE
python3 /tmp/latency_test.py | tee /dev/console
"""  # END OF USER_DATA_SCRIPT

# 初始化 AWS 客戶端
ec2 = boto3.client('ec2', region_name=REGION)

# 確保Placement Group存在 (不存在則建立)
try:
    ec2.describe_placement_groups(GroupNames=[PLACEMENT_GROUP_NAME])
except:
    print(f"Creating placement group {PLACEMENT_GROUP_NAME} in {BEST_AZ}...")
    try:
        ec2.create_placement_group(GroupName=PLACEMENT_GROUP_NAME, Strategy='cluster')
    except Exception as e:
        print(f"[ERROR] 無法建立 Placement Group: {e}")
        exit(1)

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

# 若第一次執行今天的檔案 → 寫入 header
if not os.path.exists(CSV_FILE):
    with open(CSV_FILE, "w", newline="") as f:
        csv.writer(f).writerow(
            ["timestamp", "instance_id", "instance_type", "median_us", "best_us", "passed"]
        )

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
                    rpt.write(f"## Small Instance Search (Placement Group '{PLACEMENT_GROUP_NAME}')\n")
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
                        ["timestamp","instance_id","instance_type","median_us","best_us","passed"]
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

        print(f"\nLaunching test instance of type {instance_type} ...")
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
                Placement={"GroupName": PLACEMENT_GROUP_NAME, "AvailabilityZone": BEST_AZ},
                UserData=USER_DATA_SCRIPT,
                TagSpecifications=[{
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": f"SmallSearch-{instance_type}"}]
                }]
            )
        except Exception as e:
            err = str(e)
            print(f"[ERROR] run_instances failed for {instance_type}: {err}")
            # 若是容量或佈局錯誤，換下一個實例類型重試
            if ("Insufficient capacity" in err) or ("Placement" in err):
                print(" -> Insufficient capacity or placement issue, will try next instance type.")
                instance_index = (instance_index + 1) % len(INSTANCE_TYPES)
                time.sleep(5)
                continue
            else:
                # 其他錯誤，等待幾秒再重試
                time.sleep(5)
                continue

        instance_id = resp['Instances'][0]['InstanceId']
        print(f"Instance {instance_id} launched.")

        # 附加 EIP 到該實例
        associated = False
        
        # 等待實例狀態為 running 之後再綁
        try:
            ec2.get_waiter('instance_running').wait(
                InstanceIds=[instance_id],
                WaiterConfig={"Delay": 5, "MaxAttempts": 30})  # 最多約 150 秒
        except Exception as e:
            print(f"[WARN] Wait for running failed: {e}")
        

        # 這時 ENI 已就緒，才去綁 EIP
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
            time.sleep(5)
            continue

        # 等待 user-data 測試完成
        for _ in range(20):                  # 最多約 60 s
            time.sleep(3)
            statuses = ec2.describe_instance_status(
                InstanceIds=[instance_id],
                IncludeAllInstances=True
            )["InstanceStatuses"]
            if statuses and statuses[0]["SystemStatus"]["Status"] == "ok":
                break

        # 取得 console output
        try:
            output = ec2.get_console_output(InstanceId=instance_id, Latest=True)
            console_text = output.get('Output', '') or ''
        except Exception as e:
            console_text = ""
            print(f"[ERROR] Could not retrieve console output: {e}")

        # 解析 console_text 獲取延遲結果
        lines = console_text.splitlines()
        host_results = {}  # {hostname: {'passed': bool, 'median': float(min), 'best': float(min)} }
        global_best_latency = float("inf")  # 這台實例所有IP中最低延遲
        for line in lines:
            line = line.strip()
            if line.endswith(":") and not line.startswith("IP"):
                # hostname line
                current_host = line.rstrip(":")
                host_results[current_host] = {"passed": False, "median": float("inf"), "best": float("inf")}
            elif line.startswith("IP ") and "median=" in line:
                median_val = best_val = None
                passed_val = False
                parts = line.split()
                # 找出 median 值和 best 值
                for part in parts:
                    if part.startswith("median="):
                        median_val = float(part.split("=")[1])
                    if part.startswith("best="):
                        best_val = float(part.split("=")[1])
                    if part.startswith("passed="):
                        passed_val = part.split("=")[1]
                        passed_val = True if passed_val == "True" else False
                # 更新當前host的最佳數據
                if median_val is not None and median_val < host_results[current_host]["median"]:
                    host_results[current_host]["median"] = median_val
                if best_val is not None and best_val < host_results[current_host]["best"]:
                    host_results[current_host]["best"] = best_val
                # 如此IP通過閾值，標記該host為passed
                if passed_val:
                    host_results[current_host]["passed"] = True
                # 更新全域最低latency
                if best_val < global_best_latency:
                    global_best_latency = best_val

        # 計算該實例對所有host的「最差中位數」作為代表值
        instance_median = 0.0
        instance_passed = True
        for host, res in host_results.items():
            if res["median"] == float("inf"):
                # 沒有測得數值，當作失敗
                instance_passed = False
            else:
                if res["median"] > instance_median:
                    instance_median = res["median"]
            if not res["passed"]:
                instance_passed = False

        # instance_median / global_best_latency / instance_passed 皆已算好
        utc_now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
        print(f"[{utc_now}] {instance_id}  {instance_type:<9}  "
            f"median={instance_median:.2f} µs  best={global_best_latency:.2f} µs  "
            f"passed={instance_passed}")

        # 追寫到 CSV
        with open(CSV_FILE, "a", newline="") as f:
            csv.writer(f).writerow(
                [utc_now, instance_id, instance_type,
                f"{instance_median:.2f}", f"{global_best_latency:.2f}", instance_passed]
            )
            
        # 保存日統計
        if instance_median > 0:
            daily_medians.append(instance_median)
        if global_best_latency < float("inf"):
            daily_best_latencies.append(global_best_latency)
        daily_counts += 1

        if instance_passed:
            # 找到錨點
            anchor_instance_id = instance_id
            anchor_instance_type = instance_type
            print(f"*** Found anchor instance {instance_id} (type {instance_type}) meeting latency criteria! ***")
            print("Latency details:", host_results, f"Global best handshake = {global_best_latency:.2f} µs")
            # 寫入成功報告立即輸出
            success_report = (f"\n成功找到錨點小型實例！\n"
                            f"- Instance ID: {anchor_instance_id}\n"
                            f"- Instance Type: {anchor_instance_type}\n"
                            f"- Placement Group: {PLACEMENT_GROUP_NAME} (AZ {BEST_AZ})\n"
                            f"- Median latencies: " +
                            ", ".join([f"{h}={res['median']:.2f}µs" for h, res in host_results.items()]) + "\n" +
                            f"- Best single handshake overall: {global_best_latency:.2f} µs\n")
            print(success_report)
            # 將當天截至目前統計寫入報告
            report_date = start_date
            report_filename = f"{REPORT_DIR}/report-{report_date}.md"
            with open(report_filename, "a") as rpt:  # append to today's report
                rpt.write("\n**Anchor instance found, stopping small instance search.**\n")
                rpt.write(success_report)
            break
        else:
            # 未達標，終止實例繼續搜尋下一台
            print(f"Instance {instance_id} did not meet latency target. Terminating and continuing...")
            try:
                ec2.terminate_instances(InstanceIds=[instance_id])
            except Exception as e:
                print(f"[ERROR] Terminating {instance_id} failed: {e}")
            # 等待片刻以確保實例終止並 EIP 可用
            time.sleep(5)
            try:
                ec2.disassociate_address(AllocationId=EIP_ALLOC_ID)
            except:
                pass
    except KeyboardInterrupt:
        print("\n[CTRL‑C] Graceful shutdown requested…")
        # 若目前仍有一台 instance 在跑（且還沒被標記為 anchor）
        if 'instance_id' in locals() and instance_id and instance_id != anchor_instance_id:
            print(f"→ Terminating pending instance {instance_id} …")
            try:
                ec2.terminate_instances(InstanceIds=[instance_id])
            except Exception as e:
                print(f"  Terminate failed: {e}")
            # 確保 EIP 釋放
            try:
                ec2.disassociate_address(AllocationId=EIP_ALLOC_ID)
            except:
                pass
        # 若已經找到錨點，也可以選擇保留；這裡不動 anchor
        break   # 跳出 while True

# 迴圈結束 (錨點已找到或被手動中止)
if anchor_instance_id:
    print(f"Anchor instance is {anchor_instance_id} ({anchor_instance_type}). Keep it running for stage 3.")
else:
    print("Search stopped without finding an anchor instance.")
