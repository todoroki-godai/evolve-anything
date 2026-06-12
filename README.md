[🇬🇧 English](README.md) | [🇯🇵 日本語](README.ja.md)

> **Note**: Japanese (`README.ja.md`) is the source of truth. Deep references (SPEC.md, ADRs, `spec/`) are maintained in Japanese only — links from this English README point to the Japanese sources.

# rl-anything

A Claude Code Plugin that **autonomously observes, discovers, prunes, and evolves** Claude Code skills/rules, and **optimizes them via direct LLM patches**.

## Quickstart

```bash
# Register the marketplace (first time only)
claude plugin marketplace add todoroki-godai/rl-anything

# Install
claude plugin install rl-anything@rl-anything --scope user

# Restart Claude Code
```

After restart, Observe hooks start running automatically and record skill usage, errors, and correction feedback.

Bare commands (`rl-audit`, `rl-evolve`, etc.) are also provided under `bin/`. Add it to your PATH to invoke them directly from the CLI:
```bash
export PATH="$(claude plugin path rl-anything)/bin:$PATH"
rl-audit
```

```bash
# Health check of your environment
/rl-anything:audit

# Bulk-collect human utterances from past sessions (optional, zero LLM)
# Note: Skill/Agent observations are recorded going forward by observe hooks.
# The dedicated backfill CLIs were removed in #215; the skill is deprecated (#486).
bin/rl-fleet ingest

# Daily operation (preview with dry-run first, then execute; ingest is included)
/rl-anything:evolve --dry-run
/rl-anything:evolve
```

In normal use, **just run `evolve` once a day**. If there isn't enough data, it will automatically suggest skipping.

## Overview — The Four Pillars

rl-anything consists of **four independent pillars**.

```
┌─────────────────────────────────────────────────────────┐
│  Pillar 1: Autonomous Evolution Pipeline                │
│  Observe(hooks) → Diagnose → Compile → Housekeeping     │
│  → Run all phases via `evolve`                          │
├─────────────────────────────────────────────────────────┤
│  Pillar 2: Correction Feedback Loop                     │
│  correction_detect(hook) → corrections.jsonl → Reflect  │
├─────────────────────────────────────────────────────────┤
│  Pillar 3: Direct-Patch Optimization                    │
│  Generate-Fitness → Optimize → RL-Loop → Evolve-Fitness │
├─────────────────────────────────────────────────────────┤
│  Pillar 4: Fleet Observation & Intervention             │
│  rl-fleet status → cross-project env_score / adoption   │
└─────────────────────────────────────────────────────────┘
```

| Pillar | What it does | Main command |
|--------|--------------|--------------|
| Autonomous Evolution | Detect patterns from usage data → generate skills → prune → evolve | `/rl-anything:evolve` |
| Feedback | Detect user corrections ("no, that's wrong" etc.) → reflect into rules | `/rl-anything:reflect` |
| Direct-Patch Optimization | corrections/context → 1-pass LLM patch → regression gate | `/rl-anything:rl-loop` |
| **Fleet Observation** | Cross-project env_score / adoption status (Phase 1: status), cross-project memory keyword recall | `bin/rl-fleet status` / `bin/rl-fleet recall` |
| Agent Management | Quality diagnosis & improvement proposals for agent definitions | `/rl-anything:agent-brushup` |
| Second Opinion | Independent cold-read second opinion | `/rl-anything:second-opinion` |
| Spec Management | Manage SPEC.md + ADRs, automatic L1/L2 promotion | `/rl-anything:spec-keeper` |
| Breakthrough | Diagnose "almost-but-not-quite" stuck problems → strategy proposal → spawn Agent | `/rl-anything:breakthrough` |
| Pitfall Curation | Grow any project's pitfalls.md: dedup / universality classification / top-N distillation / sync gate | `/rl-anything:pitfall-curate` |
| Growth Visualization (NFD) | Lv.1–10 level system + 4-phase auto-detect + 5 traits + growth narrative | `/rl-anything:audit --growth` |

