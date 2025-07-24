"""Shared utilities for DC Machine."""

import os
import datetime
from typing import Tuple
from .constants import UTC_PLUS_8, LOG_DATE_FORMAT


def get_current_timestamp() -> str:
    """Get current timestamp in UTC+8 timezone."""
    return datetime.datetime.now(UTC_PLUS_8).isoformat(timespec="seconds")


def get_log_file_paths(report_dir: str) -> Tuple[str, str, str]:
    """Generate log file paths for current date.
    
    Args:
        report_dir: Directory for report files
        
    Returns:
        Tuple of (jsonl_file, text_file, champion_log_file)
    """
    today = datetime.date.today()
    date_str = today.strftime(LOG_DATE_FORMAT)
    
    jsonl_file = os.path.join(report_dir, f"latency_log_{date_str}.jsonl")
    text_file = os.path.join(report_dir, f"latency_log_{date_str}.txt")
    champion_log_file = os.path.join(report_dir, f"champion_log_{date_str}.txt")
    
    return jsonl_file, text_file, champion_log_file


def ensure_directory_exists(directory: str) -> None:
    """Ensure directory exists, create if necessary."""
    os.makedirs(directory, exist_ok=True)


def format_domain_short(domain: str) -> str:
    """Get short form of domain name."""
    return domain.replace(".binance.com", "")