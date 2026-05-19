# Plan — Three-tab restructure of the ssis2sql TUI

**Goal:** Replace the flat sidebar + single content-switcher layout in
`ssis2sql/tui.py` with a Textual **`TabbedContent`** of three tabs —
**Migration**, **Validation**, **Configuration**. Each tab has its own left
sub-sidebar of recipe buttons and its own content switcher. A new
**`ConfigPane`** (a `.env` / SQL Server editor) is added to the Configuration
tab. This also fixes the pre-existing bug where the non-scrolling `#sidebar`
clipped buttons off the bottom of the viewport (the sub-sidebars are
`VerticalScroll`).

## Decisions locked (from user)

| Decision | Choice |
|----------|--------|
| Top-level layout | Three tabs: Migration, Validation, Configuration |
| Within-tab navigation | Left sub-sidebar of buttons + content switcher (one per tab) |
| Configuration tab content | **Both** — maintenance recipes **and** a new `.env`/SQL Server editor pane |

## Recipe partition

Discovered recipes (`just --dump`): clean, convert, convert-samples,
convert-tree, cov, demo, inspect, install, install-validation, test, validate,
validate-cov, validate-static, validate-unit. (`opus`/`tui` already excluded by
`discover_recipes`; `validate`/`validate-static`/`validate-unit` already folded
into `ValidationPane` by the `_VALIDATION_LAYER_RECIPES` filter — unchanged.)

| Tab (`TabPane` id) | Sidebar buttons (top → bottom) | Panes |
|--------------------|--------------------------------|-------|
| Migration (`tab-migration`) | convert, convert-samples, convert-tree, demo, inspect | `DtsxPickerPane` (convert, inspect), `RecipePane` (convert-samples, demo), `ConvertTreePane` (convert-tree) |
| Validation (`tab-validation`) | **Validation**, validate-cov | `ValidationPane` (synthetic `validation`), `RecipePane` (validate-cov) |
| Configuration (`tab-configuration`) | **Configuration**, clean, cov, install, install-validation, test | `ConfigPane` (synthetic `config`), `RecipePane` (clean, cov, install, install-validation, test) |

Partition rule (robust to new recipes): a static `_MIGRATION_RECIPES` /
`_VALIDATION_TAB_RECIPES` membership set; **any unknown discovered recipe
defaults to the Configuration tab** (maintenance catch-all).

## Definition of done

1. The TUI shows a three-tab row — Migration, Validation, Configuration.
2. Each tab has a left sub-sidebar (scrollable) of its recipe buttons and a
   content switcher; clicking a sidebar button switches that tab's pane.
3. Switching tabs works (Textual `TabbedContent` native behaviour).
4. The Migration tab holds the conversion recipes; the Validation tab holds the
   `ValidationPane` + `validate-cov`; the Configuration tab holds the
   maintenance recipes + the new `ConfigPane`.
5. `ConfigPane` shows the four `MSSQL_*` settings pre-filled from `.env` (or
   blank when absent), with the password field masked, and a **Save** button
   that writes `.env`. A status line confirms the save.
6. The validation framework still runs from the Validation tab exactly as
   before (the three layer buttons, streamed Log, parsed summary).
7. No recipe button is clipped — the sub-sidebars scroll.
8. `just test` is green (existing TUI tests reworked for the tab structure,
   plus new tests).

## Files touched

| File | Change |
|------|--------|
| `ssis2sql/tui.py` | `read_env`/`write_env` helpers, `ConfigPane`, `TabbedContent` restructure of `compose`, per-tab routing, CSS. |
| `tests/test_tui.py` | Rework Pilot tests for the tab structure; new tests for `read_env`/`write_env`, `ConfigPane`, tab navigation. |
| `README.md` | Update the one-line TUI note for the tabbed layout. |

---

## Phase 0 — Allowed APIs (verified against `.repomix-textual.xml`)

Implementers **must** grep `.repomix-textual.xml` to confirm any Textual API
not already used in `tui.py`. Verified for this plan:

| API | Source in pack | Use for |
|-----|----------------|---------|
| `TabbedContent`, `TabPane` | `docs/widgets/tabbed_content.md`, `docs/examples/widgets/tabbed_content.py`, `src/textual/widgets/_tabbed_content.py` | The three-tab shell. |
| `with TabbedContent(initial="tab-migration"):` then `with TabPane("Title", id="tab-x"):` | tabbed_content.md "Switching tabs" / "Initial tab" | Compose. Tab switching is native — no custom handler. |
| `Input(password=True)` | already used (`Input`) — `password` is a standard `Input` arg; confirm in pack | Mask `MSSQL_SA_PASSWORD`. |
| `ContentSwitcher(id=…, initial=…)` | already used `tui.py` | One per tab — **must** have unique ids. |
| `VerticalScroll` | already used `tui.py` | Each sub-sidebar (fixes the clipping bug). |

Anti-patterns:
- **Do not** invent Textual APIs. If a call is not already in `tui.py`, grep
  `.repomix-textual.xml` and cite the file before using it.