## Task-oriented Guide

| What you want to do | Command |
|---------------------|---------|
| Daily maintenance (preview → execute) | `evolve --dry-run` → `evolve` |
| Pinpoint-improve a specific skill | `rl-loop my-skill` |
| Reflect correction feedback into rules | `reflect` |
| View accumulated feedback | `reflect --view` |
| Inventory all skills/rules | `audit` |
| Create a project-specific fitness function | `generate-fitness --ask` |
| Collect data from past sessions | `backfill` |
| Improve the fitness function itself | `evolve-fitness` |
| Diagnose & improve agent definitions | `agent-brushup` |
| Get an independent second opinion | `second-opinion` |
| Initialize / update SPEC.md | `spec-keeper init` / `spec-keeper update` |
| Break through stuck problems | `breakthrough` || Environment growth report | `audit --growth` |
| Post-merge / post-deploy cleanup | `cleanup` |
| Curate a project's pitfalls.md (dedup / classify / distill / sync) | `pitfall-curate` |
| Cross-project fleet status | `bin/rl-fleet status` |
| Cross-project memory recall (keyword) | `bin/rl-fleet recall "<query>"` |
| Update the permanent cross-project utterance archive | `bin/rl-fleet ingest` |
| Review implicit-correction signals and promote to corrections | `reflect --show-weak-signals` / `reflect --promote-weak` |

> All commands are invoked with the `/rl-anything:` prefix (e.g., `/rl-anything:evolve`).

## Skill Catalog (19 user-invocable skills)

> **Policy**: Only user-invocable skills (callable via `/rl-anything:<skill>`) are listed here. Internal skills called automatically by evolve are noted below the table.

| Skill | Pillar | Description |
|-------|--------|-------------|
| `evolve` | Autonomous Evolution | Run all phases together (daily operation) |
| `discover` | Autonomous Evolution | Detect patterns from observation data → generate skill/rule candidates |
| `prune` | Autonomous Evolution | Prune unused / duplicate artifacts (with merge consolidation) |
| `audit` | Autonomous Evolution | Inventory & health check of skills/rules/memory + Growth Report |
| `backfill` | Autonomous Evolution | Collect & analyze data from past session history |
| `reflect` | Feedback | Reflect corrections into CLAUDE.md / rules |
| `rl-loop` | Direct-Patch Optimization | Baseline → direct patch → evaluation → human-confirmation loop (backed by `rl-loop-orchestrator`) |
| `generate-fitness` | Direct-Patch Optimization | Auto-generate project-specific fitness functions |
| `evolve-fitness` | Direct-Patch Optimization | Improve fitness functions from accept/reject data |
| `evolve-skill` | Direct-Patch Optimization | Inject self-evolution patterns into a specific skill |
| `agent-brushup` | Agent Management | Quality diagnosis / improvement proposals for agent definitions |
| `second-opinion` | Second Opinion | Independent cold-read second opinion via Claude Agent |
| `breakthrough` | Breakthrough | Diagnose "almost-but-not-quite" stuck problems → strategy → spawn Agent |
| `implement` | Structured Implementation | plan artifact → task decomposition → implementation (Standard/Parallel) → plan-conformance check → telemetry |
| `spec-keeper` | Spec Management | SPEC.md + ADR management, Progressive Disclosure L1/L2 auto-promotion || `cleanup` | Post-merge cleanup | After PR merge / deploy: handle branches / remote refs / worktrees / tmp dirs / close-candidate Issues / leftover PR Test plan items via per-item approval. Default tmp-dir prefix is `rl-anything-` only (see [ADR-021 (JA)](docs/decisions/021-cleanup-tmp-dir-prefix-safety.md)) |
| `pitfall-curate` | Pitfall Curation | Grow any project's pitfalls.md (project-agnostic): jaccard dedup + supersede / universality classification (`Transferability` × `Generality` 1–5) / three-tier disclosure top-N distillation / record↔classify↔distribute sync gate. Classification & reframing are the agent's judgment; deterministic work is `pitfall_curate.py`. Opt-in auto-enforcement: run `enable` once per project to register a pitfalls.md, then edit-time (`pitfall_lint`, warn-only) and commit-time (`pitfall_commit_gate`, blocks index/TOC wipe) hooks keep its canonical format automatically. Distinct from `pitfall_manager` (self-evolved-skill-only) |
| `release-notes-review` | Utility | CC release-notes analysis + global environment health check (`--env-only` supported) |
| `feedback` | Utility | Send feedback via GitHub Issue |

