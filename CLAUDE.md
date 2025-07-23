# DC Machine - AWS EC2 Low Latency Instance Finder

## Project Overview

This project automatically finds AWS EC2 instances with the lowest network latency to Binance servers for high-frequency trading purposes.

### Goal

Find EC2 instances in ap-northeast-1a with TCP handshake latency to Binance servers:

-   **Initial search**: Small instances (c7i.large/c8g.large) to find optimal rack location
-   **Production deployment**: Large instances (c8g.24xlarge/c8g.metal-24xl) in same Cluster Placement Group

### Latency Requirements

ANY IP address from the Binance domains must meet ONE of these criteria:

-   Median latency ≤ 122 µs OR
-   Best single handshake ≤ 102 µs

## Core Features

1. **SSH-Based Testing**

    - Direct SSH connection to instances for reliable test execution
    - No dependency on EC2 console output or user-data scripts
    - Real-time progress updates during testing
    - Clean JSON output for reliable parsing

2. **Instance Management**

    - Instance names prefixed with Unix timestamp (format: `{timestamp}-DC-Search`)
    - Alternates between instance types (currently c8g.24xlarge/c8g.metal-24xl)
    - Single Elastic IP reused across all test instances
    - **fstream-mm Champion System**: Always maintains the instance with the lowest fstream-mm latency
    - **Dynamic Placement Groups**: Each instance gets a unique placement group (`dc-machine-cpg-{timestamp}`)
    - **Asynchronous Cleanup**: Failed instances and their placement groups are cleaned up in background threads

3. **Latency Testing**

    - Tests TCP handshake latency to 3 Binance endpoints:
        - fapi-mm.binance.com
        - ws-fapi-mm.binance.com
        - fstream-mm.binance.com
    - Each domain resolves to multiple IPs (8 IPs per domain typically)
    - Performs 10,000 TCP connections per IP
    - Calculates median and best (minimum) latencies

4. **Pass Criteria**

    - Instance passes if ANY single IP meets median ≤ 122 µs OR best ≤ 102 µs
    - Tracks best values across all IPs with their source IP and hostname

5. **Reporting**
    - **Per-domain tracking**: Best median and best latency tracked separately for each Binance service
    - Enhanced CSV format includes optimal IPs for each domain:
        - fapi-mm.binance.com (Futures API)
        - ws-fapi-mm.binance.com (WebSocket stream)
        - fstream-mm.binance.com (Futures stream)
    - Detailed text logs (`latency_log_YYYY-MM-DD.txt`) with full test results
    - Daily Markdown reports with statistics
    - Automatic daily rollover at midnight

## AWS Resources Required

-   **Region**: ap-northeast-1
-   **Availability Zone**: ap-northeast-1a (Binance location)
-   **VPC**: Must have DNS enabled (`enableDnsSupport` and `enableDnsHostnames`)
-   **Subnet**: Pre-configured subnet in target AZ
-   **Security Group**: Allows SSH (port 22) and outbound HTTPS (port 443)
-   **Key Pair**: SSH key at `~/.ssh/dc-machine.pem`
-   **Elastic IP**: Single EIP reused across test instances
-   **Placement Groups**: Dynamic cluster placement groups created per instance
-   **IAM Permissions**: EC2 full access

## Script Configuration

Configuration is centralized in `config.json` (except domains which are defined in `binance_latency_test.py`):

```json
{
    "region": "ap-northeast-1",
    "availability_zone": "ap-northeast-1a",
    "subnet_id": "subnet-07954f36129e8beb1",
    "security_group_id": "sg-080dea8b90091be0b",
    "key_name": "dc-machine",
    "key_path": "~/.ssh/dc-machine",
    "eip_allocation_id": "eipalloc-05500f18fa63990b6",
    "placement_group_base": "dc-machine-cpg",
    "latency_thresholds": {
        "median_us": 122,
        "best_us": 102
    },
    "instance_types": [
        "c8g.medium",
        "c8g.large",
        "c8g.xlarge",
        "c8g.2xlarge",
        "c8g.4xlarge"
    ],
    "report_dir": "./reports"
}
```