- **Do not** add a custom tab-click handler — `TabbedContent` switches tabs
  itself. `on_button_pressed` handles only `nav-*` / `run-*` buttons.
- **Do not** import anything from `validation/` — unchanged subprocess-only
  discipline.
- **Do not** modify `_run_recipe` / `_run_validation` / `discover_recipes` /
  `find_repo_root` / `DtsxTree` bodies.

---

## Phase 1 — `read_env` / `write_env` helpers + `ConfigPane` (TDD)

### 1.1 `.env` helpers — pure, module-level, near `parse_pytest_summary`

```python
_MSSQL_KEYS = (
    "MSSQL_SERVER_ADDRESS",
    "MSSQL_SERVER_PORT",
    "MSSQL_SA_USERNAME",
    "MSSQL_SA_PASSWORD",
)

def read_env(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE .env file. Missing file → empty dict.

    Blank lines and ``#`` comments are skipped; only the first ``=`` splits.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values

def write_env(path: Path, values: dict[str, str]) -> None:
    """Write the four MSSQL_* keys as KEY=VALUE lines (others dropped)."""
    lines = ["# MSSQL connection parameters for the validation framework."]
    lines += [f"{k}={values.get(k, '')}" for k in _MSSQL_KEYS]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

**Failing tests first** (`tests/test_tui.py`, plain `def`):
- `read_env` on a written `.env` → dict of the 4 keys.
- `read_env` on a missing path → `{}`.
- `read_env` skips comments / blank lines.
- `write_env` then `read_env` round-trips the 4 values.
- `write_env` drops non-`MSSQL_*` keys.
- `write_env` does not crash on a partial dict (missing keys → empty value).

### 1.2 `ConfigPane` — new pane class, placed after `ValidationPane`

```python
class ConfigPane(VerticalScroll):
    """Configuration pane: edit the .env MSSQL connection settings."""

    def __init__(self, recipe: Recipe, repo_root: Path) -> None:
        super().__init__(id="pane-config")
        self._recipe = recipe
        self._env_path = repo_root / ".env"

    def compose(self) -> ComposeResult:
        values = read_env(self._env_path)
        yield Static("Edit the SQL Server connection used by the differential "
                     "validation layer. Saved to .env (gitignored).",
                     classes="pane-desc")
        for key in _MSSQL_KEYS:
            yield Static(key, classes="config-label")
            yield Input(
                value=values.get(key, ""),
                id=f"cfg-{key}",
                password=key.endswith("PASSWORD"),
            )
        yield Button("Save", id="run-config-save", variant="primary")
        yield Static("", id="config-status")
```

- `password=True` only for `MSSQL_SA_PASSWORD` — never render the password
  in plain text and never write it to a `Log`.
- Pane id `pane-config`; the synthetic recipe is named `config`.

### 1.3 Verification
- `.venv/bin/python -m pytest tests/test_tui.py -k "read_env or write_env"` — all pass.
- `.venv/bin/python -c "import ssis2sql.tui"` — clean.

---

## Phase 2 — `TabbedContent` restructure, `ConfigPane` wiring, routing, CSS

### 2.1 Partition constants (below `_VALIDATION_LAYERS`)

```python
_MIGRATION_RECIPES = frozenset(
    {"convert", "convert-samples", "convert-tree", "demo", "inspect"}
)
_VALIDATION_TAB_RECIPES = frozenset({"validate-cov"})  # plus synthetic "validation"
# (tab id suffix, tab title) in display order.
_TABS = (("migration", "Migration"),
         ("validation", "Validation"),
         ("configuration", "Configuration"))
```

### 2.2 `__init__` — partition recipes into three groups

After the existing `_VALIDATION_LAYER_RECIPES` filter, build:

```python
self._tab_recipes: dict[str, list[Recipe]] = {
    "migration": [], "validation": [], "configuration": [],
}
for r in recipes:                       # discovered, layer recipes already removed
    if r.name in _MIGRATION_RECIPES:
        self._tab_recipes["migration"].append(r)
    elif r.name in _VALIDATION_TAB_RECIPES:
        self._tab_recipes["validation"].append(r)
    else:
        self._tab_recipes["configuration"].append(r)
# Synthetic panes, first in their tab.
self._tab_recipes["validation"].insert(
    0, Recipe(name="validation", doc="Run the ssis2sql validation framework."))
self._tab_recipes["configuration"].insert(
    0, Recipe(name="config", doc="Edit the .env SQL Server settings."))
# recipe name -> tab id, for nav routing.
self._tab_of = {r.name: tab
                for tab, rs in self._tab_recipes.items() for r in rs}
```

### 2.3 `compose` — `TabbedContent` of three tabs

```python
def compose(self) -> ComposeResult:
    yield Header()
    with TabbedContent(initial="tab-migration"):
        for tab, title in _TABS:
            with TabPane(title, id=f"tab-{tab}"):
                recipes = self._tab_recipes[tab]
                with Horizontal():
                    with VerticalScroll(classes="tab-sidebar"):
                        for r in recipes:
                            yield Button(r.name if r.name not in ("validation", "config")
                                         else title,
                                         id=f"nav-{_slug(r.name)}")
                    initial = f"pane-{_slug(recipes[0].name)}" if recipes else None
                    with ContentSwitcher(id=f"content-{tab}", classes="tab-content",
                                         initial=initial):
                        for r in recipes:
                            yield self._build_pane(r)
    yield Footer()