**Internal skills** (called automatically, not user-invocable): `rl-loop-orchestrator` (rl-loop backend: baseline→patch→eval→confirm loop), `genetic-prompt-optimizer` (LLM direct-patch optimizer used by rl-loop), `reorganize` (split detection, called by evolve), `enrich` (merged into discover, deprecated)

## Hooks (Data Collection)

13 hooks cover the full session lifecycle at zero LLM cost.

| Hook | Event | Output |
|------|-------|--------|
| `observe` | PostToolUse | `usage.jsonl`, `errors.jsonl` |
| `correction_detect` | UserPromptSubmit | `corrections.jsonl` |
| `subagent_observe` | SubagentStop | `subagents.jsonl` |
| `instructions_loaded` | InstructionsLoaded | `sessions.jsonl` + Growth greeting |
| `workflow_context` | PreToolUse | `$TMPDIR/rl-anything-workflow-*.json` |
| `skill_activation_log` | PostToolUse | `skill_activations.jsonl` (skill firing record) |
| `file_changed` | FileChanged | stdout (audit suggestion) |
| `permission_denied` | PermissionDenied | `errors.jsonl` (permission-denial record) |
| `stop_failure` | StopFailure | `errors.jsonl` (API errors) |
| `save_state` | PreCompact | `checkpoint.json` |
| `post_compact` | PostCompact | stdout (post-compact guidance) |
| `restore_state` | SessionStart | stdout |
| `session_summary` | Stop | `sessions.jsonl`, `workflows.jsonl` |

### Auto Trigger

On session end / when corrections accumulate, evolve/audit execution is automatically *suggested* (not executed).

| Condition | Default threshold | Evaluated at |
|-----------|-------------------|--------------|
| Sessions since last evolve | ≥ 10 | Session end |
| Days since last evolve | ≥ 7 | Session end |
| Accumulated corrections | ≥ 10 | On correction detection |
| Days since last audit | ≥ 30 | Session end |

Settings can be overridden via `trigger_config` in `~/.claude/rl-anything/evolve-state.json`:

```json
{
  "trigger_config": {
    "enabled": true,
    "triggers": {
      "session_end": { "min_sessions": 10, "max_days": 7 },
      "corrections": { "threshold": 10 },
      "audit_overdue": { "interval_days": 30 }
    },
    "cooldown_hours": 24
  }
}
```

Disable: `"trigger_config": { "enabled": false }`

---

The sections below are detail references — read on demand.

<details>
<summary><strong>Per-skill detailed options</strong></summary>

### evolve

```
/rl-anything:evolve --dry-run    # Preview (recommended)
/rl-anything:evolve              # Execute
```

Phases: Diagnose (Discover + Audit + Reorganize) → Compile (Optimize + Remediation + Reflect) → Housekeeping (Prune + Fitness Evolution) → Report

If fewer than 3 sessions have elapsed, or fewer than 10 observations have been collected since the last run, skipping is recommended.

### discover

```
/rl-anything:discover                    # Pattern detection + candidate generation (enrich integrated)
/rl-anything:discover --scope global     # Detect at global scope
```

