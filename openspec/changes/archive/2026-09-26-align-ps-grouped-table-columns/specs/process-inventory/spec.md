## MODIFIED Requirements

### Requirement: One common process table per inventory section

The Rich projection of `ProcessInventory` SHALL preserve the existing shared-resource, main-checkout, and environment sections and their deterministic order. Within each section it SHALL render exactly one common bordered process table with primary columns `Type`, `State`, `PID / scope`, `Processes`, `CPU`, `Memory`, and `Details`. Odoo groups, the proven-owned PostgreSQL cluster/container, attributable PostgreSQL backend groups, and bounded external process contributions SHALL use these same columns instead of product-specific table shapes or prose.

All process tables rendered together in one Rich frame SHALL use the same ordered width allocation for those seven named columns. Each column's unconstrained width SHALL account for the widest header or sanitized cell line in every process section in that frame. When the unconstrained grid exceeds the available terminal width, the projection SHALL apply one deterministic constrained allocation to the whole frame and SHALL wrap content without dropping a section, row, column, or typed fact. The resulting outer table widths and corresponding vertical column boundaries SHALL match across every process table in the frame.

One-shot `odcli ps` SHALL derive the allocation from its single rendered inventory. Every successful `odcli ps --watch` refresh SHALL derive a fresh allocation from that refresh's inventory and current console width before replacing the live frame; it SHALL NOT reuse widths from a prior sample when content or terminal width changes.

Storage footprint MAY remain a separate section fact and SHALL NOT be represented as a process row. A section with no proven process entry SHALL still render an explicit empty/unavailable row in its common table rather than omitting the table or inventing a process.

#### Scenario: Mixed checkout uses one table

- **WHEN** one checkout has an Odoo process group, an attributable PostgreSQL backend group, and an external contribution
- **THEN** its section contains one bordered table with three rows under the same seven primary columns

#### Scenario: Owned cluster uses the common shape

- **WHEN** a shared-resource section contains a proven-owned PostgreSQL cluster/container
- **THEN** that cluster is one row in the section's common process table and is not emitted as a separate prose line or incompatible table

#### Scenario: Empty stopped checkout stays visible

- **WHEN** a stopped checkout has no running process and its typed inventory keeps the owner visible
- **THEN** its section contains one explicit stopped/unavailable row in the common table

#### Scenario: One-shot frame aligns unequal sections

- **WHEN** `odcli ps` renders two or more process sections whose corresponding cells require different natural widths at a fixed terminal width
- **THEN** every same-named column has identical left and right boundary positions across all process tables and all rows and facts remain present, wrapping where required

#### Scenario: Single section retains the common projection

- **WHEN** one Rich frame contains exactly one process section
- **THEN** that section renders one bounded table with the same seven columns and no placeholder or second table is introduced for layout coordination

#### Scenario: Watch recalculates one grid per refresh

- **WHEN** `odcli ps --watch` receives a later successful sample with wider cell content or a changed console width
- **THEN** the replacement frame uses one newly calculated width allocation across all of its process tables and does not mix boundaries from the preceding frame
