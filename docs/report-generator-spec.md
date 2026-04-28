# Weekly Staffing Report — Generator Spec

Requirements for the generator that produces the weekly staffing Excel report (Board_Summary and Weekly_Detail tabs). Derived from review of the 2026-04-12 report with the report owner.

## Context

- The report is **regenerated from scratch** each week from source schedule data. It is an output artifact, not a live spreadsheet.
- Do **not** use cross-sheet formulas to link Board_Summary values to Weekly_Detail. The generator writes both tabs independently from the same source data.
- Do **not** convert values to `SUM`/`AVERAGE` formulas. The generator is the source of truth.
- Do **not** rely on Excel conditional formatting for status colors. Bake color logic into the generator so the file renders identically in any viewer.
- Audience includes board members and non-operational readers. Tone of visual signals (colors, labels) should reflect operational reality without creating false alarm.

---

## 1. Configuration constants

These live in generator config, not derived from weekly data. Changes require a code update and should be reviewed with ops.

### 1.1 Canonical base ordering

Strict alphabetical, used everywhere (both tabs, any charts):

```
BASES = ["Bedford", "Lawrence", "Manchester", "Mansfield", "Plymouth"]
```

### 1.2 Base/unit configuration

Defines which bases staff which units on which shifts. Cells outside this config render as "N/A" (gray italic) and are excluded from all percentage calculations.

| Base       | RW/D | RW/N | GR/D | GR/N |
|------------|:----:|:----:|:----:|:----:|
| Bedford    | ✓    | ✓    | ✓    | ✓    |
| Lawrence   | ✓    | ✓    | ✓    | —    |
| Manchester | ✓    | —    | —    | —    |
| Mansfield  | ✓    | —    | ✓    | —    |
| Plymouth   | ✓    | ✓    | ✓    | —    |

### 1.3 Weekly budget caps (system denominators)

**Operational commitments**, not derivable from the config table. Do not compute them from 7 × configured-cell-count.

| Unit | Weekly budget | Composition |
|------|:-------------:|-------------|
| RW   | 56            | All configured RW cells × 7 days |
| GR   | 28            | Bedford 14 (always) + flex pool of 14 day shifts shared across Lawrence, Mansfield, Plymouth |

**Why GR = 28 and not 35:** Lawrence + Mansfield + Plymouth have 21 possible GR/D cells combined, but only 14 get staffed any given week — they share a budget pool. Using 35 as the denominator would understate GR%.

### 1.4 KPI thresholds

Color status is derived from these thresholds at generation time. **These match the dashboard app's RAG logic and the `kpi_thresholds` table** — keep them in sync.

| KPI                    | On target (green) | Monitor (yellow) | Action needed (red) |
|------------------------|-------------------|------------------|----------------------|
| Staffing rate          | ≥ 95%             | 90% – 94.9%      | < 90%                |
| Backfill rate (OT dep) | ≤ 8%              | 8.1% – 12%       | > 12%                |
| Shift exception %      | ≤ 25%             | 25.1% – 32%      | > 32%                |
| System GR %            | ≥ 92%             | 85% – 91.9%      | < 85%                |
| System RW %            | ≥ 95%             | 90% – 94.9%      | < 90%                |

Display the green threshold in a "Target" column on Board_Summary's KPI table and Weekly_Detail's Executive Summary so readers see *why* a status is Monitor without inferring.

### 1.5 Metrics that are NOT graded

Some fields are contextual information, not pass/fail metrics. They appear in the report without a Status column, Target, or color fill.

- **Unpartnered — Medic / RN:** raw count of staff working without a partner. Many legitimate reasons (LOA, open shift, training, sick call). Show the number plus a Notes cell for user-entered context. No color, no status badge.
- **Required shifts / Filled shifts / Vacancies / Shift exception total:** raw counts that feed into the graded percentages. Show the value, not a color.
- **Overtime shift counts by role (RN / Medic / EMT):** raw counts. The aggregate Backfill rate (§1.4) is what gets graded. Per-role OT counts are informational — no target, no status, no color. A high Medic OT count isn't inherently "bad"; it depends on context (e.g. covering LOA).
- **Schedule exception counts by role and type (AT / LT / SICK / LOA / JURY / BREV):** raw counts in the exceptions matrix. The aggregate Shift exception % is what gets graded.

### 1.6 Glossary of abbreviations


Abbreviations used in schedule data and report labels. Spell out on first use in any user-facing narrative; shorthand is acceptable in compact table cells where column width is constrained.


| Abbreviation | Meaning |
|--------------|---------|
| RW           | Rotor-Wing (helicopter unit) |
| GR           | Ground (ambulance unit) |
| D / N        | Day shift / Night shift |
| OT           | Overtime |
| LT           | Leave Time (planned PTO) |
| SL           | Sick Leave |
| LOA          | Leave of Absence |
| AT           | Admin Time |
| SICK         | Sick call (unplanned) |
| JURY         | Jury duty |
| BREV         | Bereavement |