Detection criteria: behavioral patterns (5+ occurrences) → skill candidates; error patterns (3+) → rule candidates; rejection reasons (3+) → rule candidates. Built-in Agents are split out into `agent_usage_summary`. Missing recommended rules/hooks are also detected. Existing-skill matching uses Jaccard similarity (enrich integration).

### prune

```
/rl-anything:prune                 # Detect prune candidates
/rl-anything:prune --restore       # Restore from archive
/rl-anything:prune --list-archive  # List archive
```

Each candidate gets a recommendation label (archive / keep / needs review) and a description. TF-IDF similarity filtering reduces false positives. Reference-type skills are excluded from pruning.

### reflect

```
/rl-anything:reflect                          # Interactive review
/rl-anything:reflect --view                   # List pending
/rl-anything:reflect --dry-run                # Preview only
/rl-anything:reflect --apply-all              # Apply high-confidence in bulk (>= 0.85)
/rl-anything:reflect --apply-all --min-confidence 0.70  # Override threshold
/rl-anything:reflect --skip-semantic          # Disable semantic verification
```

### rl-loop

```
/rl-anything:rl-loop my-skill              # 1 loop
/rl-anything:rl-loop my-skill --loops 3    # 3 loops
/rl-anything:rl-loop my-skill --auto       # Skip human confirmation
```

### generate-fitness

```
/rl-anything:generate-fitness                # Default
/rl-anything:generate-fitness --ask          # Ask quality criteria first
/rl-anything:generate-fitness --name bot     # Specify function name
```

### audit

```
/rl-anything:audit [project-dir]
/rl-anything:audit --skip-rescore    # Skip quality measurement
/rl-anything:audit --memory-context  # Output JSON for MEMORY semantic verification
```

Report contents: Skill Quality Trends / MEMORY Health / Plugin Usage / OpenSpec Workflow Analytics / Hardcoded-value detection.

### backfill (deprecated — #215/#486)

The dedicated CLIs (`rl-backfill`, etc.) were removed in #215. Observation is now recorded
going forward by observe hooks, and ingest/analysis is folded into `evolve` / `audit`.
To bulk-collect only human utterances first:

```
bin/rl-fleet ingest                # Ingest human utterances across all PJs into utterances.db (zero LLM)
/rl-anything:evolve --dry-run      # Ingest + improvement proposals (dry-run preview)
```

</details>

<details>
<summary><strong>Data flow</strong></summary>

All data is stored under `~/.claude/rl-anything/`.

```
~/.claude/rl-anything/
├── usage.jsonl           # Skill / agent usage records
├── errors.jsonl          # Error records
├── sessions.jsonl        # Session summaries
├── workflows.jsonl       # Workflow sequences
├── subagents.jsonl       # Subagent completion data
├── usage-registry.jsonl  # Global skill usage registry
├── corrections.jsonl     # Correction feedback
├── false_positives.jsonl # False-positive corrections (SHA-256 managed)
├── workflow_stats.json   # Workflow statistics (output by workflow_analysis.py)
├── checkpoint.json       # Evolution-state checkpoint
├── archive/              # Files archived by prune
└── feedback-drafts/      # Locally-saved feedback
```

| File | Writer | Reader |
|------|--------|--------|
| `usage.jsonl` | observe hook, backfill | discover, prune, audit |
| `errors.jsonl` | observe hook | discover, audit |
| `sessions.jsonl` | session_summary hook, backfill | audit, evolve, discover |
| `workflows.jsonl` | session_summary hook, backfill | audit, discover |
| `corrections.jsonl` | correction_detect hook, backfill | reflect, discover, evolve, prune |
| `false_positives.jsonl` | reflect | correction_detect |
| `workflow_stats.json` | workflow_analysis.py | optimize, rl-scorer, generate-fitness |
| `checkpoint.json` | save_state hook | restore_state hook |

</details>

<details>
<summary><strong>Fitness functions</strong></summary>

### Built-in

