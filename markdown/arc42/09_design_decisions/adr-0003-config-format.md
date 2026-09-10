# ADR-0003: YAML is the default config format, TOML optional

|             |                                                                                                                       |
|-------------|-----------------------------------------------------------------------------------------------------------------------|
| **Status**  | accepted                                                                                                              |
| **Affects** | [chapter 2.1](../02_architecture_constraints/index.md), [chapter 5.7](../05_building_block_view/config_resolution.md) |

## Context

denver started on `denver.yml`. It then switched to `denver.toml`, and later
switched back. The arguments on each side:

| For TOML                                                    | For YAML                                                      |
|-------------------------------------------------------------|---------------------------------------------------------------|
| In the stdlib since 3.11 (`tomllib`), no dependency to read | Nested stage sections read far better than TOML tables        |
| Unambiguous types, no YAML surprises (`no` as a boolean)    | Lists of maps (`packages:`, `download-auth`) are much shorter |
| Already the format of `pyproject.toml` next to it           | Every user already writes YAML in CI files                    |
|                                                             | PyYAML is one small dependency, and denver already had it     |

## Decision

- `denver.yml` (and `denver.yaml`) is the default. PyYAML is a runtime dependency.
- `denver.toml` stays supported. Both are dispatched by file name, everywhere `<env>` is.
- `--show-config` / `--show-config-full` take `--format {yml,toml}`, independent of what the env itself is written in.
- TOML output uses denver’s own renderer, not a library. Writing TOML needs far less than reading it.

## Consequences

- Bundled examples were converted to `denver.yml`.
- Error messages had to stop naming `denver.toml` as *the* config file.
- A team preferring TOML loses nothing. A team preferring YAML gets the shorter file.
- The TOML renderer is denver’s own code, and is covered by the same test gate as everything else.
- TOML has no `null`, so `--show-config-full --format toml` writes unset keys as commented-out `# key = null` lines.
