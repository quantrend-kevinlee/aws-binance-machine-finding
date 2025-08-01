#!/usr/bin/env python3
"""
Find AWS EC2 instances with low latency to Binance servers.

This is the main entry point for the latency finder. It uses a modular architecture
to orchestrate the process of launching instances, testing latency, and
finding qualified instances that meet the specified criteria.
"""

from core.config import Config
from core.orchestrator import Orchestrator


def main():
    """Main entry point."""
    # Load configuration
    config = Config()
    
    # Show IP assignment mode from config
    print(f"Using {'Elastic IPs (EIP)' if config.use_eip else 'auto-assigned public IPs'} for instances")
    print("="*60)
    
    # Create and run orchestrator
    orchestrator = Orchestrator(config)
    orchestrator.run()


if __name__ == "__main__":
    main()