```

- The synthetic `validation`/`config` buttons show the tab title as their
  label; all other buttons keep the recipe name.
- Each `ContentSwitcher` gets a **unique** id `content-<tab>`.

### 2.4 `_build_pane` — add the `ConfigPane` branch

```python
if recipe.name == "config":
    return ConfigPane(recipe, self._repo_root)
```
(placed before the existing `validation` branch.)

### 2.5 Routing — `on_button_pressed`

`nav-*` must target the **right** content switcher (there are three now):

```python
if bid.startswith("nav-"):
    recipe = bid[len("nav-"):]
    tab = self._tab_of.get(recipe)
    if tab:
        self.query_one(f"#content-{tab}", ContentSwitcher).current = f"pane-{recipe}"
    return
elif bid == "run-config-save":
    self._save_config()
    return
```
Keep the existing `run-convert-tree` / `_VALIDATION_LAYER_RECIPES` / generic
`run-` branches unchanged below this.

### 2.6 `_save_config` — collect inputs, write `.env`

```python
def _save_config(self) -> None:
    values = {k: self.query_one(f"#cfg-{k}", Input).value.strip()
              for k in _MSSQL_KEYS}
    write_env(self._repo_root / ".env", values)
    self.query_one("#config-status", Static).update(
        f"saved → {self._repo_root / '.env'}")
```
The status line names the path but **never** echoes the password value.

### 2.7 CSS — replace the flat `#sidebar`/`#content` rules

```
.tab-sidebar { dock: left; width: 28; background: $panel; }
.tab-sidebar Button { width: 100%; margin: 0 0 1 0; }
.tab-content { width: 1fr; padding: 1 2; }
.config-label { color: $text-muted; margin: 1 0 0 0; }
#config-status { margin: 1 0; color: $text-muted; }
```
Keep `.pane-desc`, `Log`, `#validation-buttons*`, `#validation-summary` rules.
Drop the old `#sidebar` / `#sidebar Button` / `#content` rules.

### 2.8 Verification
- `import ssis2sql.tui` clean; `grep -nE "import validation|from validation"` → 0.
- Headless probe: every `nav-*` button exists; each `#content-<tab>` switcher
  exists; `#nav-validation` is in the Validation tab and reachable.

---

## Phase 3 — Test rework + new tests

The flat-layout assumptions break: any test that queries `#sidebar`, the lone
`ContentSwitcher`, or assumes 12 sidebar buttons must be reworked.

### 3.1 Rework existing Pilot tests
- `test_app_compose_one_button_per_recipe` → assert the three tabs exist and
  each tab's sub-sidebar holds its expected buttons.
- `test_app_compose_no_button_for_excluded_recipes` → `opus`/`tui` still absent
  from every tab.
- `test_clicking_nav_button_switches_content_pane` → click into a tab, click a
  sub-sidebar button, assert that tab's `#content-<tab>` switched.
- `test_run_button_writes_to_log_and_exits`, the Story-3 picker tests, and the
  **five validation Pilot tests** → navigate to the owning tab first
  (`pilot.click("#--content-tab-tab-validation")` or set
  `TabbedContent.active`), then proceed. Confirm the tab-activation selector
  against `.repomix-textual.xml` before use.

### 3.2 New tests
- Tab presence/navigation: all three `TabPane`s exist; switching `active`
  shows the right pane.
- `ConfigPane`: the four `cfg-*` Inputs pre-fill from a `tmp_path/.env`;
  `MSSQL_SA_PASSWORD` Input has `password=True`.
- Save: type values, click `#run-config-save`, assert `.env` on disk contains
  them and `#config-status` updates.
- `ConfigPane` with no `.env` → Inputs blank, no crash.

### 3.3 Verification
- `.venv/bin/python -m pytest tests/test_tui.py` — all pass.
- No test shells out to a real `just`/`pytest` (hermetic `Popen` monkeypatch).

---

## Phase 4 — Verify & docs

- `just test` — full suite green.
- `just validate-static`, `just validate-unit` — still green (untouched).
- `git diff --stat` — only `ssis2sql/tui.py`, `tests/test_tui.py`, `README.md`,
  this plan file.
- Anti-pattern sweep on `tui.py`: 0 `import validation`, 1 `subprocess.run`,
  2 `subprocess.Popen`, 0 `pytest.main`.
- `README.md`: update the `## Validation` TUI note — the layers now live under
  the **Validation** tab (`just tui` → Validation tab).
- Manual smoke: `just tui` → all three tabs reachable, sub-sidebars scroll,
  ConfigPane loads/saves `.env`, validation layers still run.

## Out of scope

- No `.env` field validation (port-is-numeric, host reachability) — plain text.
- No per-tab keyboard shortcuts beyond `TabbedContent`'s native tab navigation.
- No new `just` recipes or pytest markers.
