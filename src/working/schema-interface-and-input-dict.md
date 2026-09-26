# Schema interface and `InputDict`: study for Q15/Q16

Both deferred. This is the study behind them, trimmed to what a future implementer needs:
what `InputDict` is, why it's not a direct dependency today, the proposed pluggable-backend
shape, and the units plan. Read from `~/projects/darts/core/darts/core/`: `input_dict.py`,
`base_dparam.py`, `dict_linker.py`, `utils/dclick.py`, parts of `dtyping/`. `dvar`/`DVarInputDict`
are darts-specific and out of scope.

---

## What `InputDict` is

A dict-and-attribute-like config base class with a custom metaclass:

```python
class Knight(InputDict):
    age: int
    title: str = "Sir"
    weapon: str = Dfield(default="sword", description="...", validators=[RangeCheck(...)])
```

- Explicit three-state defaults: has a default / no default (required) / `NO_DEFAULT`
  ("optional, key simply absent unless set") — no v1-style `Optional[X]`-without-default trap.
- Unknown keys rejected on construction; validators and cross-field `validate()` hooks;
  read-only fields/objects; nested `InputDict`s as ordinary field types.
- `parse(dict)`: lenient coercion via a dynamically-built Pydantic model, then the class's own
  type check and validators. `cfg(delta=True)` dumps only non-default values.
- A CLI generator (`InputDict.cli()` → click, via `DictLinker`/`dclick`): dotted nested option
  names (`--foo.bar`, matching our own choice), per-field overrides (`CliHint`), kebab-case by
  default (opposite of our snake_case), fixed-`nargs` for vectors, repeated+index-aligned flags
  for lists of records. `dclick`'s layered-config engine tracks a **source per value** through
  the merge (our `--show-config` provenance, independently arrived at) but **concatenates**
  repeated options across layers, where we chose "later replaces."
- `BaseDParam` adds per-field provenance via a parallel auto-generated class — the idea (a
  `path -> source` side table) is what we want; the mechanism (a whole parallel class,
  mutable per-instance state) is heavier than our layering needs, since ours is a pure
  function of (defaults, files, CLI).

## Pros and cons as a tlaunch dependency

**In favor:** schema-as-interface already built and in production use; Pydantic is only its
*coercion backend* (real evidence a layered design works — see the proposal below); explicit
required/default/absent semantics; unknown-key rejection built in; `delta` dumps and
source-tracked layering are ready-made models for `--dump-config`; trivial introspection
(`__input_fields__` is a plain dict); units-aware types would be a genuine win for robotics.

**Against:**
1. **Not standalone.** Importing it pulls in `dtyping`/`dunits`, which need a compiled
   extension, plus an import-time hard dependency on a module literally named `pydantic2`
   (darts' own side-by-side Debian package, not a normal system's Pydantic). The
   `python3-darts-core` apt package supplies both — but isn't installable here (no candidate
   in this environment's apt), so nothing about actually running `InputDict` is tested; if it
   were installed, it pulls ~25 Python packages plus a native shared library (numpy, pandas,
   matplotlib, ipython, sympy, ...) — fine if every user is already a darts user, heavy otherwise.
2. **A second type system.** Its own `type_check`/`resolve_type` run alongside Pydantic's
   coercion on the same data — two engines that can disagree, one of them ours to maintain.
3. **Mutable, self-revalidating objects**; a launch config is build-once-then-read, where
   immutability is the better default (available via `make_read_only()`, but opt-in).
4. **Docstring-scraping for field descriptions** (numpydoc + regex), which the darts authors'
   own comments call brittle.
5. **Click, not argparse** — adopting the generator means adopting `dclick` (4350 lines) or
   rewriting it for argparse (our own walker is already ~170 lines).
6. **No JSON Schema output** — tables/asciidoc only; we want machine-readable `--schema`.
7. **Python ≥ 3.11** (`typing.Self`, `dataclass_transform`) — fine for Jazzy, not Humble (3.10).

**Net assessment:** a strong *reference design* and a plausible *third backend* someday, but a
poor *direct dependency* today — coupled to darts units/`dtyping` and a private package name,
brings its own type checker and click stack. The valuable part is the ideas, not the code:
explicit field metadata, `delta` dumps, source-tracked layering, unknown-key rejection,
per-field CLI hints, repeated-flag-for-records + fixed-`nargs`-for-vectors.

