"""The transpiler base class and the component registry.

A *transpiler* turns one pipeline component into SQL. Each one is registered
against one or more :class:`~msb_ssis2sql.model.ComponentKind` values via the
:func:`register` decorator, so adding support for a new component is a
self-contained file; the generator finds it through :func:`get_transpiler`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..observability import log_methods

if TYPE_CHECKING:
    from ..model import Component, ComponentKind
    from ..relation import Relation
    from .context import BuildContext


class Transpiler(ABC):
    """Base class for a component transpiler."""

    kinds: tuple[ComponentKind, ...] = ()

    @abstractmethod
    def transpile(self, ctx: BuildContext, component: Component) -> None:
        """Consume the component, registering its relations / sinks on ``ctx``."""
        raise NotImplementedError

    def _require_upstream(self, ctx: BuildContext, component: Component) -> Relation | None:
        """The relation feeding a single-input component, else ``None`` (warns).

        The shared guard for single-input transpilers: resolve the one upstream
        relation, or warn and return ``None`` so the caller bails in one line.
        """
        upstream = ctx.single_upstream(component)
        if upstream is None:
            ctx.warn(f"{component.kind.value} {component.name!r} is not fully connected - skipped")
        return upstream

    def _single_io(self, ctx: BuildContext, component: Component):
        """Upstream relation and the single output of a one-in/one-out transform.

        Warns and returns ``None`` when the component is not fully connected,
        which lets a transpiler bail with one guard line.
        """
        upstream = self._require_upstream(ctx, component)
        if upstream is None:
            return None
        outputs = component.non_error_outputs()
        if not outputs:
            ctx.warn(f"{component.kind.value} {component.name!r} has no output - skipped")
            return None
        return upstream, outputs[0]


_REGISTRY: dict[ComponentKind, type] = {}


def register(*kinds: ComponentKind):
    """Class decorator: bind a :class:`Transpiler` subclass to component kinds.

    The transpiler is also instrumented with :func:`log_methods`, so every one
    of its methods is traced and any failure is logged with a traceback.
    """

    def decorator(cls):
        cls.kinds = tuple(kinds)
        cls = log_methods(cls)
        for kind in kinds:
            _REGISTRY[kind] = cls
        return cls

    return decorator


def get_transpiler(kind: ComponentKind) -> Transpiler | None:
    """Instantiate the transpiler registered for ``kind``, or ``None``."""
    cls = _REGISTRY.get(kind)
    return cls() if cls is not None else None
