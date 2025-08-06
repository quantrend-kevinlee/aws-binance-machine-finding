"""Shared constants for the latency finder."""

from datetime import timezone, timedelta

# Timezone
UTC_PLUS_8 = timezone(timedelta(hours=8))

# User data script for EC2 instances
USER_DATA_SCRIPT = """#!/bin/bash
# Latency finder instance - No optimizations
# This runs automatically when the instance boots

# Create log file
LOG_FILE="/var/log/latency-finder-init.log"
echo "[$(date)] Latency finder instance started" >> $LOG_FILE

# Install basic required packages
echo "[$(date)] Installing required packages..." >> $LOG_FILE
yum install -y -q bind-utils python3 2>&1 | tee -a $LOG_FILE

# Log instance information
echo "[$(date)] Instance information:" >> $LOG_FILE
echo "  Instance ID: $(ec2-metadata --instance-id | cut -d' ' -f2)" >> $LOG_FILE 2>/dev/null || true
echo "  Instance Type: $(ec2-metadata --instance-type | cut -d' ' -f2)" >> $LOG_FILE 2>/dev/null || true
echo "  Availability Zone: $(ec2-metadata --availability-zone | cut -d' ' -f2)" >> $LOG_FILE 2>/dev/null || true

echo "[$(date)] Latency finder initialization completed" >> $LOG_FILE
"""

# Default values
DEFAULT_SSH_TIMEOUT = 300  # Default for general SSH commands
DEFAULT_SSH_MAX_ATTEMPTS = 30
DEFAULT_SSH_RETRY_DELAY = 5

# Latency test timeout model
# TCP connection timeout is configurable via tcp_connection_timeout_ms in config.json
# SSH timeout uses a large fixed value (30 minutes) as a safety net
# Tests complete naturally when all TCP connections succeed or timeout

# Thread cleanup settings
CLEANUP_CHECK_DELAY = 10  # seconds
CLEANUP_MAX_ATTEMPTS = 180  # 30 minutes total (180 * 10 seconds)
CLEANUP_FINAL_DELAY = 10  # seconds after termination

# Logging
LOG_DATE_FORMAT = "%Y-%m-%d"
LOG_TIMESTAMP_FORMAT = "seconds"