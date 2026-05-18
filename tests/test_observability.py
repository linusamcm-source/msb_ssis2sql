"""Tests for the loguru-based logging / error-handling instrumentation."""
from __future__ import annotations

import pytest

from ssis2sql.observability import (
    configure_logging,
    instrument_module,
    log_methods,
    logged,
    logger,
)


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


def test_log_methods_rewraps_classmethods():
    @log_methods
    class Registry:
        @classmethod
        def label(cls):
            return cls.__name__

    # The classmethod is still bound to the class after rewrapping.
    assert Registry.label() == "Registry"


def test_log_methods_rewraps_staticmethods():
    @log_methods
    class Maths:
        @staticmethod
        def double(n):
            return n * 2

    assert Maths.double(21) == 42


def test_instrument_module_wraps_functions_defined_in_the_module():
    import types

    module = types.ModuleType("fake_observability_target")

    def alpha():
        return "alpha"

    def beta():
        return "beta"

    alpha.__module__ = module.__name__
    beta.__module__ = module.__name__
    module.alpha = alpha
    module.beta = beta

    count = instrument_module(module)

    assert count == 2
    assert module.alpha() == "alpha"
    assert getattr(module.alpha, "__wrapped_by_logged__", False) is True


def test_instrument_module_skips_imported_private_and_already_wrapped():
    import types

    module = types.ModuleType("fake_observability_target_2")

    def local_fn():
        return 1

    def imported_fn():
        return 2

    def _private_fn():
        return 3

    local_fn.__module__ = module.__name__
    imported_fn.__module__ = "some.other.module"   # not defined here -> skipped
    _private_fn.__module__ = module.__name__
    module.local_fn = local_fn
    module.imported_fn = imported_fn
    module._private_fn = _private_fn

    # include_private=False also drops the underscore-prefixed function.
    count = instrument_module(module, include_private=False)

    assert count == 1
    # A second pass wraps nothing: every eligible function is already wrapped.
    assert instrument_module(module, include_private=False) == 0