Domains are defined in `binance_latency_test.py`:

```python
DOMAINS = [
    "fstream-mm.binance.com",
    "ws-fapi-mm.binance.com",
    "fapi-mm.binance.com"
]
```

## Testing Flow

1. Create unique placement group with timestamp (`dc-machine-cpg-{timestamp}`)
2. Launch instance with Unix timestamp prefix in name (format: `{timestamp}-DC-Search`)
3. Wait for instance to reach "running" state
4. Associate Elastic IP to instance
5. Wait for SSH availability
6. Copy `binance_latency_test.py` to instance via SSH
7. Execute test script and capture JSON output
8. Parse JSON results from test script
9. **Champion Evaluation**: Check if instance has better fstream-mm latency than current champion
10. **Champion Management**: 
    - If better → promote to champion, terminate old champion
    - If worse → check overall pass criteria
11. **Overall Pass Criteria**: Evaluate against thresholds (ANY IP meeting criteria = pass)
12. **Instance Disposition**:
    - If overall pass → keep as anchor, stop searching
    - If champion → keep running, continue searching
    - If neither → terminate and continue searching

## Output Format

### Console Output

Per-domain best results for optimal IP pinning:

```
[2025-07-22T05:35:26+00:00] i-014fc6ce2eed063b7  c8g.24xlarge
  fapi-mm: median=267.20µs (35.79.37.81), best=167.34µs (54.199.94.11)
  ws-fapi-mm: median=254.50µs (52.68.15.23), best=152.10µs (52.68.15.23)
  fstream-mm: median=261.80µs (13.114.195.190), best=158.90µs (18.176.4.238)
  Passed: False
```

### CSV Format

Per-domain tracking for optimal IP selection:

```csv
timestamp,instance_id,instance_type,best_median_us_fapi-mm,best_best_us_fapi-mm,best_median_ip_fapi-mm,best_best_ip_fapi-mm,best_median_us_ws-fapi-mm,best_best_us_ws-fapi-mm,best_median_ip_ws-fapi-mm,best_best_ip_ws-fapi-mm,best_median_us_fstream-mm,best_best_us_fstream-mm,best_median_ip_fstream-mm,best_best_ip_fstream-mm,passed
2025-07-22T05:35:26+00:00,i-014fc6ce2eed063b7,c8g.24xlarge,267.20,167.34,35.79.37.81,54.199.94.11,254.50,152.10,52.68.15.23,52.68.15.23,261.80,158.90,13.114.195.190,18.176.4.238,False
```

## Development History

### Major Changes

1. ✅ **Console output parsing → SSH-based execution**: Eliminated unreliable console output parsing
2. ✅ **Fixed pass criteria → ANY IP passing**: Changed from "all hosts must pass" to "any IP can pass"
3. ✅ **DNS issues → VPC DNS enabled**: Fixed DNS resolution by enabling VPC DNS settings
4. ✅ **Basic CSV → Enhanced CSV**: Added source IP/host information for best values
5. ✅ **Static names → Timestamped names**: Added Unix timestamp prefix to instance names
6. ✅ **Embedded script → External file**: Test script now loaded from `binance_latency_test.py` file
7. ✅ **Static placement group → Dynamic placement groups**: Each test uses unique PG for rack diversity
8. ✅ **Synchronous cleanup → Asynchronous cleanup**: Background threads handle termination/deletion
9. ✅ **Global best tracking → Per-domain tracking**: Track optimal IPs separately for each Binance service
10. ✅ **Simple pass/fail → Champion system**: Maintain best fstream-mm instance while continuing search
11. ✅ **Hardcoded config → Centralized config.json**: All configuration now in single JSON file

### Key Files

-   `config.json`: Centralized configuration file for all scripts
-   `find_small_anchor.py`: Main script that launches instances and runs tests via SSH
-   `binance_latency_test.py`: Latency test script executed on each instance (formerly our.py)
-   `setup_aws_resources.py`: Creates all required AWS resources with DNS properly configured  
-   `check_vpc_dns.py`: Verifies and fixes VPC DNS settings
-   `dc.py`: Reference latency test script provided by client DC (contains bugs, see script comments)

