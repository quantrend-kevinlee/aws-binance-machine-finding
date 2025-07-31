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

# Test latency locally (for baseline comparison)
python3 test_instance_latency.py

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
   - **aws/**: EC2 and placement group management
   - **champion/**: Champion selection and persistence
   - **testing/**: SSH and latency test execution
   - **logging/**: JSONL and text format logging
   - **ip_discovery/**: IP discovery, validation, and loading with DNS fallback

3. **test_instance_latency.py** - User-facing latency testing tool
   - Runs latency tests locally or on remote EC2 instances  
   - Provides beautiful formatted output for easy analysis
   - Automatically loads IP lists from `reports/ip_lists/ip_list_latest.json`
   - Falls back to DNS resolution if no IP list available
   - Supports both local baseline testing and remote instance testing

4. **Multi-Domain Champion System**
   - Tracks best instance per Binance service domain
   - Supports one instance championing multiple domains
   - Persists champion state across script restarts
   - Smart termination logic protects active champions

### AWS Resources

- **Region**: ap-northeast-1 (Tokyo)
- **Availability Zone**: ap-northeast-1a (Binance location)
- **Instance Types**: c8g.medium through c8g.48xlarge (ARM-based Graviton instances)
- **Placement Groups**: Dynamic cluster groups per instance
- **Public IP Assignment**: Auto-assign enabled on subnet (instances get public IPs automatically)
- **No Elastic IP Binding**: Instances use auto-assigned public IPs directly
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
    "placement_group_base": "dc-machine-cpg",
    "latency_thresholds": {
        "median_us": 122,
        "best_us": 102
    },
    "latency_test_domains": [
        "fstream-mm.binance.com",
        "ws-fapi-mm.binance.com",
        "fapi-mm.binance.com"
    ],
    "discovery_domains": [
        "fstream-mm.binance.com",
        "ws-fapi-mm.binance.com",
        "fapi-mm.binance.com",
        "stream.binance.com",
        "ws-api.binance.com",
        "api.binance.com"
    ],
    "instance_types": [
        "c8g.medium",
        "c8g.48xlarge",
        "c8g.large",
        "c8g.xlarge",
        "c8g.2xlarge",
        "c8g.4xlarge",
        "c8g.8xlarge",
        "c8g.12xlarge",
        "c8g.24xlarge"
    ],
    "report_dir": "./reports",
    "ip_list_dir": "./reports/ip_lists",  // Directory for IP list files
    "max_instance_init_wait_seconds": 600,  // Maximum wait time after SSH ready before testing
    "latency_test_timeout_scale_per_domain": 120,  // Timeout scale factor per domain (seconds)
    "latency_test_timeout_floor": 180         // Minimum timeout floor regardless of domain count (seconds)
}
```


### Binance Domains

Domains are now centrally configured in `config.json` with separate lists for IP discovery and latency testing:

```json
"latency_test_domains": [
    "fstream-mm.binance.com",  // Futures stream
    "ws-fapi-mm.binance.com",  // WebSocket API  
    "fapi-mm.binance.com"      // Futures REST API
],
"discovery_domains": [
    "fstream-mm.binance.com",  // Futures stream
    "ws-fapi-mm.binance.com",  // WebSocket API
    "fapi-mm.binance.com",     // Futures REST API
    "stream.binance.com",      // Spot stream
    "ws-api.binance.com",      // Spot WebSocket API
    "api.binance.com"          // Spot REST API
]
```

**Purpose:**
- `latency_test_domains`: Domains tested during instance evaluation for pass/fail criteria
- `discovery_domains`: All domains for IP discovery (can include more domains than tested)

## IP Discovery System

### Overview

The IP discovery system addresses DNS limitations and ensures comprehensive testing:
- DNS servers typically return only 8 IPs per query (round-robin subset)
- AWS DNS cache TTL is 60 seconds
- IPs can change as nodes are added/removed

### How It Works

1. **Standalone Discovery** (`discover_ips.py`):
   - Runs independently from instance testing
   - Queries each domain multiple times per batch
   - Waits 60 seconds between batches to bypass DNS cache
   - Validates IP liveness with TCP connectivity tests
   - Removes dead IPs automatically
   - Persists live IPs to `reports/ip_lists/ip_list_latest.json`

2. **Integration with Testing**:
   - `find_instance.py` reads IP list from file at startup
   - Falls back to DNS resolution if no IP list exists (fresh repo)
   - No background IP collection during instance testing
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
          "last_validated": "2025-07-25T10:30:00+08:00"
        }
      }
    }
  }
}
```

Note: All IPs in `ip_list_latest.json` are considered alive. Dead IPs are moved to `ip_list_dead.jsonl`.

### Usage

```bash
# Run IP discovery separately (recommended before main run)
python3 discover_ips.py

# Note: discover_ips.py runs continuously by default, use Ctrl+C to stop

# Run latency test locally with beautiful formatted output
python3 test_instance_latency.py

# Run latency test on remote instance
python3 test_instance_latency.py i-1234567890abcdef0
```

## Testing Workflow

1. **Instance Launch**
   - Create unique placement group (`dc-machine-cpg-{timestamp}`)
   - Launch instance with timestamp prefix (`{timestamp}-DC-Search`)
   - Instance automatically gets public IP (subnet auto-assign enabled)
   - Instance fails gracefully if no public IP (check subnet auto-assign setting)

2. **Latency Testing**
   - SSH to instance and deploy test script
   - Use pre-discovered IPs from `ip_list_latest.json`
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

## Local Testing

### Running Latency Tests Locally

The system supports running latency tests locally for baseline comparisons and development:

```bash
# Run locally with beautiful formatted output (no instance ID needed)
python3 test_instance_latency.py

# Run on remote instance with beautiful formatted output
python3 test_instance_latency.py i-1234567890abcdef0

# Use test_instance_latency.py for both local and remote testing
```

### Benefits of Local Testing

- **Baseline Comparison**: Compare local latency vs EC2 instances
- **Development**: Test code changes without launching instances  
- **Cost Savings**: No EC2 costs for initial testing
- **IP List Validation**: Verify discovered IPs work correctly
- **Network Troubleshooting**: Debug connectivity issues

### Local vs Remote Results

Local results typically show higher latency due to:
- Different network path (home/office vs AWS datacenter)
- Consumer internet vs enterprise backbone
- Geographic distance to Binance servers
- Local network congestion and routing

Use local tests to validate functionality, not for production optimization.

## Utility Scripts

### IP Verification

The `test_ip_for_is_fstream.py` script verifies if an IP address belongs to fstream-mm.binance.com:

```bash
# Test if an IP serves fstream-mm.binance.com content
python3 tool_scripts/test_ip_for_is_fstream.py 52.195.47.229
```

This is useful for:
- Verifying discovered IPs are correct
- Debugging connection issues
- Confirming IP ownership before using in production

### Auto-Assigned IP Refresh

The `test_latency_with_new_auto_ip.py` script forces AWS to assign a new public IP:

```bash
# Refresh auto-assigned IP and test latency
python3 tool_scripts/test_latency_with_new_auto_ip.py i-1234567890abcdef0
```

Use cases:
- Testing if different auto-assigned IPs affect latency
- Working around problematic auto-assigned IPs
- Validating network path variations

Note: Uses `binance_vip_whitelisted_eip_allocation_id` from config.json for temporary EIP binding during the refresh process.

### EIP Binding

The `bind_eip.py` script allows you to bind any Elastic IP to an instance:

```bash
# Bind specific EIP to instance
python3 tool_scripts/bind_eip.py <instance-id> <eip-allocation-id>
python3 tool_scripts/bind_eip.py i-1234567890abcdef0 eipalloc-05500f18fa63990b6
```

This script:
- Checks if the EIP is already associated with another instance
- Disassociates it if needed before binding to the target instance
- Provides SSH commands with the new EIP address

Use cases:
- Binding Binance VIP whitelisted IPs to champion instances
- Moving EIPs between instances for testing
- Getting ready-to-use SSH commands after EIP binding

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
After SSH is ready, the system waits (configurable via max_instance_init_wait_seconds) for the instance to stabilize:

1. **CPU Load Monitoring**: Displays CPU load average every 5 seconds
2. **EC2 Status Checks**: Can exit early if EC2 status checks pass (3/3)
3. **Network Verification**: Confirms basic network connectivity is working

#### Recommended Settings
- **Default**: `max_instance_init_wait_seconds: 600` - Allows instance to fully stabilize (10 minutes)
- **Fast testing**: `max_instance_init_wait_seconds: 30` - Shorter wait for quick tests
- **Skip wait**: `max_instance_init_wait_seconds: 0` - Not recommended for accurate results

### SSH Access to Champions

```bash
# Method 1: Direct SSH if instance has public IP (auto-assigned)
ssh -i ~/.ssh/dc-machine -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ec2-user@<PUBLIC_IP>

# Method 2: Using the convenient ssh_instance.py script (handles IP detection)
python3 tool_scripts/ssh_instance.py i-03fa7ce9d925be452

# Method 3: Using bind_eip.py script for EIP binding
python3 tool_scripts/bind_eip.py i-03fa7ce9d925be452 eipalloc-05500f18fa63990b6

# Method 4: Manual EIP binding (if needed)
aws ec2 associate-address \
  --instance-id i-03fa7ce9d925be452 \
  --allocation-id <YOUR_EIP_ALLOCATION_ID>
ssh -i ~/.ssh/dc-machine ec2-user@<EIP_ADDRESS>
```

#### SSH Without Known Hosts Issues

The scripts use these SSH options to avoid host key verification issues:
- `-o StrictHostKeyChecking=no` - Don't check host keys
- `-o UserKnownHostsFile=/dev/null` - Don't save host keys
- `-o ConnectTimeout=10` - Timeout after 10 seconds

This is useful when testing different instances with temporary public IPs.

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
| `test_instance_latency.py` | Run latency tests locally or on remote instances with beautiful formatted output |
| `discover_ips.py` | Standalone IP discovery and validation tool (run separately from instance testing) |

### Tool Scripts

Located in `tool_scripts/` directory:

| Script | Purpose |
|--------|---------|
| `setup_aws_resources.py` | Create VPC, subnets, security groups, etc. |
| `check_vpc_dns.py` | Verify/fix VPC DNS settings |
| `query_jsonl.py` | Analyze JSONL latency logs |
| `cleanup_orphaned_placement_groups.py` | Remove orphaned placement groups from terminated instances |
| `ssh_instance.py` | SSH into instance by ID using its public IP |
| `check_subnet_public_ip.py` | Check/configure subnet auto-assign public IP settings |
| `launch_test_instance.py` | Launch test instance with public IP control |
| `terminate_all_champions.py` | Terminate all champion instances and clean up placement groups |
| `test_ip_for_is_fstream.py` | Verify if an IP belongs to fstream-mm.binance.com by comparing WebSocket responses |
| `test_latency_with_new_auto_ip.py` | Test instance latency after forcing AWS to assign a new auto-assigned public IP |
| `bind_eip.py` | Bind a specific Elastic IP to an instance and provide SSH commands |

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
   - Confirm instance has a public IP (auto-assigned)

3. **Insufficient Capacity Errors**
   - Script automatically tries next instance type
   - Consider adjusting instance_types in config.json

4. **Champion Not Found on Restart**
   - Script validates champions are still running
   - Terminated instances removed from champion state
   - Check AWS console for instance status

5. **Latency Test Timeouts**
   - Test timeout scales with domain count (configurable via latency_test_timeout_scale_per_domain)
   - Minimum timeout ensures tests complete (configurable via latency_test_timeout_floor)
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
8. **Dynamic Test Timeout**: Scales with number of domains (configurable via latency_test_timeout_scale_per_domain) with a minimum floor (latency_test_timeout_floor)
9. **Real-time Progress**: Displays remote test progress on local terminal for debugging
10. **Max Instance Initialization Wait**: Configurable maximum wait after SSH ensures accurate latency measurements by allowing the instance to stabilize
11. **Auto-Naming**: Instances automatically renamed to reflect their champion/anchor status for easy identification
12. **Automatic IP List Loading**: `test_instance_latency.py` auto-loads IP lists from default location for comprehensive testing
13. **Local Testing Support**: `test_instance_latency.py` can run locally without instance ID for baseline comparisons
14. **Clean Architecture**: Internal implementation organized in `core/`, user-facing scripts at root level
15. **No EIP Dependency**: System relies entirely on subnet auto-assigned public IPs, eliminating EIP management overhead
16. **Separated IP Discovery**: IP discovery runs as a standalone process (`discover_ips.py`), not during instance testing, for cleaner separation of concerns