"""Logging and error-handling instrumentation, built on loguru.

:func:`logged` is the core decorator: it wraps a callable so each invocation is
traced and any exception is logged with a full traceback. By default the
exception is **re-raised** - a try/except that silently swallowed every error
would turn bugs into wrong results, so swallowing is opt-in (``reraise=False``).

:func:`log_methods` applies :func:`logged` across a class; :func:`instrument_module`
across a whole module, so "instrument every function" is one call, not one
decorator per ``def``.

Following loguru's convention for libraries, ssis2sql's logger is *disabled* on
import - importing the package emits nothing. An application turns logging on
with :func:`configure_logging` (the CLI does this from its ``-v`` flag).
"""
from __future__ import annotations

import functools
import inspect
import sys
import time

from loguru import logger

# Stay silent until the host application opts in (loguru's library convention).
logger.disable("ssis2sql")

__all__ = ["logger", "configure_logging", "logged", "log_methods", "instrument_module"]

_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | "
    "<level>{level: <7}</level> | "
    "<level>{message}</level>"
)


def configure_logging(
    level: str = "INFO",
    sink=None,
    *,
    diagnose: bool = False,
    backtrace: bool = True,
):
    """Enable ssis2sql logging and route it to ``sink`` (default: ``stderr``).

    ``diagnose`` controls loguru's variable-value annotation inside tracebacks;
    it is off by default because those values are verbose and may be sensitive.
    """
    logger.remove()
    logger.add(
        sink if sink is not None else sys.stderr,
        level=level.upper(),
        backtrace=backtrace,
        diagnose=diagnose,
        format=_FORMAT,
    )
    logger.enable("ssis2sql")
    return logger


def logged(func=None, *, level: str = "DEBUG", reraise: bool = True):
    """Decorator: trace a call, and log any exception with its traceback.

    Entry and successful exit are logged at ``level`` (DEBUG by default, so they
    are silent unless logging is turned up). An exception is logged at ERROR
    with the traceback, then re-raised - unless ``reraise`` is False, in which
    case the wrapper logs and returns ``None``.

    Works bare (``@logged``) or parameterised (``@logged(level="INFO")``).
    """

    def decorate(fn):
        name = fn.__qualname__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            logger.log(level, "→ {}", name)
            start = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                logger.opt(exception=True).error(
                    "✗ {} failed after {:.2f} ms: {}", name, elapsed_ms, exc
                )
                if reraise:
                    raise
                return None
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            logger.log(level, "← {} ({:.2f} ms)", name, elapsed_ms)
            return result

        wrapper.__wrapped_by_logged__ = True
        return wrapper

    return decorate(func) if func is not None else decorate


def log_methods(cls=None, *, level: str = "DEBUG", reraise: bool = True):
    """Class decorator: apply :func:`logged` to every method the class defines.

    Dunder methods and inherited methods are left alone; ``staticmethod`` and
    ``classmethod`` descriptors are rewrapped with the descriptor preserved.
    """

    def decorate(klass):
        for attr_name, attr in list(vars(klass).items()):
            if attr_name.startswith("__"):
                continue
            if isinstance(attr, staticmethod):
                inner = logged(attr.__func__, level=level, reraise=reraise)
                setattr(klass, attr_name, staticmethod(inner))
            elif isinstance(attr, classmethod):
                inner = logged(attr.__func__, level=level, reraise=reraise)
                setattr(klass, attr_name, classmethod(inner))
            elif inspect.isfunction(attr):
                setattr(klass, attr_name, logged(attr, level=level, reraise=reraise))
        return klass

    return decorate(cls) if cls is not None else decorate


def instrument_module(
    module,
    *,
    level: str = "DEBUG",
    reraise: bool = True,
    include_private: bool = True,
) -> int:
    """Wrap every function *defined in* ``module`` with :func:`logged`.

    Functions imported from elsewhere are skipped, and an already-instrumented
    function is not double-wrapped. Returns the count instrumented. Drop this at
    the foot of a module to trace all of its functions::

        from ssis2sql.observability import instrument_module
        instrument_module(sys.modules[__name__])
    """
    count = 0
    for attr_name, obj in list(vars(module).items()):
        if not inspect.isfunction(obj):
            continue
        if obj.__module__ != module.__name__:
            continue
        if getattr(obj, "__wrapped_by_logged__", False):
            continue
        if not include_private and attr_name.startswith("_"):
            continue
        setattr(module, attr_name, logged(obj, level=level, reraise=reraise))
        count += 1
    return count
