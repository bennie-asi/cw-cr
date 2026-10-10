"""Backward-compatible command name for older local scripts.

New deployments should call ``initialize_data`` instead.
"""

from review.management.commands.bootstrap_data import Command

__all__ = ['Command']
