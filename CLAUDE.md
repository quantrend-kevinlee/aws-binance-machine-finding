# DC Machine - AWS EC2 Low Latency Instance Finder

## Project Overview

This project automatically finds AWS EC2 instances with the lowest network latency to Binance servers for high-frequency trading purposes.

### Goal
Find small EC2 instances (c7i.large/c8g.large) in ap-northeast-1a with TCP handshake latency to Binance servers:
- Median latency ≤ 122 µs OR
- Best single handshake ≤ 102 µs

Once found, this "anchor" instance serves as the location reference for deploying larger instances (24xL/48xL) in the same Cluster Placement Group.

## Core Features

1. **Automated Instance Testing**
   - Alternates between c7i.large and c8g.large instance types
   - Launches instances → Attaches EIP → Runs latency tests → Captures results → Terminates non-qualifying instances
   - Keeps the first instance meeting latency criteria as the "anchor"

2. **Latency Testing**
   - Tests TCP handshake latency to 3 Binance endpoints
   - Performs 10,000 connection attempts per IP
   - Calculates median and best (minimum) latencies

3. **Reporting**
   - Real-time console output with test results
   - CSV logging: `reports/latency_log_YYYY-MM-DD.csv`
   - Daily Markdown reports: `reports/report-YYYY-MM-DD.md`
   - Automatic daily rollover at midnight

4. **Graceful Shutdown**
   - Ctrl-C handling to cleanly terminate testing
   - Releases EIP and terminates pending instances
   - Preserves found anchor instance

## AWS Resources Required

- **Region**: ap-northeast-1
- **Availability Zone**: ap-northeast-1a (Binance location)
- **VPC/Subnet**: Pre-configured subnet in target AZ
- **Security Group**: Allows outbound HTTPS (port 443)
- **Key Pair**: SSH key for instance access
- **Elastic IP**: Single EIP reused across test instances
- **Placement Group**: Cluster type for co-location
- **IAM Permissions**: EC2 full access, including RunInstances, TerminateInstances, AssociateAddress

## Script Configuration

Key variables in `find_small_anchor.py`:
```python
REGION = "ap-northeast-1"
BEST_AZ = "ap-northeast-1a"
SUBNET_ID = "subnet-07954f36129e8beb1"
SECURITY_GROUP_ID = "sg-080dea8b90091be0b"
KEY_NAME = "dc-machine"
EIP_ALLOC_ID = "eipalloc-05500f18fa63990b6"
PLACEMENT_GROUP_NAME = "dc-machine-cpg"
INSTANCE_TYPES = ["c7i.large", "c8g.large"]
```

## Testing Flow

1. Launch instance with user-data script containing latency test
2. Wait for instance to reach "running" state
3. Associate Elastic IP to instance
4. Wait for system status checks to pass
5. Retrieve console output with test results
6. Parse results and determine if thresholds are met
7. If passed: Keep as anchor instance
8. If failed: Terminate and try next instance

## Output Format

### Console Output
```
[2025-01-22T10:30:45] i-0123456789abcdef c7i.large  median=120.50 µs  best=98.30 µs  passed=True
```

### CSV Format
```csv
timestamp,instance_id,instance_type,median_us,best_us,passed
2025-01-22T10:30:45,i-0123456789abcdef,c7i.large,120.50,98.30,True
```

### Daily Report
- Total instances tested
- Instance type breakdown
- Fastest instance details
- Latency distribution statistics

## Known Issues

1. **Zero/Infinity Values**: Some instances show `median=0.00 µs  best=inf µs`, indicating the user-data script output wasn't captured properly from EC2 console
2. **Console Output Timing**: May need longer wait time for user-data completion before retrieving console output

## Development History

### Iterations Completed
1. ✅ EIP binding failures → Added instance_running waiter
2. ✅ Missing reports directory → Auto-create with os.makedirs()
3. ✅ Daily report overwrites → Automatic midnight rollover
4. ✅ No graceful shutdown → Added KeyboardInterrupt handling
5. ✅ statistics.quantiles error → Added sample size check
6. ✅ Empty instance status → Check array before indexing
7. ✅ None/inf in CSV → Initialize variables per-instance, guard against None
8. ✅ Pass criteria → Changed to "any host meeting median OR best threshold"

## Usage

```bash
python3 find_small_anchor.py
```

The script will run continuously until:
- An anchor instance meeting latency criteria is found
- User interrupts with Ctrl-C (graceful shutdown)

## Next Steps

Once an anchor instance is found, use its Placement Group to launch larger instances (24xL/48xL) for production workloads, ensuring they're co-located in the same rack/cluster for optimal performance.