**Label convention:** where space allows, prefer spelled-out terms in Executive Summary rows (e.g. "Vacant due to Leave Time and Sick Leave" rather than "Vacant d/t LT and SL"). Shorthand is only acceptable in the exceptions matrix column headers where width is fixed.


---

## 2. Status labels and severity gradient

### 2.1 Plain-English labels

In all user-facing cells, use:

- **On target** (green fill `#C6EFCE`, black text, bold)
- **Monitor** (yellow fill `#FFEB9C`, black text, bold)
- **Action needed** (red fill `#C12126`, white text, bold)

Do NOT use the words "Green / Yellow / Red" in status cells. The color communicates; the label should add meaning, not repeat the color.

### 2.2 Severity gradient

Full saturation fills are loud. Reserve them for notable deviation. Use softer fills when a metric is simply "as expected":

| Signal                           | Fill           | Notes |
|----------------------------------|----------------|-------|
| On target, expected (e.g. all RW 100%) | `#EAF5E9` soft green | Muted — avoids wall-of-green |
| On target, notable (per-base GR ≥ 95%) | `#C6EFCE` full green | Stands out against soft-green neighbors |
| Monitor                          | `#FFEB9C` yellow | Always full saturation |
| Action needed, moderate (40%–80% of red threshold miss) | `#F4CCCC` soft red | Severity gradient — not all reds are equal |
| Action needed, severe            | `#C12126` full red, white bold | Reserve for actually alarming |

**Rule of thumb:** if every cell in a row is the same full-saturation color, the reader loses signal. Break up visual monotony by using soft fills for the "expected" cases.

### 2.3 Legend row

Weekly_Detail includes a legend row directly under the reporting-period strip:

```
Status legend:   [On target]   [Monitor]   [Action needed]   N/A = unit not staffed at that base/shift
```

This primes first-time readers on the three-band scale and the N/A semantic before they see the data.

---

## 3. Aggregation rules

### 3.1 Per-base coverage %

`per_base_pct = covered_shifts / 7` for each configured cell. N/A cells have no percentage.

Display shift counts as raw integers (e.g. "4", "7"). Headers indicate the denominator: "RW/D (of 7)".

### 3.2 System coverage %

```
system_RW_pct = sum(covered RW shifts across all configured cells) / 56
system_GR_pct = sum(covered GR shifts across all configured cells) / 28
```

N/A cells contribute nothing to numerator or denominator. Computed separately for RW and GR.

### 3.3 Do not sum raw shift counts across bases

Adding per-base shift counts produces operationally meaningless numbers because bases share no single denominator. The only valid system-level metric is the weighted percentage above. The "System total" row in the base coverage grid should show only the RW% and GR% columns — leave the raw-count cells blank.

---

## 4. Cell rendering rules

### 4.1 Empty cells are a bug

Every cell in the base coverage grid must contain a value:

- **Integer 0–7** — configured cell with that many shifts covered
- **"N/A"** (gray italic `#999999`) — cell outside the base/unit config

Never leave blank. A blank cell forces readers to guess between "zero coverage" and "doesn't apply."

### 4.2 Status colors are computed, not painted

Cell fills for status come from §1.4 + §2.2. Never hardcode. A good week renders with mostly soft-green/neutral fills.

### 4.3 Direction arrows are computed

Column G on Board_Summary's KPI table shows **semantic** direction (improvement vs worsening), not raw week-over-week arithmetic alone.

Implementation matches `direction_for_metric` in code:

- **Higher-is-better metrics** (e.g. Staffing Rate, System RW %, System GR %): `↑` if this week’s value **>** prior week, `↓` if **less**, `→` if equal or no prior week.
- **Lower-is-better metrics** (e.g. Backfill rate / OT Dependency, Shift exception %): the raw `↑` / `↓` from a simple numeric compare are **swapped** so the symbol reflects *operational* direction — e.g. for OT Dependency, `↑` means the metric **improved** (value **decreased**), `↓` means it **worsened** (value **increased**). `→` if equal or no prior week.

So for lower-is-better KPIs, the arrow is **not** the same as “this week minus prior” sign alone; readers should interpret arrows as better / worse / flat for that metric.

### 4.4 Notes column and cell comments

The Executive Summary and Overtime blocks include a **Notes column** (rightmost). Two patterns for annotation:

- **Inline text** in the Notes cell — short free-text reason (e.g. *"2 medics on LOA, 2 open shifts"*)
- **Cell comment / tooltip** — for fields where a comment prompts the user to fill in context on regeneration (e.g. Unpartnered rows)

**Regeneration rule:** when the generator recreates the file, any text the user typed into a Notes cell on a prior week's file is lost. Notes for the current week should come from a **separate input** (yaml, form entry, CLI arg) that the generator reads at runtime, not from the spreadsheet itself.

---

## 5. Layout requirements

### 5.1 Column usage

Both tabs use columns A–G consistently. A is the label column (wider), B–G are data columns (uniform width ~95pt).

### 5.2 Weekly_Detail structure

