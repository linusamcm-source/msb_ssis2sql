"""Lookup transpiler.

An SSIS Lookup enriches each row by matching it against a reference set. In
SQL that is a JOIN:

* the reference query becomes its own CTE;
* the matched output is the upstream relation LEFT JOINed to that CTE, with the
  copied reference columns appended;
* a no-match output, if present, is the anti-join (``reference key IS NULL``).

LEFT JOIN is emitted by default. SSIS lookups configured to *fail* on a missing
match are closer to an INNER JOIN - a warning flags this so it can be tightened.
"""
from __future__ import annotations

from ..model import Component, ComponentKind, Port
from ..relation import RelColumn, Relation
from .base import merge_column, passthrough_columns, table_name, wrap_sql_command
from .context import BuildContext
from .registry import Transpiler, register


@register(ComponentKind.LOOKUP)
class LookupTranspiler(Transpiler):
    """Lookup: emitted as a LEFT JOIN against the reference set's CTE."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        upstream = self._require_upstream(ctx, component)
        if upstream is None:
            return

        match_output, nomatch_output = self._classify_outputs(ctx, component)
        if match_output is None:
            return

        join_pairs = self._join_keys(component)
        copy_cols = [
            (oc.name, (oc.properties.get("CopyFromReferenceColumn") or oc.name).strip())
            for oc in match_output.columns
        ]

        if not join_pairs:
            ctx.warn(
                f"lookup {component.name!r}: no join keys found (expected a "
                f"'JoinToReferenceColumn' property on the input columns) - emitted as a "
                f"pass-through without the lookup join; review required"
            )
            ctx.make_relation(
                component, match_output, passthrough_columns(ctx, upstream),
                ctx.from_clause(upstream), name_hint=component.name,
                depends_on=(upstream,),
            )
            return

        ref_relation = self._reference_cte(ctx, component, join_pairs, copy_cols)
        on_clause = " AND ".join(
            f"L.{ctx.quote(lk)} = R.{ctx.quote(rk)}" for lk, rk in join_pairs
        )
        from_sql = (
            f"FROM {ctx.quote(upstream.name)} AS L\n"
            f"LEFT JOIN {ctx.quote(ref_relation.name)} AS R ON {on_clause}"
        )
        ctx.warn(
            f"lookup {component.name!r}: emitted as a LEFT JOIN - if the SSIS lookup was "
            f"set to fail on no-match, change LEFT JOIN to INNER JOIN for exact equivalence"
        )

        self._emit_match(ctx, component, match_output, upstream, ref_relation, copy_cols, from_sql)
        if nomatch_output is not None:
            self._emit_nomatch(
                ctx, component, nomatch_output, upstream, ref_relation, join_pairs, from_sql
            )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _classify_outputs(
        ctx: BuildContext, component: Component
    ) -> tuple[Port | None, Port | None]:
        match_output = None
        nomatch_output = None
        for out in component.non_error_outputs():
            label = out.name.lower().replace("_", " ").replace("-", " ")
            if "no match" in label:
                nomatch_output = out
            elif match_output is None:
                match_output = out
        if match_output is None and nomatch_output is not None:
            match_output, nomatch_output = nomatch_output, None
        if match_output is None:
            ctx.warn(f"lookup {component.name!r} has no usable output - skipped")
        return match_output, nomatch_output

    @staticmethod
    def _join_keys(component: Component) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for inp in component.inputs:
            for ic in inp.columns:
                ref_col = ic.properties.get("JoinToReferenceColumn")
                if ref_col:
                    pairs.append((ic.name, ref_col.strip()))
        return pairs

    def _reference_cte(
        self,
        ctx: BuildContext,
        component: Component,
        join_pairs: list[tuple[str, str]],
        copy_cols: list[tuple[str, str]],
    ) -> Relation:
        ref_names: list[str] = []
        seen: set[str] = set()
        for _, rk in join_pairs:
            if rk.lower() not in seen:
                seen.add(rk.lower())
                ref_names.append(rk)
        for _, rc in copy_cols:
            if rc and rc.lower() not in seen:
                seen.add(rc.lower())
                ref_names.append(rc)

        projection = ",\n".join("    " + ctx.quote(n) for n in ref_names)
        derived = wrap_sql_command(ctx, component, "_ref")
        if derived is not None:
            body = f"SELECT\n{projection}\nFROM {derived}"
        else:
            table = table_name(component)
            if table:
                body = f"SELECT\n{projection}\nFROM {ctx.dialect.quote_qualified(table)}"
            else:
                ctx.warn(
                    f"lookup {component.name!r}: no reference query or table found - "
                    f"emitted a placeholder reference set"
                )
                body = f"SELECT\n{projection}\nFROM /* lookup reference table */ _ref"

        ref_columns = [RelColumn(n, ctx.quote(n)) for n in ref_names]
        return ctx.emit_internal_cte(
            component, ref_columns, body, name_hint=f"{component.name}_Ref"
        )

    def _emit_match(
        self,
        ctx: BuildContext,
        component: Component,
        match_output: Port,
        upstream: Relation,
        ref_relation: Relation,
        copy_cols: list[tuple[str, str]],
        from_sql: str,
    ) -> None:
        columns = passthrough_columns(ctx, upstream, "L")
        index = {c.name.lower(): i for i, c in enumerate(columns)}
        for out_name, ref_name in copy_cols:
            if out_name.lower() in index:
                ctx.warn(
                    f"lookup {component.name!r}: reference column [{out_name}] collides with "
                    f"an upstream column - the reference value is used"
                )
            merge_column(columns, index, RelColumn(out_name, f"R.{ctx.quote(ref_name)}"))
        ctx.make_relation(
            component, match_output, columns, from_sql,
            name_hint=f"{component.name}_Match", depends_on=(upstream, ref_relation),
        )

    def _emit_nomatch(
        self,
        ctx: BuildContext,
        component: Component,
        nomatch_output: Port,
        upstream: Relation,
        ref_relation: Relation,
        join_pairs: list[tuple[str, str]],
        from_sql: str,
    ) -> None:
        columns = passthrough_columns(ctx, upstream, "L")
        first_ref = join_pairs[0][1]
        ctx.make_relation(
            component, nomatch_output, columns, from_sql,
            where=f"R.{ctx.quote(first_ref)} IS NULL",
            name_hint=f"{component.name}_NoMatch", depends_on=(upstream, ref_relation),
        )
