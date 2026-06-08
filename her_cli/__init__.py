"""
her CLI - Unified command-line interface for her Agent.

Provides subcommands for:
- her chat          - Interactive chat (same as ./her)
- her gateway       - Run gateway in foreground
- her gateway start - Start gateway service
- her gateway stop  - Stop gateway service
- her setup         - Interactive setup wizard
- her status        - Show status of all components
- her cron          - Manage cron jobs
"""

import os
import sys

__version__ = "0.16.0"
__release_date__ = "2026.6.5"