| Function | Description |
|----------|-------------|
| `default` | Generic LLM evaluation (clarity / completeness / structure / practicality) |
| `skill_quality` | Rule-based structural quality (+ CSO security axis) |
| `coherence` | Structural coherence of the environment (4 axes: Coverage / Consistency / Completeness / Efficiency) |
| `telemetry` | Telemetry-driven environmental effectiveness (3 axes: Utilization / Effectiveness / Implicit Reward) |
| `constitutional` | Principle-based LLM-Judge evaluation (project-specific principles × 4 layers) |
| `chaos` | Virtual-removal robustness (virtually delete Rules/Skills, detect SPOFs via Coherence ΔScore) |
| `environment` | Dynamic-weight integration of coherence + telemetry + constitutional |
| `plugin` | Plugin-integrated fitness |

`telemetry` / `environment` / `constitutional` are *not* used via the `--fitness` flag (they require a project path). Use them via `audit --coherence-score --telemetry-score --constitutional-score`.

### Project-specific (custom)

Place at `scripts/rl/fitness/{name}.py` → use with `--fitness {name}`.

Interface: receive skill content via stdin, output a value in 0.0–1.0 to stdout.

```python
#!/usr/bin/env python3
import sys

def evaluate(content: str) -> float:
    score = 0.0
    if "required-keyword" in content:
        score += 0.5
    return score

def main():
    content = sys.stdin.read()
    print(f"{evaluate(content)}")

if __name__ == "__main__":
    main()
```

### Cultivating fitness functions

Once 30+ accept/reject records accumulate, `/rl-anything:evolve-fitness` proposes improvements:
- score-acceptance correlation < 0.50 → recalibration recommended
- same `rejection_reason` 3+ times → propose adding a new axis

</details>

<details>
<summary><strong>rl-scorer domain auto-detection</strong></summary>

The domain is inferred from CLAUDE.md, switching evaluation axes automatically.

| Domain | Evaluation axes |
|--------|-----------------|
| Game | Immersion / fun / balance / specificity |
| API / Backend | Correctness / robustness / maintainability / security |
| Bot / Conversational | Personality fit / usefulness / tone consistency |
| Documentation | Accuracy / readability / executability / completeness |

Score composition: Technical quality (40%) + Domain quality (40%) + Structural quality (20%)

</details>

<details>
<summary><strong>Adoption story (Slack Bot project example)</strong></summary>

### Act 1: Observe — Data accumulates

After installation, hooks automatically record skill usage, errors, and correction feedback. With 14 skills in operation, `/bot-create` was repeatedly missing personality settings.

### Act 2: Discover → Optimize — From patterns to improvements

`/rl-anything:discover` detected the pattern "personality is manually added after `/bot-create`" and auto-generated a rule candidate. Direct-patch optimization further improved the skill itself, raising its score from 0.62 → 0.84.

### Act 3: Reflect — Feedback comes alive

The correction "no, set personality first" was auto-reflected into CLAUDE.md via `/rl-anything:reflect`, eliminating the recurring mistake.

### Act 4: Daily operation

| Timing | What to do |
|--------|------------|
| When adding a new skill | Run `optimize` once → review the diff |
| Daily / weekly | `evolve --dry-run` → confirm → `evolve` |
| When corrections pile up | `reflect` to apply feedback |

</details>

<details>
<summary><strong>Migrating from claude-reflect</strong></summary>

```bash
# Migrate data (idempotent, prevents double-append)
python3 <PLUGIN_DIR>/scripts/migrate_reflect_queue.py

# Verify
/rl-anything:reflect --view

# Uninstall
claude plugin uninstall claude-reflect
```

</details>

## Tests

```bash
# bare command collects everything (pytest.ini testpaths is the single source of collection paths)
python3 -m pytest -v

# Plugin definition consistency check
claude plugin validate
```

## Acknowledgements

The architecture for correction detection / confidence decay / multi-target routing draws on [claude-reflect](https://github.com/bayramnnakov/claude-reflect) (MIT License, Bayram Annakov).

## License

MIT
