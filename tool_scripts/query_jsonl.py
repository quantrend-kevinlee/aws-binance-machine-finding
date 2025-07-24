#!/usr/bin/env python3
"""
Query and analyze JSONL latency logs
"""
import json
import sys
from glob import glob
from collections import defaultdict

def load_jsonl(file_path):
    """Load all records from a JSONL file"""
    records = []
    with open(file_path, 'r') as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records

def query_by_domain(records, domain):
    """Find all records that tested a specific domain"""
    results = []
    for record in records:
        if domain in record.get("domains", {}):
            results.append(record)
    return results

def find_best_latencies(records):
    """Find best latencies for each domain across all records"""
    best_by_domain = defaultdict(lambda: {"median": float("inf"), "best": float("inf")})
    
    for record in records:
        for domain, stats in record.get("domains", {}).items():
            if stats["median"] < best_by_domain[domain]["median"]:
                best_by_domain[domain]["median"] = stats["median"]
                best_by_domain[domain]["median_record"] = record
                
            if stats["best"] < best_by_domain[domain]["best"]:
                best_by_domain[domain]["best"] = stats["best"]
                best_by_domain[domain]["best_record"] = record
    
    return dict(best_by_domain)

def query_by_instance_type(records, instance_type):
    """Find all records for a specific instance type"""
    results = []
    for record in records:
        if record.get("instance_type") == instance_type:
            results.append(record)
    return results

def print_instance_type_stats(records, instance_type):
    """Print statistics for a specific instance type"""
    filtered_records = query_by_instance_type(records, instance_type)
    
    if not filtered_records:
        print(f"No records found for instance type: {instance_type}")
        return
    
    print(f"Instance Type: {instance_type}")
    print(f"Total tests: {len(filtered_records)}")
    
    # Calculate pass rate
    passed = sum(1 for r in filtered_records if r.get("passed", False))
    print(f"Pass rate: {passed}/{len(filtered_records)} ({passed/len(filtered_records)*100:.1f}%)")
    
    # Collect all latencies by domain
    domain_latencies = defaultdict(lambda: {"median": [], "best": []})
    for record in filtered_records:
        for domain, stats in record.get("domains", {}).items():
            domain_latencies[domain]["median"].append(stats["median"])
            domain_latencies[domain]["best"].append(stats["best"])
    
    # Print statistics by domain
    print("\nLatency statistics by domain:")
    for domain in sorted(domain_latencies.keys()):
        medians = domain_latencies[domain]["median"]
        bests = domain_latencies[domain]["best"]
        
        print(f"\n  {domain}:")
        print(f"    Median latencies:")
        print(f"      Min: {min(medians):.2f}µs")
        print(f"      Max: {max(medians):.2f}µs")
        print(f"      Avg: {sum(medians)/len(medians):.2f}µs")
        print(f"    Best latencies:")
        print(f"      Min: {min(bests):.2f}µs")
        print(f"      Max: {max(bests):.2f}µs")
        print(f"      Avg: {sum(bests)/len(bests):.2f}µs")
    
    # Find best performers for this instance type
    print(f"\nBest performing instances for {instance_type}:")
    best = find_best_latencies(filtered_records)
    for domain, stats in sorted(best.items()):
        print(f"\n  {domain}:")
        if "median_record" in stats:
            rec = stats["median_record"]
            print(f"    Best median: {stats['median']:.2f}µs")
            print(f"      Instance: {rec['instance_id']}")
            print(f"      Time: {rec['timestamp']}")
        if "best_record" in stats:
            rec = stats["best_record"]
            print(f"    Best single: {stats['best']:.2f}µs")
            print(f"      Instance: {rec['instance_id']}")
            print(f"      Time: {rec['timestamp']}")

