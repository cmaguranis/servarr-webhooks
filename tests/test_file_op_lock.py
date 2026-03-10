"""Tests for src/file_op_lock.py."""

import pytest

from src import file_op_lock


@pytest.fixture(autouse=True)
def _clear_active():
    """Ensure the shared set is clean before and after each test."""
    file_op_lock._active.clear()
    yield
    file_op_lock._active.clear()


def test_acquire_succeeds_when_free():
    assert file_op_lock.try_acquire("/media/foo.mkv") is True


def test_acquire_fails_when_held():
    file_op_lock.try_acquire("/media/foo.mkv")
    assert file_op_lock.try_acquire("/media/foo.mkv") is False


def test_release_allows_reacquire():
    file_op_lock.try_acquire("/media/foo.mkv")
    file_op_lock.release("/media/foo.mkv")
    assert file_op_lock.try_acquire("/media/foo.mkv") is True


def test_release_noop_when_not_held():
    file_op_lock.release("/media/foo.mkv")  # should not raise


def test_different_paths_independent():
    assert file_op_lock.try_acquire("/media/a.mkv") is True
    assert file_op_lock.try_acquire("/media/b.mkv") is True
