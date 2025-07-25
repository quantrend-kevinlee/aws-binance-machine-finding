"""Shared constants for DC Machine."""

from datetime import timezone, timedelta

# Timezone
UTC_PLUS_8 = timezone(timedelta(hours=8))

# User data script for EC2 instances
USER_DATA_SCRIPT = """#!/bin/bash
# DC Machine Instance Initialization Script
# This runs automatically when the instance boots

# Create log file
LOG_FILE="/var/log/dc-machine-optimizer.log"
echo "[$(date)] Starting DC Machine optimization" >> $LOG_FILE

# Install required packages
echo "[$(date)] Installing required packages..." >> $LOG_FILE
yum install -y -q bind-utils python3 tuned-utils cpupowerutils 2>&1 | tee -a $LOG_FILE

# Track success
SUCCESS=true

# System optimization for low-latency measurements
echo "[$(date)] Applying system optimizations..." >> $LOG_FILE

# 1. Disable CPU idle states (C-states) to prevent wake-up latency
echo "[$(date)] Disabling CPU C-states..." >> $LOG_FILE
for cpu in /sys/devices/system/cpu/cpu*/cpuidle/state*/disable; do
    echo 1 > "$cpu" 2>/dev/null || true
done

# 2. Set CPU governor to performance mode
echo "[$(date)] Setting CPU governor to performance..." >> $LOG_FILE
if cpupower frequency-set -g performance 2>&1 | tee -a $LOG_FILE; then
    echo "[$(date)] CPU governor set to performance" >> $LOG_FILE
else
    # Try alternative method
    if echo performance | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor >/dev/null 2>&1; then
        echo "[$(date)] CPU governor set to performance (alt method)" >> $LOG_FILE
    else
        echo "[$(date)] WARN: Failed to set CPU governor" >> $LOG_FILE
        SUCCESS=false
    fi
fi

# 3. Disable irqbalance to prevent IRQ migration
echo "[$(date)] Disabling IRQBalance..." >> $LOG_FILE
if systemctl stop irqbalance 2>&1 | tee -a $LOG_FILE && systemctl disable irqbalance 2>&1 | tee -a $LOG_FILE; then
    echo "[$(date)] IRQBalance disabled" >> $LOG_FILE
else
    echo "[$(date)] WARN: Failed to disable IRQBalance (may not be installed)" >> $LOG_FILE
fi

# 4. Network optimizations
echo "[$(date)] Applying network optimizations..." >> $LOG_FILE

# Get primary network interface
IFACE=$(ip route get 8.8.8.8 | grep -oP 'dev \\K\\S+' | head -1)
if [ -n "$IFACE" ]; then
    echo "[$(date)] Primary interface: $IFACE" >> $LOG_FILE
    
    # Set IRQ affinity to all CPUs for network interface
    for irq in $(grep "$IFACE" /proc/interrupts | awk '{print $1}' | tr -d ':'); do
        echo ff > /proc/irq/$irq/smp_affinity 2>/dev/null || true
    done
fi

# Apply network settings
NET_SETTINGS=(
    "net.core.busy_poll=50"
    "net.core.busy_read=50"
    "net.core.netdev_max_backlog=5000"
    "net.ipv4.tcp_low_latency=1"
    "net.ipv4.tcp_timestamps=0"
    "net.ipv4.tcp_sack=0"
    "net.ipv4.tcp_no_metrics_save=1"
    "net.core.rmem_max=134217728"
    "net.core.wmem_max=134217728"
    "net.ipv4.tcp_rmem=4096 87380 134217728"
    "net.ipv4.tcp_wmem=4096 65536 134217728"
)

for setting in "${NET_SETTINGS[@]}"; do
    if sysctl -w "$setting" 2>&1 | tee -a $LOG_FILE; then
        echo "[$(date)] Applied: $setting" >> $LOG_FILE
    else
        echo "[$(date)] WARN: Failed to apply $setting" >> $LOG_FILE
        SUCCESS=false
    fi
done

# 5. Set tuned profile to network-latency
echo "[$(date)] Setting tuned profile..." >> $LOG_FILE
if command -v tuned-adm &> /dev/null; then
    if tuned-adm profile network-latency 2>&1 | tee -a $LOG_FILE; then
        echo "[$(date)] Applied tuned network-latency profile" >> $LOG_FILE
    else
        echo "[$(date)] WARN: Failed to apply tuned profile" >> $LOG_FILE
    fi
fi

# 6. Additional optimizations
# Disable transparent huge pages for consistent latency
echo never > /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
echo never > /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || true

# Set swappiness to 0 (avoid swapping)
echo 0 > /proc/sys/vm/swappiness 2>/dev/null || true

# Create marker file to indicate optimizations were applied
touch /etc/dc-machine-optimized

# Final status
if [ "$SUCCESS" = true ]; then
    echo "[$(date)] System optimization completed successfully" >> $LOG_FILE
    echo "[Machine Optimization] System optimization complete!" >> $LOG_FILE
else
    echo "[$(date)] System optimization completed with some warnings" >> $LOG_FILE
    echo "[Machine Optimization] System optimization completed with warnings (check $LOG_FILE)" >> $LOG_FILE
fi

# Log instance information
echo "[$(date)] Instance information:" >> $LOG_FILE
echo "  Instance ID: $(ec2-metadata --instance-id | cut -d' ' -f2)" >> $LOG_FILE 2>/dev/null || true
echo "  Instance Type: $(ec2-metadata --instance-type | cut -d' ' -f2)" >> $LOG_FILE 2>/dev/null || true
echo "  Availability Zone: $(ec2-metadata --availability-zone | cut -d' ' -f2)" >> $LOG_FILE 2>/dev/null || true

echo "[$(date)] DC Machine optimization script completed" >> $LOG_FILE
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