#!/usr/bin/env python3
"""
Test if a given IP belongs to fstream-mm.binance.com by comparing WebSocket responses.

This script connects to both the provided IP and the fstream-mm.binance.com domain,
then compares their WebSocket responses to verify if they serve the same data.

Usage: python3 test_ip_for_is_fstream.py <IP_ADDRESS>
Example: python3 test_ip_for_is_fstream.py 52.195.47.229
"""

import asyncio
import json
import websockets
import ssl
import sys

# Configuration
BINANCE_FUTURES_DOMAIN = "fstream-mm.binance.com"
BINANCE_FUTURES_PORT = 443
SYMBOL = "btcusdt"  # lowercase for Binance

async def compare_connections(test_ip):
    """Compare WebSocket connections between IP and domain"""
    
    stream_types = [
        ("bookTicker", f"/ws/{SYMBOL}@bookTicker"),
    ]
    
    for stream_name, stream_path in stream_types:
        print(f"\n{'='*80}")
        print(f"Comparing {stream_name} stream for IP {test_ip}")
        print(f"{'='*80}")
        
        # URLs for IP and domain
        ip_url = f"wss://{test_ip}:{BINANCE_FUTURES_PORT}{stream_path}"
        domain_url = f"wss://{BINANCE_FUTURES_DOMAIN}:{BINANCE_FUTURES_PORT}{stream_path}"
        
        # SSL contexts
        ip_ssl_context = ssl.create_default_context()
        ip_ssl_context.check_hostname = False
        
        domain_ssl_context = ssl.create_default_context()
        
        try:
            # Connect to both simultaneously with close timeout
            async with websockets.connect(
                ip_url, ssl=ip_ssl_context, close_timeout=1
            ) as ip_ws, websockets.connect(
                domain_url, ssl=domain_ssl_context, close_timeout=1
            ) as domain_ws:
                
                print(f"✓ Both connections established")
                print(f"  IP connection: {ip_ws.remote_address}")
                print(f"  Domain connection: {domain_ws.remote_address}")
                
                # Collect messages from both
                print(f"\nCollecting 3 messages from each connection...")
                
                for i in range(3):
                    # Get message from IP connection
                    ip_msg = await asyncio.wait_for(ip_ws.recv(), timeout=5.0)
                    ip_data = json.loads(ip_msg)
                    
                    # Get message from domain connection
                    domain_msg = await asyncio.wait_for(domain_ws.recv(), timeout=5.0)
                    domain_data = json.loads(domain_msg)
                    
                    print(f"\n  Message {i+1}:")
                    print(f"  IP data:     {json.dumps(ip_data, sort_keys=True)[:150]}...")
                    print(f"  Domain data: {json.dumps(domain_data, sort_keys=True)[:150]}...")
                    
                    # Compare structure
                    ip_keys = set(ip_data.keys())
                    domain_keys = set(domain_data.keys())
                    
                    if ip_keys == domain_keys:
                        print(f"  ✓ Same data structure (keys: {', '.join(sorted(ip_keys))})")
                    else:
                        print(f"  ✗ Different data structure!")
                        print(f"    IP keys: {ip_keys}")
                        print(f"    Domain keys: {domain_keys}")
                    
                    # Verify this is the expected data type
                    if stream_name == "bookTicker" and 'e' in ip_data and ip_data['e'] == 'bookTicker':
                        if ip_data.get('s') == domain_data.get('s'):
                            print(f"  ✓ Same symbol: {ip_data.get('s')}")
                        else:
                            print(f"  ✗ Different symbols: IP={ip_data.get('s')}, Domain={domain_data.get('s')}")
                            
        except Exception as e:
            print(f"✗ Connection failed: {type(e).__name__}: {str(e)}")

async def main():
    """Main test function"""
    if len(sys.argv) != 2:
        print("Usage: python3 test_ip_for_is_fstream.py <IP_ADDRESS>")
        print("Example: python3 test_ip_for_is_fstream.py 52.195.47.229")
        sys.exit(1)
    
    test_ip = sys.argv[1]
    
    print("Binance Futures WebSocket IP vs Domain Comparison")
    print("=" * 80)
    print(f"Testing IP: {test_ip}")
    print(f"Domain: {BINANCE_FUTURES_DOMAIN}")
    print(f"Symbol: {SYMBOL}")
    print("=" * 80)
    
    await compare_connections(test_ip)
    
    print("\n✓ Comparison completed!")

if __name__ == "__main__":
    asyncio.run(main())