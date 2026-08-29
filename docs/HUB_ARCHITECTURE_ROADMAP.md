# CrewPulse — Architecture & Roadmap

> **Status:** Draft / living document — captures the planning conversation so we can keep
> refining it before committing to implementation. **No code changes yet.**
> **Last updated:** 2026-06-29
> **Branch:** `claude/staffing-hub-architecture-72urx6`

---

## 1. Vision

Turn the current **Weekly Staffing** tool (a localhost KPI/report generator that *imports* an
Excel schedule) into **CrewPulse** — a **comprehensive Boston MedFlight operations hub**: the single management
layer that ingests the published schedule, lets the on-duty AOC operate it live (sick calls,
leave approvals, overtime, swaps), produces all reports and KPIs automatically, and broadcasts
the schedule to crews through secure, auto-updating calendars.

**One-liner:** *Crew Ops 360 builds and publishes the schedule; the Hub operates it, reports on
it, and shares it.*

---

## 2. System topology (federated)

There are **three** systems. We are building the middle-and-right; we consume from the left.

```
┌─────────────────┐   published 2-wk Excel   ┌──────────────────────────┐
│  Crew Ops 360   │  block (SharePoint)      │   THE HUB (this app)     │
│  (peers' app)   │ ───────────────────────► │  DB = source of truth    │
│  builds the     │                          │  • ingest @ publish      │
│  schedule:      │                          │  • AOC live operations   │
│  matrix, medic/ │ ◄─────────────────────── │  • approvals, KPIs       │
│  basic rotations│   write-back via Graph   │  • AOC daily report      │
│  year overlay   │   (AOC daily changes)    │  • public crew calendars │
└─────────────────┘                          └──────────────────────────┘
```

- **Crew Ops 360** stays the schedulers' product. We do **not** absorb scheduling. We consume
  its **published output**, not its codebase.
- **Excel on SharePoint** is the **integration bus** between the two apps (for now), not the
  source of truth.
- **The Hub** owns everything *after* a schedule is published.

### Strategic note — federated now, converge later
Build so that Excel is a *replaceable adapter*, not a dependency woven through the app. If the
peers ever agree to a shared database or a small API, we swap the adapter and nothing else
changes. Costs little now; avoids a rewrite later.

---

## 3. The core principle: invert the source of truth

**Today:** Excel is authoritative; the DB is a read-only derivative (one-directional import).
**Target:** the **DB is authoritative** once a schedule is published; Excel becomes a **mirror we
sync to** via Microsoft Graph.

Everything the Hub needs — live AOC changes, approvals, the public calendar, richer reporting —
requires this inversion. The Excel/Graph logic must be **quarantined in one sync module** behind
a clean interface (outbox pattern).

---

## 4. Publishing cadence & the rolling window

- The **publish unit is a 2-week block** = one **pay period**. Schedulers publish one new block
  every two weeks.
- The window always holds **exactly three published blocks = 6 weeks**.
- Publishing a new block at the far end **archives** the block that just finished.

```
        ARCHIVE          │   ROLLING WINDOW (always 3 blocks / 6 weeks)
   ┌──────────────┐      │  ┌─────────┐ ┌─────────┐ ┌─────────────┐
   │ prior blocks │ ◄────┼──│ Block A │ │ Block B │ │  Block C    │
   │ (history,    │ roll │  │ current │ │  next   │ │ next-next   │
   │  viewable)   │ off  │  │         │ │         │ │ (just       │
   └──────────────┘      │  └─────────┘ └─────────┘ │  released)  │
                         │                          └─────────────┘
   every 2 weeks: + one new block at the front, − the finished one to archive
```

The AOC operates whichever active block(s) a given day falls in.

---

## 5. Per-schedule state machine

```
Crew Ops 360 owns          │  THE HUB owns (DB = source of truth)
──────────────────────────►│──────────────────────────────────────►
  DRAFT  (building)        │  PUBLISHED      LIVE/OPS        ARCHIVED
                           │  ingest →       AOC changes:    rolled-off
         ── PUBLISH ──────►│  immutable      sick/leave/     block, still
                           │  snapshot (v1)  OT/swaps →      publicly
                           │                 write back      viewable
```

- **PUBLISH** is the **ownership-transfer boundary**. Once published, schedulers never touch the
  block again — only the AOC does. This is what eliminates two-writer Excel collisions.
- We must formalize the **"publish" signal** with the peers (a folder, a status, a button) so the
  Hub can reliably detect "new block ready to ingest and own."
- Each publish is stored as an **immutable snapshot**; AOC changes are a **running ledger on top**.
  This lets us answer both "what is the schedule now" and "what did the published schedule say,"
  and gives reporting a clean event stream.

---

## 6. Core data model (new concepts + existing reuse)

