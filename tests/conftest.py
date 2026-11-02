"""Shared fixtures for the sketchDiy core test suite."""

import pytest

from core.document import Document


@pytest.fixture
def doc():
    """A fresh, empty document for each test."""
    return Document()
