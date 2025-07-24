"""Shared constants for DC Machine."""

from datetime import timezone, timedelta

# Timezone
UTC_PLUS_8 = timezone(timedelta(hours=8))

# User data script for EC2 instances
USER_DATA_SCRIPT = """#!/bin/bash
yum install -y -q bind-utils python3
"""

# Default values
DEFAULT_SSH_TIMEOUT = 300  # Default for general SSH commands
DEFAULT_SSH_MAX_ATTEMPTS = 30
DEFAULT_SSH_RETRY_DELAY = 5

# Latency test timeout calculation
# The timeout scales with the number of domains being tested
# Formula: max(300, 100 * num_domains) seconds
# This ensures adequate time for testing multiple IPs per domain

# Thread cleanup settings
CLEANUP_CHECK_DELAY = 60  # seconds
CLEANUP_MAX_ATTEMPTS = 30  # 30 minutes total
CLEANUP_FINAL_DELAY = 10  # seconds after termination

# Logging
LOG_DATE_FORMAT = "%Y-%m-%d"
LOG_TIMESTAMP_FORMAT = "seconds"