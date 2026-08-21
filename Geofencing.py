#!/usr/bin/env python3
"""
Visitor Geofencing Security System — backwards-compatible entry point.

The implementation lives in the `geofencing/` package.
This file is kept so `python Geofencing.py` continues to work.
"""
import runpy

if __name__ == "__main__":
    runpy.run_path("demo.py", run_name="__main__")