## Proposal: a small pluggable schema interface (deferred, not built)

tlaunch's own code would see only a normalized description, never a backend directly:

```python
@dataclass
class FieldSpec:            # one leaf field
    path: tuple[str, ...]
    type: Any                # Optional stripped
    required: bool
    default: Any              # MISSING sentinel if none
    choices: list | None      # Literal / Enum
    constraints: dict         # ge/le/gt/lt/min_length/...
    owner: type

class SchemaBackend(Protocol):
    def handles(self, cls) -> bool: ...
    def fields(self, cls) -> Iterator[FieldSpec]: ...   # recursive
    def validate(self, cls, data: dict) -> Any: ...      # raises SchemaError
    def json_schema(self, cls) -> dict: ...
```

`@launch(config=Model)` would accept any class some registered backend `handles`. This is what
would make "system Pydantic vs pip Pydantic" a non-issue for tlaunch itself: it adapts to
whatever the launch author's model classes are (a v1 backend, a v2 backend, later an `InputDict`
backend), rather than tlaunch depending on one version. Cost: a few dozen lines per backend and
a test matrix; the current walker already has ~90% of the logic (`schema.py` is already the
single Pydantic-aware module by design, precisely so this stays cheap to add later).

Not decided if/when resumed: how `FieldSpec.type` represents collections/models generically;
whether launch authors ever see `FieldSpec` directly (leaning: no, only for per-field hints,
Q14); adding `InputDict` as a backend only makes sense once/if it can be decoupled from darts
or `python3-darts-core` becomes installable here.

## Units (Q15)

REP 103 makes SI the ROS convention, so unit-aware config is genuinely useful:
`--robot.radius "22 cm"`, YAML `yaw: 90 deg`, normalized to SI before reaching a node
parameter, with a clear error for `"22 kg"` given a length field. Also removes a common launch
bug class (mm vs m).

**Integration paths, cheapest first:** (A) nothing now, plain floats, units documented in
`description` — what's built today. (B) **tlaunch-native**: an `Annotated[float, Unit("m")]`
marker in our own schema layer, parsing `"22 cm"`-style strings via a small dependency
(`pint`, apt-installable) or a built-in unit table. (C) reuse darts' `dunits` unit *types*
only, via `python3-darts-core` if installed. (D) adopt `InputDict` as a full backend (heaviest;
same cons as above).

**Recommendation: (B), the native route.** Verified with a toy `Unit` marker + Pydantic
`BeforeValidator`: `annotation` stays `float` (so flag classification is unaffected), the
marker survives in `FieldInfo.metadata`, `"22 cm"` validates to `0.22`, and `ge=0` bounds apply
*after* conversion. Estimated 250–400 lines including tests.

```python
from ros2_typed_launch.units import Length, Duration, Angle

class NavConfig(BaseModel):
    radius: Length = Field(0.22, ge=0)   # Annotated[float, Unit("m")]; "22 cm" -> 0.22
    timeout: Duration = 5.0               # seconds
    yaw: Angle = 0.0                      # radians
```

Work, in order: `units.py` (the `Unit` marker + aliases: `Length`, `Duration`, `Angle`,
`Frequency`, `Velocity`, `AngularVelocity`, `Acceleration`, `Mass`); wire `FieldSpec.unit`
through `schema.py`; show the unit and accepted forms in `-h`/`--show-config`; collections
(each list element converted); clear dimension/parse errors; table-driven tests; docs.

**Decisions needed before starting:**

| # | Decision | Leaning |
|---|---|---|
| U1 | Unit engine | **`pint`** (full algebra, pure Python, apt-installable — `python3-pint`); import lazily so unit-free launches pay nothing. Alternative: a built-in table (no dependency, but we own compound-unit correctness) |
| U2 | Bare numbers | Interpreted in the declared unit |
| U3 | What the launch function receives | A plain `float` in the declared unit (ROS-friendly), not a quantity object |
| U4 | Which aliases ship | The list above, plus anything routinely needed; offset units (°C) out of scope |
| U5 | Angle conveniences | `"90 deg"` yes; expressions like `"pi/2"` no |
| U6 | Relation to darts | Coexists — the native route stays available for plain Pydantic models even if `python3-darts-core` is later added as a backend |
