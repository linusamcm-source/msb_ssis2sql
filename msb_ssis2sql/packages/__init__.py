"""SSIS package extraction package.

Connects to a SQL Server instance (Windows Integrated auth) and writes every
stored SSIS package to disk as a ``.dtsx`` file — the format
:mod:`msb_ssis2sql.batch` (``convert-tree``) consumes.

Two storage models are supported, selected by store type:

* ``msdb``   — the legacy package store (``msdb.dbo.sysssispackages``), whose
  ``packagedata`` column *is* the ``.dtsx`` payload.
* ``ssisdb`` — the SSIS catalog (``SSISDB.catalog.*``), whose packages live
  inside ``.ispac`` project archives fetched via ``catalog.get_project`` and
  unzipped to their ``.dtsx`` members.

``auto`` probes for the ``SSISDB`` database and picks the catalog when present.
"""
from __future__ import annotations

from .extractor import extract_packages
from .model import ExtractedPackage

__all__ = ["extract_packages", "ExtractedPackage"]
