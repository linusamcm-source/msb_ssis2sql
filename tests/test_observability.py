"""Tests for the loguru-based logging / error-handling instrumentation."""
from __future__ import annotations

import pytest

from ssis2sql.observability import configure_logging, log_methods, logged, logger


@pytest.fixture
def captured():
    """Route ssis2sql logs into a list; restore the silent default afterwards."""
    messages: list[str] = []
    configure_logging(level="DEBUG", sink=messages.append)
    yield messages
    logger.remove()
    logger.disable("ssis2sql")


def test_returns_the_wrapped_value():
    @logged
    def add(a, b):
        return a + b

    assert add(2, 3) == 5


def test_reraises_by_default():
    @logged
    def boom():
        raise ValueError("blew up")

    with pytest.raises(ValueError, match="blew up"):
        boom()


def test_can_swallow_when_asked():
    @logged(reraise=False)
    def boom():
        raise RuntimeError("handled")

    assert boom() is None


def test_preserves_function_metadata():
    @logged
    def documented(value):
        """Original docstring."""
        return value

    assert documented.__name__ == "documented"
    assert documented.__doc__ == "Original docstring."


def test_failure_is_logged_with_traceback(captured):
    @logged(reraise=False)
    def failing_step():
        raise RuntimeError("captured failure")

    failing_step()

    assert any("captured failure" in m for m in captured)
    assert any("failing_step" in m for m in captured)
    assert any("RuntimeError" in m for m in captured)   # the traceback is included


def test_success_is_traced(captured):
    @logged
    def quick():
        return 1

    quick()

    assert any("→" in m and "quick" in m for m in captured)
    assert any("←" in m and "quick" in m for m in captured)


def test_log_methods_instruments_every_method():
    @log_methods
    class Worker:
        def run(self):
            return "ok"

        def fail(self):
            raise ValueError("method failure")

    worker = Worker()
    assert worker.run() == "ok"
    with pytest.raises(ValueError, match="method failure"):
        worker.fail()