def print_summary(records):
    """Print summary statistics"""
    print(f"Total records: {len(records)}")
    
    # Count instances by type
    instance_types = defaultdict(int)
    for record in records:
        instance_types[record["instance_type"]] += 1
    
    print("\nInstances by type:")
    for itype, count in sorted(instance_types.items()):
        print(f"  {itype}: {count}")
    
    # Find all domains tested
    all_domains = set()
    for record in records:
        all_domains.update(record.get("domains", {}).keys())
    
    print(f"\nDomains tested: {len(all_domains)}")
    for domain in sorted(all_domains):
        print(f"  - {domain}")
    
    # Find best latencies
    print("\nBest latencies by domain:")
    best = find_best_latencies(records)
    for domain, stats in sorted(best.items()):
        print(f"\n  {domain}:")
        if "median_record" in stats:
            rec = stats["median_record"]
            print(f"    Best median: {stats['median']:.2f}µs")
            print(f"      Instance: {rec['instance_id']} ({rec['instance_type']})")
            print(f"      Time: {rec['timestamp']}")
        if "best_record" in stats:
            rec = stats["best_record"]
            print(f"    Best single: {stats['best']:.2f}µs")
            print(f"      Instance: {rec['instance_id']} ({rec['instance_type']})")
            print(f"      Time: {rec['timestamp']}")

def main():
    """Main query interface"""
    if len(sys.argv) < 2:
        print("Usage: python query_jsonl.py <command> [args]")
        print("\nCommands:")
        print("  summary <file>           - Show summary statistics")
        print("  domain <file> <domain>   - Query by domain")
        print("  best <file>             - Show best latencies")
        print("  instance-type <type>     - Analyze all JSONL files for specific instance type")
        print("  instance-best <type>     - Show only best results for instance type")
        print("  all                     - Analyze all JSONL files")
        return
    
    command = sys.argv[1]
    
    if command == "all":
        # Analyze all JSONL files
        import os
        reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
        pattern = os.path.join(reports_dir, "latency_log_*.jsonl")
        files = sorted(glob(pattern))
        all_records = []
        for file in files:
            print(f"\nLoading {file}...")
            records = load_jsonl(file)
            all_records.extend(records)
        print_summary(all_records)
        
    elif command == "instance-type" and len(sys.argv) >= 3:
        # Analyze specific instance type across all files
        instance_type = sys.argv[2]
        import os
        reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
        pattern = os.path.join(reports_dir, "latency_log_*.jsonl")
        files = sorted(glob(pattern))
        all_records = []
        for file in files:
            records = load_jsonl(file)
            all_records.extend(records)
        print_instance_type_stats(all_records, instance_type)
        
    elif command == "instance-best" and len(sys.argv) >= 3:
        # Show only best results for instance type
        instance_type = sys.argv[2]
        import os
        reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
        pattern = os.path.join(reports_dir, "latency_log_*.jsonl")
        files = sorted(glob(pattern))
        all_records = []
        for file in files:
            records = load_jsonl(file)
            all_records.extend(records)
        
        filtered_records = query_by_instance_type(all_records, instance_type)
        if not filtered_records:
            print(f"No records found for instance type: {instance_type}")
            return
            
        print(f"Best latencies for {instance_type}:")
        best = find_best_latencies(filtered_records)
        for domain, stats in sorted(best.items()):
            print(f"\n{domain}:")
            print(f"  Best median: {stats['median']:.2f}µs")
            if "median_record" in stats:
                print(f"    Instance: {stats['median_record']['instance_id']}")
            print(f"  Best single: {stats['best']:.2f}µs")
            if "best_record" in stats:
                print(f"    Instance: {stats['best_record']['instance_id']}")
        
    elif command == "summary" and len(sys.argv) >= 3:
        records = load_jsonl(sys.argv[2])
        print_summary(records)
        
    elif command == "domain" and len(sys.argv) >= 4:
        records = load_jsonl(sys.argv[2])
        domain = sys.argv[3]
        results = query_by_domain(records, domain)
        print(f"Found {len(results)} records testing {domain}")
        for rec in results:
            stats = rec["domains"][domain]
            print(f"  {rec['timestamp']} - {rec['instance_id']} ({rec['instance_type']})")
            print(f"    Median: {stats['median']:.2f}µs ({stats['median_ip']})")
            print(f"    Best: {stats['best']:.2f}µs ({stats['best_ip']})")
            
    elif command == "best" and len(sys.argv) >= 3:
        records = load_jsonl(sys.argv[2])
        best = find_best_latencies(records)
        for domain, stats in sorted(best.items()):
            print(f"\n{domain}:")
            print(f"  Best median: {stats['median']:.2f}µs")
            print(f"  Best single: {stats['best']:.2f}µs")

if __name__ == "__main__":
    main()