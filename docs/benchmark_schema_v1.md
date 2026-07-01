# Prooflens AI4Math Benchmark Schema v1

`prooflens.ai4math.v1` is the first compatibility target for exported benchmark items.
The schema is now marked `stable`: exporters must avoid removing or renaming the fields below
without adding a migration path.

## Common fields

- `schema_version`: fixed to `prooflens.ai4math.v1`.
- `schema_stability`: currently `stable`; future values may include `deprecated`.
- `missing_value`: always `null`. Missing optional data must use this sentinel rather than omitting
  the field.
- `field_status`: maps optional field names to `available`, `missing`, or `error`.
- `id`, `name`, `kind`, `file`, `line`, `column`: stable identity and source start location.
- `natural_language_description`, `lean_statement`, `dependencies`, `verification`, `difficulty`:
  model-facing task text, Lean context, direct dependencies, verification command, and coarse
  difficulty.

## Declaration items

Declaration items cover theorem-like and definition-like records:
`theorem`, `lemma`, `def`, `abbrev`, `structure`, `class`, and `inductive`.

Required declaration-only fields:

- `canonical_name`
- `semantic_kind`
- `end_line`
- `end_column`
- `formal_parameters`
- `assumptions`
- `conclusion`
- `static_dependencies`
- `semantic_dependencies`
- `proof_steps`
- `dependency_files`

## Tactic items

Tactic items use `kind: "tactic"` and point back to a parent declaration.

Required tactic-only fields:

- `parent_name`
- `parent_canonical_name`
- `parent_kind`
- `tactic`
- `tactic_text`
- `before_state`
- `after_state`

When proof-state extraction is unavailable, `before_state` and `after_state` are present with
`null` values and `field_status` entries set to `missing`.