1. Row 1: title + week number (right-aligned)
2. Row 2: reporting period + generation date (right-aligned)
3. Row 3: **status legend** (On target / Monitor / Action needed swatches + N/A note)
4. Executive summary block (Metric / Day / Night / Total / Target / Status / Notes)
5. Overtime block (Role / Day / Night / Total / Target / Status / Notes) — only the Backfill rate row gets Target + Status; per-role rows are raw counts per §1.5
6. Schedule exceptions by role (Role / AT / LT / SICK / LOA / JURY / BREV) — raw counts per §1.5, no Target/Status columns
7. Base coverage (Base / RW/D / RW/N / GR/D / GR/N / RW% / GR%)
8. Footer: denominator methodology + N/A semantic (10pt italic gray)

### 5.3 Section banners

Each banner is navy blue (`#052C47`) with white bold title on the left and a muted italic subtitle on the right in `#CBC7D1`:

- "Executive summary" — subtitle "This week vs target"
- "Overtime" — subtitle "Shift counts"
- "Schedule exceptions by role" — subtitle "Shift counts by type"
- "Base coverage" — subtitle "Rotor-Wing / Ground"

### 5.4 Alternating row banding

Data tables use `#F7F7F7` on every other row (subtle, not stripes). Totals rows get the darker `#E6E6E6` banner gray.

### 5.5 Visual separators in base coverage grid

Thin `#CBC7D1` vertical border between the RW columns (B:C) and GR columns (D:E), and between the count columns and the % columns (E | F). Keeps the four unit/shift combinations visually distinct.

### 5.6 Staffing rate values

Fill Day and Night columns for the Staffing rate row (`filled_day / required_day`, `filled_night / required_night`). Do not leave them blank — the reader will read a blank cell as missing data.

### 5.7 Board_Summary structure

- Title spans A1:G1
- KPI table includes Target column using §1.4 green thresholds
- System RW% and GR% appear in **one location only** — the KPI block. Remove from the base coverage table's totals row.

### 5.8 Narrative section (Board_Summary)

Current placeholder text (`[User-editable action item]`, empty Risks header) is not acceptable. Pick one model:

**Option A — Full auto.** Generator writes all four sections from rules:
- **Key Takeaways:** summarize staffing rate vs target, WoW direction, any "Action needed" KPIs
- **Drivers:** `if shift exception % elevated → bullet`; `if backfill in Monitor/Action → backfill bullet`
- **Risks:** `if any base GR in Action needed → list it`; `if medic_unpartnered > 0 → fatigue/readiness note`
- **Actions:** derive from risks (e.g. "Review coverage plan for base X")

**Option B — Hybrid.** Auto-generate Key Takeaways and Drivers. Leave Risks and Actions as empty sections with italic prompt: *"Add notes before distribution."* Drop `[User-editable action item]` strings entirely.

Pick one. Commit in code.

---

## 6. Metadata footer

Small block at the bottom of Board_Summary (or hidden row at top) with:

- Generation timestamp (ISO 8601)
- Generator / app version
- Source schedule filename
- Row count of source data

When a file "looks weird" three weeks from now, this turns a 30-minute forensic exercise into a 10-second check.

---

## 7. Priority order

1. **Config constants (§1.2, §1.3, §1.4)** — biggest correctness win. Prevents ambiguity on denominators and color logic.
2. **Status labels + severity gradient (§2)** — biggest communication win. Makes the report readable for non-operational audiences.
3. **Ungraded metrics (§1.5)** — removes false alarm on Unpartnered and per-role count rows.
4. **N/A semantics (§4.1)** — eliminates reader ambiguity on base coverage.
5. **Notes column + regeneration rule (§4.4)** — lets ops add context without losing it on rebuild.
6. **Layout polish (§5)** — legend row, section subtitles, row banding, visual separators.
7. **Narrative cleanup (§5.8)** — removes the half-finished look.
8. **Metadata footer (§6)** — do once you're in the code.

---

## 8. What NOT to do

- ❌ **Cross-sheet formulas.** Board_Summary cells should not reference Weekly_Detail cells. Both tabs are written independently from source data.
- ❌ **`SUM`/`AVERAGE` formulas on generated values.** Decorative at best, fragile at worst.
- ❌ **Conditional formatting for status colors.** Compute in code, write the fill directly.
- ❌ **Summing raw shift counts across bases.** See §3.3.
- ❌ **Deriving system denominators from the config table.** The 28 GR cap is an operational budget, not `7 × configured-cells`. See §1.3.
- ❌ **Hardcoded placeholder strings** like `[User-editable action item]`. See §5.8.
- ❌ **Status badges on ungraded rows** (Unpartnered, per-role OT counts, exception matrix cells). See §1.5.
- ❌ **Full-saturation green on every green cell.** See §2.2 — use soft green for "expected", reserve full green for "notable".
- ❌ **Storing user-typed Notes in the spreadsheet itself.** Notes are lost on regeneration. Use a separate input source. See §4.4.
- ❌ **The words "Green / Yellow / Red"** in user-facing status cells. Use "On target / Monitor / Action needed". See §2.1.