## Usage

### Initial Setup

```bash
# Create all AWS resources
python3 setup_aws_resources.py

# Or just check/fix DNS on existing VPC
python3 check_vpc_dns.py
```

### Find Anchor Instance

```bash
python3 find_small_anchor.py

# Graceful shutdown with Ctrl+C will wait for all cleanup tasks
# Background cleanup threads continue even if script exits normally
```

### Cleanup

For cleanup, manually terminate instances and delete placement groups via AWS console or CLI. Champions are protected and must be explicitly terminated if no longer needed.

## Test Scripts

### binance_latency_test.py

The main latency test script executed on each instance:

-   Defines domains to test in DOMAINS array (imported by find_small_anchor.py)
-   Resolves all IPs for each Binance domain using `host` command
-   Performs 1,000 TCP handshakes per IP  
-   Uses nanosecond precision timing
-   Calculates median and best (minimum) latencies
-   Returns clean JSON with raw data only
-   No hardcoded thresholds - pass/fail decision made by main script

### dc.py

Reference latency testing script provided by client DC:

-   Performs 100 TCP handshakes per IP
-   Uses basic `os.popen()` for DNS resolution
-   Calculates average latency in microseconds (sum of 100 measurements × 10 × 1000)
-   Simple text output format
-   Contains one known bug (marked with comment in code):
    -   `ip.address` should be `ip` in error handling
-   Not used in production; `binance_latency_test.py` is the improved version

## Placement Group Strategy

### Why Dynamic Placement Groups?

AWS cluster placement groups place instances on the same physical rack for lowest latency. However:
- AWS doesn't guarantee which rack a placement group uses
- Empty placement groups don't have "affinity" to previous rack locations
- Different racks can have significantly different latency to external endpoints (30-67% variation reported by HFT traders)

### Implementation

1. **Unique PG per test**: Each instance gets `dc-machine-cpg-{timestamp}`
2. **Fresh placement**: Ensures AWS selects from all available racks
3. **Asynchronous cleanup**: Background threads handle cleanup without blocking tests
4. **Graceful shutdown**: Ctrl+C waits for all cleanup tasks to complete

### Cleanup Behavior

- **Instance launch fails**: Placement group deleted immediately (synchronous)
- **Instance fails tests**: Placement group deletion scheduled in background thread
- **Background threads**: Check instance status every minute for up to 30 minutes
- **Automatic deletion**: Placement groups deleted once instances fully terminate
- **Progress tracking**: Clear console output shows cleanup status
- **Manual cleanup**: Use AWS console or CLI to clean up resources
- **Graceful shutdown**: Ctrl+C waits for all background cleanup tasks to complete

## fstream-mm Champion System

### Purpose

For HFT applications, maintaining the absolute best fstream-mm connection is critical. The champion system ensures you always have access to the lowest-latency fstream-mm instance, even while continuing to search for better options.

### How It Works

1. **Champion Tracking**: Script tracks the instance with the lowest "best latency" for fstream-mm domain
2. **Champion Protection**: Champions are never terminated, even if they fail overall pass criteria
3. **Champion Replacement**: When a better instance is found, the old champion is terminated and the new one promoted
4. **Champion Persistence**: Champions survive script termination, cleanup operations, and system restarts
5. **Continuous Improvement**: Search continues indefinitely to find incrementally better champions

### Champion Persistence

Champions are protected through multiple mechanisms:

- **State File**: Champion details saved to `reports/champion_state.json`
- **Startup Recovery**: Script loads existing champion state on restart and validates instance is still running
- **Cleanup Protection**: Champions are never automatically terminated
- **EIP Management**: EIP automatically moves to new instances as needed

### Champion Selection Criteria

- **Metric**: Lowest "best latency" value for fstream-mm.binance.com domain
- **Comparison**: New instance must have lower latency than current champion
- **Fallback**: If fstream-mm data is missing/invalid, instance cannot become champion

