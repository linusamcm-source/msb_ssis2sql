"""Tests for ``ssis2sql.transforms.registry``.

These cover the :class:`Transpiler` ABC, the :func:`register` decorator and
:func:`get_transpiler`. Importing ``ssis2sql.transforms`` (the package) has the
side effect of registering every real transpiler, so the real-kind lookups
below resolve against that populated registry.

The dummy-registration tests deliberately register against a throwaway local
``Enum`` rather than a real :class:`~ssis2sql.model.ComponentKind`, so the
shared ``_REGISTRY`` is never mutated and the full suite stays unaffected.
"""
from __future__ import annotations

from enum import Enum

import pytest

# Importing the package registers every real transpiler against the registry.
import ssis2sql.transforms  # noqa: F401
from ssis2sql.model import Component, ComponentKind
from ssis2sql.transforms.registry import Transpiler, get_transpiler, register


class _FakeKind(Enum):
    """Throwaway kinds used only for dummy registration - never real."""

    ALPHA = "alpha"
    BETA = "beta"
    GAMMA = "gamma"
    UNUSED = "unused"


# --------------------------------------------------------------------------- #
# Transpiler ABC
# --------------------------------------------------------------------------- #
def test_transpiler_is_abstract_and_cannot_be_instantiated():
    with pytest.raises(TypeError):
        Transpiler()


def test_transpiler_kinds_defaults_to_an_empty_tuple():
    assert Transpiler.kinds == ()


def test_transpiler_subclass_without_transpile_stays_abstract():
    class Incomplete(Transpiler):
        pass

    with pytest.raises(TypeError):
        Incomplete()


# --------------------------------------------------------------------------- #
# register decorator
# --------------------------------------------------------------------------- #
def test_register_sets_kinds_on_the_decorated_class():
    @register(_FakeKind.ALPHA)
    class AlphaDummy(Transpiler):
        def transpile(self, ctx, component):  # pragma: no cover - never run
            return None

    assert AlphaDummy.kinds == (_FakeKind.ALPHA,)


def test_register_sets_kinds_for_multiple_kinds():
    @register(_FakeKind.ALPHA, _FakeKind.BETA)
    class MultiDummy(Transpiler):
        def transpile(self, ctx, component):  # pragma: no cover - never run
            return None

    assert MultiDummy.kinds == (_FakeKind.ALPHA, _FakeKind.BETA)


def test_register_makes_the_kind_resolvable_via_get_transpiler():
    @register(_FakeKind.BETA)
    class BetaDummy(Transpiler):
        def transpile(self, ctx, component):  # pragma: no cover - never run
            return None

    resolved = get_transpiler(_FakeKind.BETA)
    assert isinstance(resolved, BetaDummy)
    assert isinstance(resolved, Transpiler)


def test_register_returns_a_usable_transpiler_class():
    calls: list[str] = []

    @register(_FakeKind.GAMMA)
    class GammaDummy(Transpiler):
        def transpile(self, ctx, component):
            calls.append(component.name)

    instance = get_transpiler(_FakeKind.GAMMA)
    instance.transpile(None, Component(name="MyScript"))
    assert calls == ["MyScript"]


# --------------------------------------------------------------------------- #
# get_transpiler
# --------------------------------------------------------------------------- #
def test_get_transpiler_returns_none_for_an_unregistered_kind():
    # _FakeKind.UNUSED is never passed to register().
    assert get_transpiler(_FakeKind.UNUSED) is None


def test_get_transpiler_returns_a_fresh_instance_each_call():
    @register(_FakeKind.GAMMA)
    class FreshDummy(Transpiler):
        def transpile(self, ctx, component):  # pragma: no cover - never run
            return None

    first = get_transpiler(_FakeKind.GAMMA)
    second = get_transpiler(_FakeKind.GAMMA)
    assert first is not second
    assert type(first) is type(second)


# --------------------------------------------------------------------------- #
# real transpilers registered by importing the package
# --------------------------------------------------------------------------- #
def test_get_transpiler_resolves_a_real_source_kind():
    transpiler = get_transpiler(ComponentKind.OLEDB_SOURCE)
    assert isinstance(transpiler, Transpiler)


def test_get_transpiler_resolves_a_real_destination_kind():
    transpiler = get_transpiler(ComponentKind.OLEDB_DESTINATION)
    assert isinstance(transpiler, Transpiler)


@pytest.mark.parametrize(
    "kind",
    [
        ComponentKind.DERIVED_COLUMN,
        ComponentKind.CONDITIONAL_SPLIT,
        ComponentKind.LOOKUP,
        ComponentKind.AGGREGATE,
        ComponentKind.UNION_ALL,
        ComponentKind.MULTICAST,
    ],
)
def test_get_transpiler_resolves_every_core_real_kind(kind):
    transpiler = get_transpiler(kind)
    assert isinstance(transpiler, Transpiler)
    assert kind in transpiler.kinds
