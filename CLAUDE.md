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
    - Automatic termination of non-qualifying instances

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
    - Enhanced CSV format includes:
        - Best median value + source IP/host
        - Best latency value + source IP/host
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
-   **Placement Group**: Cluster type for co-location
-   **IAM Permissions**: EC2 full access

## Script Configuration

Key variables in `find_small_anchor.py`:

```python
REGION = "ap-northeast-1"
BEST_AZ = "ap-northeast-1a"
SUBNET_ID = "subnet-07954f36129e8beb1"
SECURITY_GROUP_ID = "sg-080dea8b90091be0b"
KEY_NAME = "dc-machine"
KEY_PATH = os.path.expanduser("~/.ssh/dc-machine")  # Note: no .pem extension in script
EIP_ALLOC_ID = "eipalloc-05500f18fa63990b6"
PLACEMENT_GROUP_NAME = "dc-machine-cpg"
MEDIAN_THRESHOLD_US = 122
BEST_THRESHOLD_US = 102
INSTANCE_TYPES = ["c8g.24xlarge", "c8g.metal-24xl"]  # Or ["c7i.large", "c8g.large"] for initial search
```

## Testing Flow

1. Launch instance with Unix timestamp prefix in name (format: `{timestamp}-DC-Search`)
2. Wait for instance to reach "running" state
3. Associate Elastic IP to instance
4. Wait for SSH availability
5. Read test script from `binance_latency_test.py` and copy to instance via SSH
6. Execute test script and capture JSON output
7. Parse JSON results from test script
8. Evaluate against thresholds (ANY IP meeting criteria = pass)
9. If passed: Keep as anchor instance
10. If failed: Terminate and try next instance

## Output Format

### Console Output

```
[2025-07-22T05:35:26+00:00] i-014fc6ce2eed063b7  c8g.24xlarge
  Best median: 267.20 µs (35.79.37.81 @ fapi-mm.binance.com)
  Best latency: 167.34 µs (54.199.94.11 @ fapi-mm.binance.com)
  Passed: False
```

### CSV Format

```csv
timestamp,instance_id,instance_type,best_median_us,best_median_ip,best_median_host,best_best_us,best_best_ip,best_best_host,passed
2025-07-22T05:35:26+00:00,i-014fc6ce2eed063b7,c8g.24xlarge,267.20,35.79.37.81,fapi-mm.binance.com,167.34,54.199.94.11,fapi-mm.binance.com,False
```

## Development History

### Major Changes

1. ✅ **Console output parsing → SSH-based execution**: Eliminated unreliable console output parsing
2. ✅ **Fixed pass criteria → ANY IP passing**: Changed from "all hosts must pass" to "any IP can pass"
3. ✅ **DNS issues → VPC DNS enabled**: Fixed DNS resolution by enabling VPC DNS settings
4. ✅ **Basic CSV → Enhanced CSV**: Added source IP/host information for best values
5. ✅ **Static names → Timestamped names**: Added Unix timestamp prefix to instance names
6. ✅ **Embedded script → External file**: Test script now loaded from `binance_latency_test.py` file

### Key Files

-   `find_small_anchor.py`: Main script that launches instances and runs tests via SSH
-   `binance_latency_test.py`: Latency test script executed on each instance (formerly our.py)
-   `setup_aws_resources.py`: Creates all required AWS resources with DNS properly configured
-   `cleanup_aws_resources.py`: Removes all created resources
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
```

### Cleanup

```bash
python3 cleanup_aws_resources.py
```

## Test Scripts

### binance_latency_test.py

The main latency test script executed on each instance:

-   Resolves all IPs for each Binance domain using `host` command
-   Performs 10,000 TCP handshakes per IP
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

## Next Steps

Once an anchor instance is found:

1. Note its Placement Group location
2. Launch production instances (c8g.24xlarge/c8g.metal-24xl) in same Placement Group
3. These co-located instances should have similar low latency characteristics
