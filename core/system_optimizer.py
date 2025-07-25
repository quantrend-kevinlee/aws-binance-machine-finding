"""System optimization module for low-latency network measurements."""

import os
import subprocess
from typing import List, Tuple, Optional


class SystemOptimizer:
    """Optimizes system settings for consistent low-latency measurements."""
    
    def __init__(self):
        """Initialize system optimizer."""
        self.optimizations_applied = []
        self.errors = []
    
    def optimize_cpu(self) -> bool:
        """Optimize CPU settings for low latency.
        
        Returns:
            True if successful, False otherwise
        """
        success = True
        
        # 1. Disable CPU idle states (C-states)
        try:
            cpu_count = os.cpu_count() or 1
            for cpu in range(cpu_count):
                for state in range(10):  # Most systems have < 10 C-states
                    path = f"/sys/devices/system/cpu/cpu{cpu}/cpuidle/state{state}/disable"
                    if os.path.exists(path):
                        with open(path, 'w') as f:
                            f.write('1')
            self.optimizations_applied.append("Disabled CPU C-states")
        except Exception as e:
            self.errors.append(f"Failed to disable C-states: {e}")
            success = False
        
        # 2. Set CPU frequency governor to performance
        try:
            result = subprocess.run(
                ['cpupower', 'frequency-set', '-g', 'performance'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                self.optimizations_applied.append("Set CPU governor to performance")
            else:
                # Try alternative method
                for cpu in range(os.cpu_count() or 1):
                    gov_path = f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_governor"
                    if os.path.exists(gov_path):
                        with open(gov_path, 'w') as f:
                            f.write('performance')
                self.optimizations_applied.append("Set CPU governor to performance (alt method)")
        except Exception as e:
            self.errors.append(f"Failed to set CPU governor: {e}")
            success = False
        
        # 3. Disable irqbalance
        try:
            subprocess.run(['systemctl', 'stop', 'irqbalance'], capture_output=True)
            subprocess.run(['systemctl', 'disable', 'irqbalance'], capture_output=True)
            self.optimizations_applied.append("Disabled irqbalance")
        except Exception as e:
            self.errors.append(f"Failed to disable irqbalance: {e}")
            success = False
        
        return success
    
    def optimize_network(self) -> bool:
        """Optimize network settings for low latency.
        
        Returns:
            True if successful, False otherwise
        """
        success = True
        
        # Network sysctls for low latency
        sysctls = {
            'net.core.busy_poll': '50',
            'net.core.busy_read': '50',
            'net.core.netdev_max_backlog': '5000',
            'net.ipv4.tcp_low_latency': '1',
            'net.ipv4.tcp_timestamps': '0',
            'net.ipv4.tcp_sack': '0',
            'net.ipv4.tcp_no_metrics_save': '1',
            'net.core.rmem_max': '134217728',
            'net.core.wmem_max': '134217728',
        }
        
        for key, value in sysctls.items():
            try:
                result = subprocess.run(
                    ['sysctl', '-w', f'{key}={value}'],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    self.optimizations_applied.append(f"Set {key}={value}")
                else:
                    self.errors.append(f"Failed to set {key}: {result.stderr}")
                    success = False
            except Exception as e:
                self.errors.append(f"Failed to set {key}: {e}")
                success = False
        
        return success
    
    def setup_cpu_affinity(self, cpu_cores: List[int]) -> Tuple[bool, str]:
        """Set CPU affinity for network interrupts.
        
        Args:
            cpu_cores: List of CPU cores to use for network interrupts
            
        Returns:
            Tuple of (success, script_content) where script_content can be used
            to set process affinity
        """
        success = True
        
        # Find network interface
        try:
            result = subprocess.run(
                ['ip', 'route', 'get', '8.8.8.8'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                # Extract interface name
                parts = result.stdout.split()
                if 'dev' in parts:
                    iface = parts[parts.index('dev') + 1]
                    
                    # Find IRQs for this interface
                    irqs = []
                    with open('/proc/interrupts', 'r') as f:
                        for line in f:
                            if iface in line:
                                irq = line.split(':')[0].strip()
                                if irq.isdigit():
                                    irqs.append(int(irq))
                    
                    # Set IRQ affinity
                    for irq in irqs:
                        mask = sum(1 << cpu for cpu in cpu_cores)
                        try:
                            with open(f'/proc/irq/{irq}/smp_affinity', 'w') as f:
                                f.write(f'{mask:x}')
                            self.optimizations_applied.append(f"Set IRQ {irq} affinity to cores {cpu_cores}")
                        except Exception as e:
                            self.errors.append(f"Failed to set IRQ {irq} affinity: {e}")
                            success = False
        except Exception as e:
            self.errors.append(f"Failed to setup IRQ affinity: {e}")
            success = False
        
        # Generate taskset command for process affinity
        cpu_list = ','.join(str(cpu) for cpu in cpu_cores)
        taskset_cmd = f"taskset -c {cpu_list}"
        
        return success, taskset_cmd
    
    def check_ena_features(self) -> bool:
        """Check and enable ENA (Elastic Network Adapter) features.
        
        Returns:
            True if ENA features are available
        """
        try:
            # Check if ena module is loaded
            result = subprocess.run(
                ['lsmod'],
                capture_output=True,
                text=True
            )
            if 'ena' in result.stdout:
                # Try to enable ENA Express (if available)
                result = subprocess.run(
                    ['ethtool', '-K', 'eth0', 'tx-checksumming', 'on'],
                    capture_output=True
                )
                
                # Check for hardware timestamping
                result = subprocess.run(
                    ['ethtool', '-T', 'eth0'],
                    capture_output=True,
                    text=True
                )
                if 'hardware-transmit' in result.stdout:
                    self.optimizations_applied.append("ENA hardware features available")
                    return True
        except Exception as e:
            self.errors.append(f"Failed to check ENA features: {e}")
        
        return False
    
    def apply_tuned_profile(self) -> bool:
        """Apply tuned profile for network latency.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            result = subprocess.run(
                ['tuned-adm', 'profile', 'network-latency'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                self.optimizations_applied.append("Applied tuned network-latency profile")
                return True
            else:
                self.errors.append(f"Failed to apply tuned profile: {result.stderr}")
        except Exception as e:
            self.errors.append(f"Failed to apply tuned profile: {e}")
        
        return False
    
    def optimize_all(self, cpu_cores: Optional[List[int]] = None) -> dict:
        """Apply all optimizations.
        
        Args:
            cpu_cores: Optional list of CPU cores for IRQ affinity.
                      If None, uses cores 0 and 1.
        
        Returns:
            Dictionary with optimization results
        """
        if cpu_cores is None:
            cpu_cores = [0, 1]  # Default to first two cores
        
        # Apply optimizations
        cpu_success = self.optimize_cpu()
        network_success = self.optimize_network()
        irq_success, taskset_cmd = self.setup_cpu_affinity(cpu_cores)
        ena_available = self.check_ena_features()
        tuned_success = self.apply_tuned_profile()
        
        return {
            'success': cpu_success and network_success,
            'optimizations': self.optimizations_applied,
            'errors': self.errors,
            'taskset_cmd': taskset_cmd,
            'ena_available': ena_available,
            'recommendations': self._get_recommendations()
        }
    
    def _get_recommendations(self) -> List[str]:
        """Get recommendations based on optimization results.
        
        Returns:
            List of recommendation strings
        """
        recommendations = []
        
        if self.errors:
            recommendations.append("Some optimizations failed - check errors")
        
        if not any('ENA' in opt for opt in self.optimizations_applied):
            recommendations.append("Consider using Nitro instances (c5n, m5n) for ENA Express")
        
        recommendations.append(f"Run latency tests with CPU affinity: taskset -c 0,1 python3 test.py")
        recommendations.append("Monitor latency variance - should be < 5% after optimizations")
        
        return recommendations


def generate_optimization_script() -> str:
    """Generate a standalone bash script for system optimization.
    
    Returns:
        Bash script content
    """
    return """#!/bin/bash
# DC Machine System Optimization Script
# Run with: sudo bash optimize_system.sh

echo "Starting system optimization for low-latency measurements..."

# 1. CPU Optimizations
echo "Applying CPU optimizations..."
for cpu in /sys/devices/system/cpu/cpu*/cpuidle/state*/disable; do
    echo 1 > "$cpu" 2>/dev/null || true
done
cpupower frequency-set -g performance 2>/dev/null || \
    echo performance | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor >/dev/null 2>&1

# 2. Stop services that can cause jitter
systemctl stop irqbalance 2>/dev/null || true
systemctl disable irqbalance 2>/dev/null || true

# 3. Network Optimizations
echo "Applying network optimizations..."
sysctl -w net.core.busy_poll=50
sysctl -w net.core.busy_read=50
sysctl -w net.core.netdev_max_backlog=5000
sysctl -w net.ipv4.tcp_low_latency=1
sysctl -w net.ipv4.tcp_timestamps=0
sysctl -w net.ipv4.tcp_sack=0
sysctl -w net.ipv4.tcp_no_metrics_save=1
sysctl -w net.core.rmem_max=134217728
sysctl -w net.core.wmem_max=134217728

# 4. Apply tuned profile if available
if command -v tuned-adm &> /dev/null; then
    echo "Applying tuned network-latency profile..."
    tuned-adm profile network-latency
fi

# 5. Find network IRQs and pin to CPU 0,1
echo "Setting IRQ affinity..."
IFACE=$(ip route get 8.8.8.8 | grep -oP 'dev \\K\\S+' | head -1)
if [ -n "$IFACE" ]; then
    grep "$IFACE" /proc/interrupts | awk -F: '{print $1}' | while read irq; do
        echo 3 > /proc/irq/$irq/smp_affinity 2>/dev/null || true
    done
fi

echo "System optimization complete!"
echo "Recommended: Run latency tests with 'taskset -c 0,1 <command>'"
"""