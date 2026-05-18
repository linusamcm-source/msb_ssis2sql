"""Unit tests for the ``validation`` framework's own modules.

These tests exercise the framework logic (config, comparison, ledger, etc.)
without requiring a live SQL Server connection.  Run them with::

    just validate-unit
"""
from __future__ import annotations
