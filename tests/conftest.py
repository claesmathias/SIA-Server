"""Pytest configuration — adds the project root to sys.path."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
