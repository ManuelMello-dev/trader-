"""Pytest configuration: add repo root to sys.path for all test modules."""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
