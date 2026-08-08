#!/usr/bin/env python3
"""
Visitor Geofencing Security System — backwards-compatible entry point.

The implementation now lives in the `geofencing/` package (see README for
the module layout). This file is kept so existing instructions and scripts
that run `python Geofencing.py` continue to work unchanged.
"""
from demo import _demo

if __name__ == "__main__":
    _demo()
