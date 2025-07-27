# DC Machine - AWS EC2 Low Latency Instance Finder

## Overview

This project automatically finds AWS EC2 instances with the lowest network latency to Binance servers for high-frequency trading in the ap-northeast-1a availability zone.

### Key Goals

- **Discovery**: Find EC2 instances with ultra-low latency to Binance endpoints
- **Optimization**: Identify optimal rack locations using dynamic placement groups
- **Persistence**: Maintain best-performing instances as "champions" for each service

### Latency Targets

An instance passes if ANY IP from the Binance domains meets:
- Median TCP handshake latency ≤ 122 µs OR
- Best single handshake latency ≤ 102 µs

## Quick Start

```bash
# Initial AWS setup (one-time)
python3 tool_scripts/setup_aws_resources.py

# Pre-discover IPs for comprehensive testing (recommended)
python3 discover_ips.py

# Start finding low-latency instances
python3 find_instance.py

# Query results
python3 tool_scripts/query_jsonl.py all
```

## Architecture

### Core Components

1. **find_instance.py** - Main entry point
   - Simple wrapper that starts the orchestration process
   - Loads configuration and initializes the system

2. **core/** - Modular architecture for maintainability
   - **config.py**: Configuration management with validation
   - **orchestrator.py**: Main loop coordination
   - **aws/**: EC2, EIP, and placement group management
   - **champion/**: Champion selection and persistence
   - **testing/**: SSH and latency test execution
   - **logging/**: JSONL and text format logging
   - **ip_discovery/**: Comprehensive IP discovery and validation

3. **binance_latency_test.py** - Latency measurement script
   - Runs on each EC2 instance
   - Tests TCP handshake latency to Binance endpoints
   - Returns JSON results for analysis

4. **Multi-Domain Champion System**
   - Tracks best instance per Binance service domain
   - Supports one instance championing multiple domains
   - Persists champion state across script restarts
   - Smart termination logic protects active champions

### AWS Resources

- **Region**: ap-northeast-1 (Tokyo)
- **Availability Zone**: ap-northeast-1a (Binance location)
- **Instance Types**: c8g.medium through c8g.4xlarge (ARM-based)
- **Placement Groups**: Dynamic cluster groups per instance
- **Elastic IP**: Single EIP reused across test instances
- **VPC Requirements**: DNS enabled (enableDnsSupport, enableDnsHostnames)

## Configuration

### config.json

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
    "domains": [
        "fstream-mm.binance.com",
        "ws-fapi-mm.binance.com"
    ],
    "instance_types": [
        "c8g.medium",
        "c8g.large", 
        "c8g.xlarge",
        "c8g.2xlarge",
        "c8g.4xlarge"
    ],
    "report_dir": "./reports",
    "network_init_wait_seconds": 30,  // Wait time after SSH ready before testing
    "timeout_per_domain_seconds": 30,  // Timeout per domain for latency tests
    "min_timeout_seconds": 180         // Minimum timeout regardless of domain count
}
```

### Binance Domains

Domains are now centrally configured in `config.json`:

```json
"domains": [
    "fstream-mm.binance.com",  // Futures stream
    "ws-fapi-mm.binance.com"   // WebSocket API
]
```

Additional domains can be added as needed:
- `"fapi-mm.binance.com"` - Futures REST API
- `"stream.binance.com"` - Spot stream
- `"ws-api.binance.com"` - Spot WebSocket API
- `"api.binance.com"` - Spot REST API

## IP Discovery System

### Overview

The IP discovery system addresses DNS limitations and ensures comprehensive testing:
- DNS servers typically return only 8 IPs per query (round-robin subset)
- AWS DNS cache TTL is 60 seconds
- IPs can change as nodes are added/removed

### How It Works

1. **Continuous Discovery** (`discover_ips.py`):
   - Queries each domain multiple times per batch
   - Waits 60 seconds between batches to bypass DNS cache
   - Builds comprehensive IP list over time
   - Validates IP liveness with TCP connectivity tests
   - Persists IP data to `reports/ip_lists/`

2. **Integration with Testing**:
   - Orchestrator validates existing IPs on startup
   - Background collection continues during instance testing
   - Test instances receive comprehensive IP list (not just DNS subset)
   - No local DNS resolution needed on test instances

3. **IP List Format**:
```json
{
  "last_updated": "2025-07-25T10:30:00+08:00",
  "domains": {
    "fstream-mm.binance.com": {
      "ips": {
        "54.65.8.148": {
          "first_seen": "2025-07-25T10:00:00+08:00",
          "last_seen": "2025-07-25T10:30:00+08:00",
          "last_validated": "2025-07-25T10:30:00+08:00",
          "alive": true
        }
      }
    }
  }
}
```

### Usage

```bash
# Standalone IP discovery (recommended before main run)
python3 discover_ips.py

# Continuous mode for long-term collection
python3 discover_ips.py --continuous
```

## Testing Workflow

1. **Instance Launch**
   - Create unique placement group (`dc-machine-cpg-{timestamp}`)
   - Launch instance with timestamp prefix (`{timestamp}-DC-Search`)
   - Associate Elastic IP for SSH access

2. **Latency Testing**
   - SSH to instance and deploy test script
   - Resolve all IPs for each Binance domain
   - Perform 1,000 TCP handshakes per IP
   - Calculate comprehensive statistics: median, best (min), average, p1, p99, and max latencies

3. **Champion Evaluation**
   - Compare median latency against current champions
   - Promote better instances to champion status
   - Smart termination of replaced champions

4. **Result Logging**
   - JSONL format for flexible schema (`latency_log_YYYY-MM-DD.jsonl`)
   - Detailed text logs (`latency_log_YYYY-MM-DD.txt`)
   - Champion state persistence (`champion_state.json`)
   - All timestamps in UTC+8 (Singapore/HK time)

## Champion System

### How It Works

The champion system maintains the lowest-latency instance for each Binance service:

- **Independent Tracking**: Each domain has its own champion
- **Multi-Domain Support**: One instance can champion multiple domains
- **Median Latency Criteria**: Champions selected by lowest median latency
- **Protection**: Champions never auto-terminate
- **Persistence**: State survives script restarts
- **Auto-Naming**: Champion instances automatically renamed to reflect their status

### Instance Naming Convention

Instances are automatically renamed based on their status:

- **Search instances**: `{timestamp}-DC-Search` (initial name)
- **Champion instances**: `DC-Champ-{domain1}-{domain2}...` (e.g., `DC-Champ-fstream-ws-fapi`)
- **Anchor instances**: `DC-ANCHOR` or `DC-Champ-{domains}-ANCHOR` if also a champion

Domain abbreviations used in names:
- `fstream-mm.binance.com` → `fstream`
- `ws-fapi-mm.binance.com` → `ws-fapi`
- `fapi-mm.binance.com` → `fapi`
- `stream.binance.com` → `stream`
- `ws-api.binance.com` → `ws-api`
- `api.binance.com` → `api`

### Champion State Example

```json
{
  "format_version": "2.0",
  "champions": {
    "fstream-mm.binance.com": {
      "instance_id": "i-03fa7ce9d925be452",
      "placement_group": "dc-machine-cpg-1753253935",
      "median_latency": 209.32,
      "best_latency": 118.27,
      "ip": "13.113.223.24",
      "instance_type": "c8g.medium",
      "timestamp": "2025-07-23T14:59:21+08:00"
    }
  }
}
```

## Data Analysis

### Query JSONL Logs

```bash
# Summary of all data
python3 query_jsonl.py all

# Analyze specific file
python3 query_jsonl.py summary reports/latency_log_2025-07-23.jsonl

# Find records for a domain
python3 query_jsonl.py domain reports/latency_log_2025-07-23.jsonl fstream-mm.binance.com

# Show best latencies only
python3 query_jsonl.py best reports/latency_log_2025-07-23.jsonl
```

### JSONL Format Benefits

- **Flexible Schema**: Add/remove domains without breaking parsers
- **Self-Describing**: Each record contains field names
- **Streaming**: Append without parsing entire file
- **Tool Support**: Works with jq, pandas, and other JSON tools

Example JSONL record:
```json
{"timestamp":"2025-07-23T18:00:00+08:00","instance_id":"i-abc123","instance_type":"c8g.large","passed":true,"domains":{"fstream-mm.binance.com":{"median":213.02,"best":116.96,"median_ip":"54.249.128.172","best_ip":"54.249.128.172"}}}
```

## Production Deployment

### Using Champion IPs

Configure your trading applications to use the optimal IPs from champion instances:

```python
# From champion state
fapi_ip = "3.114.17.148"      # fapi-mm.binance.com champion
ws_ip = "52.198.205.156"       # ws-fapi-mm.binance.com champion
stream_ip = "13.113.223.24"    # fstream-mm.binance.com champion
```

### IP Pinning Methods

1. **Host File Method**:
   ```bash
   echo "13.113.223.24 fstream-mm.binance.com" >> /etc/hosts
   echo "52.198.205.156 ws-fapi-mm.binance.com" >> /etc/hosts
   echo "3.114.17.148 fapi-mm.binance.com" >> /etc/hosts
   ```

2. **Direct IP Connection**:
   ```python
   # WebSocket with Host header
   ws_url = "wss://52.198.205.156/ws"
   headers = {"Host": "ws-fapi-mm.binance.com"}
   ```

### Production Checklist

If **anchor instance found** (meets pass criteria):
1. Note placement group name
2. Extract optimal IPs from results
3. Launch production instances in SAME placement group
4. Keep anchor running to maintain placement group

If **only champions found**:
1. Use champion IPs for each service
2. Continue searching for better instances
3. Launch additional instances in champion placement groups
4. Monitor for new champions

## Placement Group Strategy

### Why Dynamic Groups?

AWS cluster placement groups provide lowest latency within a rack, but:
- No guarantee which physical rack is selected
- Different racks have 30-67% latency variation to external endpoints
- Fresh placement groups ensure testing across all available racks

### Implementation

- **Unique Names**: `dc-machine-cpg-{timestamp}` per instance
- **Automatic Cleanup**: Background threads delete groups after instance termination (checks every 10 seconds)
- **Graceful Shutdown**: Ctrl+C waits for cleanup completion

## Operational Notes

### Network Initialization and Latency Testing

The system waits for instances to stabilize before running latency tests to ensure accurate measurements.

#### Instance Readiness Check
After SSH is ready, the system waits (configurable via network_init_wait_seconds) for the instance to stabilize:

1. **CPU Load Monitoring**: Displays CPU load average every 5 seconds
2. **EC2 Status Checks**: Can exit early if EC2 status checks pass (3/3)
3. **Network Verification**: Confirms basic network connectivity is working

#### Recommended Settings
- **Default**: `network_init_wait_seconds: 30` - Allows instance to fully stabilize
- **Fast testing**: `network_init_wait_seconds: 0` - Skip wait (not recommended for accurate results)

### SSH Access to Champions

```bash
# Method 1: Using the convenient ssh_instance.py script
python3 tool_scripts/ssh_instance.py i-03fa7ce9d925be452

# Method 2: Using bind_eip.py then SSH manually
python3 tool_scripts/bind_eip.py i-03fa7ce9d925be452
ssh -i ~/.ssh/dc-machine -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ec2-user@<EIP_ADDRESS>

# Method 3: Using AWS CLI directly
aws ec2 associate-address \
  --instance-id i-03fa7ce9d925be452 \
  --allocation-id eipalloc-05500f18fa63990b6
ssh -i ~/.ssh/dc-machine ec2-user@<EIP_ADDRESS>
```

#### SSH Without Known Hosts Issues

The scripts use these SSH options to avoid host key verification issues:
- `-o StrictHostKeyChecking=no` - Don't check host keys
- `-o UserKnownHostsFile=/dev/null` - Don't save host keys
- `-o ConnectTimeout=10` - Timeout after 10 seconds

This is useful when the same EIP is reused across different instances.

### Manual Cleanup

Champions are protected from auto-termination. To remove:
```bash
# Terminate instance
aws ec2 terminate-instances --instance-ids i-abc123

# Delete placement group (after instance terminates)
aws ec2 delete-placement-group --group-name dc-machine-cpg-1753253935

# Or use the cleanup script to find and remove all orphaned placement groups
python3 tool_scripts/cleanup_orphaned_placement_groups.py
```

### Monitoring

- **Live Progress**: Console output shows real-time test results
- **Champion Status**: Check `reports/champion_state.json`
- **Historical Data**: Query JSONL logs for trends
- **Champion Events**: Review `reports/champion_log_YYYY-MM-DD.txt`

## Scripts Reference

### Main Scripts

| Script | Purpose |
|--------|---------|
| `find_instance.py` | Main entry point - orchestrates the instance finding process |
| `binance_latency_test.py` | Latency test executed on each instance |
| `discover_ips.py` | Standalone IP discovery and validation tool |

### Tool Scripts

Located in `tool_scripts/` directory:

| Script | Purpose |
|--------|---------|
| `setup_aws_resources.py` | Create VPC, subnets, security groups, etc. |
| `check_vpc_dns.py` | Verify/fix VPC DNS settings |
| `query_jsonl.py` | Analyze JSONL latency logs |
| `cleanup_orphaned_placement_groups.py` | Remove orphaned placement groups from terminated instances |
| `bind_eip.py` | Bind Elastic IP to an instance by ID |
| `ssh_instance.py` | SSH into instance by ID (auto-binds EIP) |
| `test_instance_latency.py` | Bind EIP and run latency test on a specific instance |
| `terminate_all_champions.py` | Terminate all champion instances and clean up placement groups |

### Configuration Files

| File | Purpose |
|------|---------|
| `config.json` | AWS resources and test parameters |
| `reports/champion_state.json` | Current champion instances |
| `reports/latency_log_*.jsonl` | Test results in JSONL format |
| `reports/latency_log_*.txt` | Detailed test logs |
| `reports/ip_lists/ip_list_latest.json` | Latest discovered IP addresses |
| `reports/ip_lists/ip_list_dead.jsonl` | Historical dead IP records (append-only) |

## Troubleshooting

### Common Issues

1. **DNS Resolution Fails**
   - Run `python3 tool_scripts/check_vpc_dns.py` to verify VPC DNS settings
   - Ensure both enableDnsSupport and enableDnsHostnames are true

2. **SSH Connection Timeout**
   - Verify security group allows SSH (port 22)
   - Check instance is in "running" state
   - Confirm EIP is associated correctly

3. **Insufficient Capacity Errors**
   - Script automatically tries next instance type
   - Consider adjusting instance_types in config.json

4. **Champion Not Found on Restart**
   - Script validates champions are still running
   - Terminated instances removed from champion state
   - Check AWS console for instance status

5. **Latency Test Timeouts**
   - Test timeout scales with domain count (configurable via timeout_per_domain_seconds)
   - Minimum timeout ensures tests complete (configurable via min_timeout_seconds)
   - Progress is now displayed in real-time on your terminal
   - Shows DNS resolution, test progress, and results for each IP
   - If timeout occurs, check the last displayed progress to identify slow domains
   - Partial results may still be available even if timeout occurs

### Best Practices

- Run script during off-peak hours for consistent results
- Allow script to test multiple placement groups (rack diversity)
- Keep champion instances running for production use
- Monitor champion state file for unexpected changes
- Use Ctrl+C for graceful shutdown to ensure cleanup

## Development Notes

### Key Design Decisions

1. **Modular Architecture**: Separated concerns for maintainability and testing
2. **SSH-based Testing**: More reliable than EC2 console output
3. **Dynamic Placement Groups**: Ensures testing across all racks
4. **Median Latency**: More stable metric than minimum for champions
5. **JSONL Format**: Flexible schema for future domain changes
6. **UTC+8 Timezone**: Aligns with APAC trading hours
7. **Asynchronous Cleanup**: Non-blocking resource management
8. **Dynamic Test Timeout**: Scales with number of domains (configurable via timeout_per_domain_seconds and min_timeout_seconds)
9. **Real-time Progress**: Displays remote test progress on local terminal for debugging
10. **Network Initialization Wait**: Configurable wait after SSH ensures accurate latency measurements by allowing the instance to stabilize
11. **Auto-Naming**: Instances automatically renamed to reflect their champion/anchor status for easy identification