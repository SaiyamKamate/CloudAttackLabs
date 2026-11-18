"""Telemetry: collect a target's logs, normalize them into events, store them.

The telemetry source is behind a small interface (source.py) so Docker logs can
be swapped for Loki later without touching normalization or detection.
"""
