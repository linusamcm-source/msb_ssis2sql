"""Validation framework for differential testing of msb_ssis2sql-converted T-SQL.

This package proves that the T-SQL produced by ``msb_ssis2sql`` is
behaviour-equivalent to the SSIS package it was converted from, by running
both against identical seeded inputs and comparing their output tables.

Three layers, cheapest first:

* **Static** — SQL parse-validity and column-lineage checks (no database).
* **Execution** — SQL runs and produces the correct row count (SQL Server).
* **Differential** — output rows match the SSIS golden capture exactly,
  modulo the expected-divergence ledger (SQL Server + committed golden).
"""
from __future__ import annotations
