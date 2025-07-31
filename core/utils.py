"""Shared utilities for the latency finder."""

import os
import datetime
from typing import Tuple
from .constants import UTC_PLUS_8, LOG_DATE_FORMAT


def get_current_timestamp() -> str:
    """Get current timestamp in UTC+8 timezone."""
    return datetime.datetime.now(UTC_PLUS_8).isoformat(timespec="seconds")


def get_run_timestamp() -> str:
    """Get run timestamp in YYYYMMDDHHMMSS format using UTC+8 timezone."""
    return datetime.datetime.now(UTC_PLUS_8).strftime("%Y%m%d%H%M%S")


def get_log_file_paths(report_dir: str, median_threshold: int, best_threshold: int, 
                      run_timestamp: str) -> Tuple[str, str, str]:
    """Generate log file paths for current run.
    
    Args:
        report_dir: Directory for report files
        median_threshold: Median latency threshold in microseconds
        best_threshold: Best latency threshold in microseconds
        run_timestamp: UTC+8 timestamp string in YYYYMMDDHHMMSS format
        
    Returns:
        Tuple of (summary_jsonl_file, text_file, detailed_jsonl_file)
    """
    base_name = f"latency_{median_threshold}-{best_threshold}_{run_timestamp}"
    
    summary_jsonl_file = os.path.join(report_dir, f"{base_name}.jsonl")
    text_file = os.path.join(report_dir, f"{base_name}.txt")
    detailed_jsonl_file = os.path.join(report_dir, f"latency_detailed_{median_threshold}-{best_threshold}_{run_timestamp}.jsonl")
    
    return summary_jsonl_file, text_file, detailed_jsonl_file


def ensure_directory_exists(directory: str) -> None:
    """Ensure directory exists, create if necessary."""
    os.makedirs(directory, exist_ok=True)


def format_domain_short(domain: str) -> str:
    """Get short form of domain name."""
    return domain.replace(".binance.com", "")