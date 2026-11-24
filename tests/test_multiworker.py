"""Tests for multi-worker parallel synthesis configuration and auto-detection."""
import os
from unittest.mock import MagicMock, patch
import pytest

from scripts.generate_chapter_vid_voice import get_default_workers


def test_get_default_workers_env_override():
    with patch.dict(os.environ, {"AUDIOBOOK_WORKERS": "4"}):
        assert get_default_workers() == 4


def test_get_default_workers_cpu():
    with patch.dict(os.environ, {}, clear=True), patch("torch.cuda.is_available", return_value=False):
        assert get_default_workers() == 1


def test_get_default_workers_8gb_gpu():
    mock_prop = MagicMock()
    mock_prop.total_memory = 8 * (1024**3)  # 8GB VRAM (e.g. RTX 3070)
    with patch.dict(os.environ, {}, clear=True), \
         patch("torch.cuda.is_available", return_value=True), \
         patch("torch.cuda.get_device_properties", return_value=mock_prop):
        assert get_default_workers() == 1


def test_get_default_workers_16gb_gpu():
    mock_prop = MagicMock()
    mock_prop.total_memory = 16 * (1024**3)  # 16GB VRAM
    with patch.dict(os.environ, {}, clear=True), \
         patch("torch.cuda.is_available", return_value=True), \
         patch("torch.cuda.get_device_properties", return_value=mock_prop):
        assert get_default_workers() == 2


def test_get_default_workers_24gb_gpu():
    mock_prop = MagicMock()
    mock_prop.total_memory = 24 * (1024**3)  # 24GB VRAM (e.g. RTX 3090, RTX 4090)
    with patch.dict(os.environ, {}, clear=True), \
         patch("torch.cuda.is_available", return_value=True), \
         patch("torch.cuda.get_device_properties", return_value=mock_prop):
        assert get_default_workers() == 3