### New entities
- **`PublishedScheduleBlock`** — a 2-week / pay-period snapshot. `status` ∈ {`active`,
  `archived`}, `version`, `published_at`, `pay_period_id`, the two `week_start`s it covers.
  Always exactly three `active`.
- **`AocAssignment`** — who is the AOC of record for a given week/block (authority + attribution).
- **`OperationalEvent`** (the AOC ledger) — every sick call, leave approval, OT fill, swap, crew
  move. Attributed to an AOC, timestamped, immutable. **This is the AOC daily report.**
- **`LeaveRequest`** — crew request → AOC decision (approve/deny) → absence written to live block.
- **`CrewCalendarToken`** — per-person, rotatable/revocable token for personal calendar feeds.

### Reuse what already exists (strong foundation)
- `weekly_person_shifts`, `weekly_manager_shifts` (incl. `event_type='aoc'`),
  `weekly_ops_view_*`, `schedule_imports` (audit + `parser_version`), `staff_roster_entry`,
  `kpi_thresholds`, `base_config`, the whole weekly/quarterly report + KPI engine.

### Pay-period as a first-class unit
The org thinks in 2-week pay periods. Model the period as a real entity so every KPI/report can
pivot by **pay period** *or* by **week**. OT/leave/swaps accrue against the period.

---

## 7. Modules (spokes)

1. **Ingest** — detect publish, parse the 2-week block, freeze an immutable snapshot, take
   ownership, archive the roll-off block.
2. **AOC live ops** — the on-duty AOC's console: sick calls, leave approvals, OT, swaps, crew
   moves. Mobile-first. Every action → `OperationalEvent`.
3. **Approvals** — crew submits leave/swap requests; the AOC of the week decides; approval ripples
   into the live block, KPIs, and the Excel write-back.
4. **KPIs & reporting** — existing engine, now fed by live data; weekly **and** pay-period views;
   the AOC daily report generated from the event ledger.
5. **Public crew calendar** — subscribable, auto-updating feeds (see §9).
6. **Excel sync bridge** — quarantined Graph outbox: DB writes → mirror to SharePoint workbook;
   queues when SharePoint is unavailable.

---

## 8. AOC authority & attribution

- Leave approvals, swaps, OT, and sick calls are handled **solely by the AOC on duty** for that
  week. No approval chain.
- Model **"AOC of the week"** as an assignment so the system enforces **authority** (only the
  active AOC mutates that block) and records **attribution** (every change stamped with which AOC
  and when).
- Attribution + immutability = the AOC daily report writes itself, plus a real audit trail.

---

## 9. Public crew calendar

- **Subscribe, don't download.** A static `.ics` download goes stale silently. Use a subscribable
  feed (`webcal://…/crew/<token>.ics`) so Apple/Google Calendar **auto-refreshes** — an AOC swap
  at 3am updates the crew member's phone on its own.
- **Personal feed per crew member** ("just my shifts") *and* a whole-schedule view. The personal
  feed is the feature that gets crews off SharePoint.
- Default view = the three active blocks (next 3 pay periods); archived schedules a click behind.
- **Privacy line (important):** a public/no-login link may show *who's working / who's off* but
  **not absence reasons** (sick vs. vacation vs. LOA). "Smith — OFF" is fine publicly; "Smith —
  SICK" is not. Keep reasons on the authenticated AOC/manager side.
- **Token wrinkle:** subscribed feeds can't do interactive MFA, so personal feeds use a rotatable
  token handed out from behind the SSO portal, revoked when someone leaves.

---

## 10. Auth, hosting & stack (all Microsoft/Azure)

The Hub is hosted in the MedFlight Azure environment with MFA already enforced — lean in.

| Concern | Choice | Why |
|---|---|---|
| App framework | **Django** | Already in use; gives auth/permissions/admin |
| Database | **Azure Database for PostgreSQL** | Multi-user concurrent writes; retire SQLite |
| Login | **Entra ID SSO (OIDC)** | No passwords/MFA to build; inherits Azure MFA |
| Roles | **Entra groups → Hub roles** | AOC / Scheduler / Manager / Crew |
| Excel write-back | **Microsoft Graph** | Same Azure app-registration story as SSO |
| Hosting | **Azure App Service / container** | In-tenant, IT-governed |

SSO and Graph write-back ride the **same Azure app registration** — one integration story.

### Roles
- **AOC** — full operational control of their active week/block.
- **Scheduler** — read access (the Crew Ops 360 folks).
- **Manager (you)** — KPIs, reporting, config, oversight, AOC assignment.
- **Crew** — own personal calendar + the posted schedule.

---

## 11. The Excel integration contract (federated risk)

Because we stay federated, the **Excel layout is the contract and we don't own it.** If Crew Ops
360 changes its export, the ~2,000-line importer can break silently.

**Mitigations:**
- Ask the peers to emit a **structured sidecar export** (CSV/JSON, stable columns) *alongside* the
  human-readable Excel. Parsing a defined data file is robust; parsing a formatted spreadsheet is
  forever fragile.