### Console Output

```
🏆 New fstream-mm champion! 125.30µs (13.114.195.190)
   Replacing old champion i-abc123 (142.50µs)
   🛡️ Champion will persist after script termination!
   💾 Champion state saved to ./reports/champion_state.json

Instance i-def456 is the fstream-mm champion - keeping it running!
  Champion: 125.30µs (13.114.195.190)
  🛡️ Champion protected: Instance and placement group will persist
  📤 EIP unbound from champion (can be rebound later for access)
  💡 To reconnect: Associate EIP to i-def456 and SSH to 125.30µs
```

### Champion Logging

Dedicated champion events are logged to `champion_log_YYYY-MM-DD.txt`:

```
2025-07-22T08:45:23+00:00
  New Champion: i-def456 (c8g.large)
  fstream-mm Best Latency: 125.30µs
  fstream-mm Optimal IP: 13.114.195.190
  Placement Group: dc-machine-cpg-1753169400
--------------------------------------------------------------------------------
```

### Script Exit

When the script exits, it displays comprehensive champion status:

```
🏆 Current fstream-mm champion: i-def456 (c8g.large)
   Best latency: 125.30µs (13.114.195.190)
   Placement Group: dc-machine-cpg-1753169400
   Status: 🛡️ PROTECTED - Will persist after script termination

   📋 Champion Access Instructions:
   1. To SSH to champion: aws ec2 associate-address --instance-id i-def456 --allocation-id eipalloc-05500f18fa63990b6
   2. Then SSH to EIP address with key: ~/.ssh/dc-machine
   3. For production: Use IP 13.114.195.190 for fstream-mm.binance.com connections

   💾 Champion state persisted in: ./reports/champion_state.json
   📜 Champion log available at: ./reports/champion_log_2025-07-22.txt
```

## IP Pinning for Production

### Using Per-Domain Results

The CSV output provides optimal IPs for each Binance service. Use these to pin connections:

```python
# From CSV results, pin each service to its optimal IP:
fapi_optimal_ip = "35.79.37.81"      # best_median_ip_fapi-mm
ws_optimal_ip = "52.68.15.23"        # best_median_ip_ws-fapi-mm  
stream_optimal_ip = "13.114.195.190" # best_median_ip_fstream-mm

# Examples:
# 1. /etc/hosts method:
#    35.79.37.81 fapi-mm.binance.com
#    52.68.15.23 ws-fapi-mm.binance.com
#    13.114.195.190 fstream-mm.binance.com

# 2. Direct IP connection with Host header:
#    wss://52.68.15.23/ws (with Host: ws-fapi-mm.binance.com)
```

### Benefits:
- **Service-specific optimization**: Each service pinned to its optimal IP
- **Consistent latency**: Avoid DNS lookup variations
- **Maximum performance**: Use best performing path for each service

## Next Steps

### If Anchor Instance Found (meets pass criteria):

1. Note its Placement Group name (includes timestamp)
2. Extract optimal IPs for each domain from CSV
3. Launch production instances in the SAME placement group
4. Configure applications to use the optimal IPs
5. Keep the anchor instance running to maintain the placement group

### If Only Champion Found (best fstream-mm, no anchor):

1. **Use the champion for fstream-mm**: Configure fstream-mm connections to use champion's optimal IP
2. **Continue searching**: Run the script again to find an anchor or better champion
3. **Production strategy**: 
   - Use champion for fstream-mm.binance.com connections
   - Use separate instances/IPs for fapi-mm and ws-fapi-mm if needed
   - Launch additional instances in champion's placement group for scaling

### Champion Utilization:

```python
# Use champion's fstream-mm IP for optimal latency
champion_fstream_ip = "13.114.195.190"  # From champion results

# Pin fstream-mm connections to champion IP
# Method 1: /etc/hosts
echo "13.114.195.190 fstream-mm.binance.com" >> /etc/hosts

# Method 2: Direct IP connection
# wss://13.114.195.190/ws (with Host: fstream-mm.binance.com)
```