- Formalize `parser_version` (already tracked in `schedule_imports`) into an **agreed schema
  version** shared by both apps.
- **Degraded mode:** if SharePoint/Graph is down, the AOC must still record changes — DB is the
  source of truth and the Graph write-back just **queues** in the outbox.

---

## 12. Security, privacy & audit

- **Entra SSO + MFA** for all authenticated access; no separate credential store.
- **Least-privilege roles** via Entra groups.
- **No absence reasons** on public/crew surfaces (§9).
- **Immutable, attributable event log** for every operational change (compliance + the AOC report).
- **Defined retention/archive horizon** for archived blocks and the event ledger (TBD with IT).
- **Token lifecycle** — rotate/revoke crew calendar tokens on offboarding.

---

## 13. Migration plan (don't break what works)

**Hard constraint:** the existing CEO weekly/quarterly board packs and KPI tooling must keep
working throughout.

1. Keep the current SQLite tool fully functional as Phase 0 baseline.
2. Stand up Postgres; port the SQLAlchemy schema; migrate historical data; run old reports against
   Postgres to prove parity **before** cutting over.
3. Add Entra SSO in front (read-only to start).
4. Introduce `PublishedScheduleBlock` + ingest-at-publish alongside the existing importer; reconcile.
5. Layer AOC ops, approvals, calendar, and Graph write-back incrementally.

---

## 14. Phased roadmap (never broken mid-flight)

- **Phase 0 — Baseline & rename.** Rename the project/product; capture this doc; freeze current
  behavior as the working baseline.
- **Phase 1 — Postgres + Entra SSO.** Move DB to Azure Postgres with data parity; add Microsoft
  login and role mapping. Existing reports still run.
- **Phase 2 — Block model & ingest.** `PublishedScheduleBlock`, the 3-block rolling window,
  ingest-at-publish, archive-on-roll. Formalize the "publish" signal with the peers.
- **Phase 3 — AOC live ops + ledger.** AOC-of-the-week assignment, sick/leave/OT/swap actions,
  immutable `OperationalEvent` log, auto-generated AOC daily report. Mobile-first.
- **Phase 4 — Approvals workflow.** Crew leave/swap requests → AOC decision → ripple into block +
  KPIs.
- **Phase 5 — Public crew calendars.** Subscribable personal + whole-schedule feeds, tokens,
  privacy filtering.
- **Phase 6 — Graph write-back.** Outbox mirror to SharePoint Excel; degraded-mode queuing.
- **Phase 7 — Reporting polish.** Pay-period views, richer KPIs, AOC/operational analytics.

*(Phase order keeps a working system at every step; Excel write-back comes last because the DB is
already authoritative by then.)*

---

## 15. Naming

This is the **operations/management layer**, not the scheduler ("Crew Ops 360" is the peers'). Avoid
"Weekly" (it's no longer just weekly).

> **Decision:** **CrewPulse.** Distinctive, app-like, and suggests a live read on the crew —
> calendars, sick calls, OT, KPIs. Pairs alongside "Crew Ops 360" without sounding like a
> spreadsheet. (Considered: MedFlight Ops Hub, Staffing Command, Coverage Hub.)

---

## 16. Decisions locked in this conversation

- **Name = CrewPulse.**
- Federated with Crew Ops 360; consume the **published 2-week Excel block**, do not absorb scheduling.
- **DB becomes the source of truth** post-publish; Excel is a synced mirror (outbox via Graph).
- Publish unit = **2-week pay-period block**; always **3 active blocks / 6 weeks**, archive-on-roll.
- **Publish = ownership transfer**; only the AOC mutates a published block (kills write collisions).
- **AOC of the week** holds full operational control (leave approvals, sick, OT, swaps); no chain.
- Auth = **Entra ID SSO** (inherits MFA); roles via Entra groups; hosted in **Azure** on Postgres.
- Crew calendars = **subscribable auto-updating feeds**, personal + whole-schedule, no absence
  reasons on public surfaces.

---

## 17. Open questions to refine

1. **AOC handoff** — backup AOC and how the on-duty baton passes if they're unavailable mid-week.
2. **Notifications** — does an affected crew member get an *active* ping (email/text/push) on a
   change, or is the auto-updating calendar enough?
3. **Degraded mode** — confirm AOC can fully operate with SharePoint/Graph offline (outbox queues).
4. **Report continuity** — exact parity checks to run before the Postgres cutover.
5. **Retention/audit horizon** — how long do archived blocks and the event ledger live (IT/compliance).
6. **Mobile scope** — which AOC screens must be phone-usable on day one.
7. **Token lifecycle** — offboarding flow that revokes crew calendar links.
8. **The "publish" signal** — concrete mechanic with the peers (folder/status/button) the Hub watches.
9. **Structured sidecar export** — will the peers add a CSV/JSON alongside the Excel?
