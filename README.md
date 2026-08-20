[🇬🇧 English](README.md) | [🇯🇵 日本語](README.ja.md)

> **Note**: Japanese (`README.ja.md`) is the source of truth. Deep references (SPEC.md, ADRs, `spec/`) are maintained in Japanese only — links from this English README point to the Japanese sources.

# evolve-anything

A Claude Code Plugin that **autonomously observes, discovers, prunes, and evolves** Claude Code skills/rules, and **optimizes them via direct LLM patches**.

> Release metadata: **v1.125.0** · **24 userConfig options**

## Quickstart

```bash
# Register the marketplace (first time only)
claude plugin marketplace add todoroki-godai/evolve-anything

# Install
claude plugin install evolve-anything@evolve-anything --scope user

# Restart Claude Code
```

After restart, Observe hooks start running automatically and record skill usage, errors, and correction feedback.

Bare commands (`evolve-audit`, `evolve`, etc.) are also provided under `bin/`. Add it to your PATH to invoke them directly from the CLI:
```bash
export PATH="$(claude plugin path evolve-anything)/bin:$PATH"
evolve-audit
```

```bash
# Health check of your environment
/evolve-anything:audit

# Bulk-collect human utterances from past sessions (optional, zero LLM)
# Note: Skill/Agent observations are recorded going forward by observe hooks.
# The dedicated backfill CLIs were removed in #215; the skill is deprecated (#486).
bin/evolve-fleet ingest

# Daily operation (preview with dry-run first, then execute; ingest is included)
/evolve-anything:evolve --dry-run
/evolve-anything:evolve
```

In normal use, **just run `evolve` once a day**. If there isn't enough data, it will automatically suggest skipping.

## Python dependencies

The plugin keeps its source-tree launch model, while `pyproject.toml` is the single source of Python dependency groups. Install the group that enables the features you use:

```bash
# Required runtime parser only
python3 -m pip install -e ".[core]"

# DuckDB-backed telemetry and storage features
python3 -m pip install -e ".[storage]"

# TF-IDF, numerical similarity, and clustering features
python3 -m pip install -e ".[analysis]"

# Contributor setup: core/storage features plus pytest and pytest-xdist
python3 -m pip install -e ".[dev]"

# Add optional numerical-analysis capabilities when working on them
python3 -m pip install -e ".[dev,analysis]"
```

The legacy `scripts/requirements.txt` remains a storage-only compatibility entry point. It is intended to be installed from `scripts/` and delegates to `../pyproject.toml`.

## Overview — The Four Pillars

evolve-anything consists of **four independent pillars**.

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
│  evolve-fleet status → cross-project env_score / adoption   │
└─────────────────────────────────────────────────────────┘
```

| Pillar | What it does | Main command |
|--------|--------------|--------------|
| Autonomous Evolution | Detect patterns from usage data → generate skills → prune → evolve | `/evolve-anything:evolve` |
| Feedback | Detect user corrections ("no, that's wrong" etc.) → reflect into rules | `/evolve-anything:reflect` |
| Direct-Patch Optimization | corrections/context → 1-pass LLM patch → regression gate | `/evolve-anything:evolve-loop` |
| **Fleet Observation** | Cross-project env_score / adoption status (Phase 1: status), cross-project memory keyword recall | `bin/evolve-fleet status` / `bin/evolve-fleet recall` |
| Agent Management | Quality diagnosis & improvement proposals for agent definitions | `/evolve-anything:agent-brushup` |
| Second Opinion | Independent cold-read second opinion | `/evolve-anything:second-opinion` |
| Spec Management | Manage SPEC.md + ADRs, automatic L1/L2 promotion | `/evolve-anything:spec-keeper` |
| Breakthrough | Diagnose "almost-but-not-quite" stuck problems → strategy proposal → spawn Agent | `/evolve-anything:breakthrough` |
| Pitfall Curation | Grow any project's pitfalls.md: dedup / universality classification / top-N distillation / sync gate | `/evolve-anything:pitfall-curate` |
| Growth Visualization (NFD) | Lv.1–10 level system + phase auto-detect + 🏆 Results Board (rework count trend / accepted-rejected-pending-excluded decisions / withdrawal candidates) | `/evolve-anything:audit --growth` |

## Task-oriented Guide

| What you want to do | Command |
|---------------------|---------|
| Daily maintenance (preview → execute) | `evolve --dry-run` → `evolve` |
| Pinpoint-improve a specific skill | `evolve-loop my-skill` |
| Reflect correction feedback into rules | `reflect` |
| View accumulated feedback | `reflect --view` |
| Inventory all skills/rules | `audit` |
| Create a project-specific fitness function | `generate-fitness --ask` |
| Start collecting telemetry | Use Claude Code normally; observe hooks record new sessions automatically, then run `evolve` |
| Improve the fitness function itself | `evolve-fitness` |
| Diagnose & improve agent definitions | `agent-brushup` |
| Get an independent second opinion | `second-opinion` |
| Initialize / update SPEC.md | `spec-keeper init` / `spec-keeper update` |
| Break through stuck problems | `breakthrough` |
| View an environment growth report | `audit --growth` |
| Post-merge / post-deploy cleanup | `cleanup` |
| Curate a project's pitfalls.md (dedup / classify / distill / sync) | `pitfall-curate` |
| Cross-project fleet status | `bin/evolve-fleet status` |
| Cross-project memory recall (keyword) | `bin/evolve-fleet recall "<query>"` |
| Update the permanent cross-project utterance archive | `bin/evolve-fleet ingest` |
| Review implicit-correction signals and promote to corrections | `reflect --show-weak-signals` / `reflect --promote-weak` |

> All commands are invoked with the `/evolve-anything:` prefix (e.g., `/evolve-anything:evolve`).

## Skill Catalog (23 user-invocable skills)

> **Policy**: This catalog is generated from the `name:` field in every `skills/*/SKILL.md`. The public slash command is `/evolve-anything:<skill>`; a source directory may use a different implementation name. For example, `/evolve-anything:evolve-loop` is implemented in `skills/evolve-loop-orchestrator/`.

| Skill | Pillar | Description |
|-------|--------|-------------|
| `agent-brushup` | Agent Management | Diagnose and improve agent definitions |
| `audit` | Autonomous Evolution | Inventory and health-check skills, rules, and memory |
| `backfill` | Deprecated redirect | Explains the supported observe → evolve ingestion flow; it performs no backfill |
| `breakthrough` | Breakthrough | Diagnose stuck work and propose a strategy |
| `cleanup` | Utility | Safely clean up post-merge and post-deploy artifacts |
| `discover` | Autonomous Evolution | Detect patterns and generate skill/rule candidates |
| `docs-refresh` | Documentation | Refresh the HTML documentation site after a release |
| `evolve` | Autonomous Evolution | Run the daily evolution pipeline |
| `evolve-fitness` | Direct-Patch Optimization | Improve fitness functions from accept/reject data |
| `evolve-loop` | Direct-Patch Optimization | Baseline → patch → evaluation → human confirmation |
| `evolve-skill` | Direct-Patch Optimization | Apply the self-evolution pattern to one skill |
| `generate-fitness` | Direct-Patch Optimization | Generate a project-specific fitness function |
| `implement` | Structured Implementation | Decompose an approved plan, implement, verify, and record telemetry |
| `import` | Fleet | Import a community skill with a confirmation gate |
| `pitfall-curate` | Pitfall Curation | Classify, deduplicate, and distribute a project's pitfalls |
| `prune` | Autonomous Evolution | Identify unused or duplicate artifacts for consolidation |
| `queue` | Fleet | Show projects with enough material for a manual evolve run |
| `reflect` | Feedback | Review and promote correction feedback |
| `release-notes-review` | Utility | Review Claude Code release notes and environment health |
| `report-feedback` | Feedback | Turn feedback about evolve-anything into an issue proposal |
| `second-opinion` | Second Opinion | Obtain an independent cold-read review |
| `spec-keeper` | Spec Management | Maintain SPEC.md and ADRs |
| `tier` | Model Tier Management | Safely inspect and update model-tier policy |

## Bare CLI inventory (27 commands)

`bin/` is the source of truth for these executable names. Add that directory to `PATH` only if you want bare CLI invocation; slash skills remain the normal plugin interface.

| Command | Command | Command |
|---------|---------|---------|
| `evolve` | `evolve-audit` | `evolve-audit-aggregate` |
| `evolve-agent-task` | `evolve-backfill-turn-indices` | `evolve-codex-config-cleanup` |
| `evolve-daily-install` | `evolve-daily-run` | `evolve-discover` |
| `evolve-dogfood-gate` | `evolve-fleet` | `evolve-gain` |
| `evolve-loop` | `evolve-loop-ablation` | `evolve-migrate-legacy-accept` |
| `evolve-optimize` | `evolve-prompt-compare` | `evolve-prune` |
| `evolve-reflect` | `evolve-release-sync` | `evolve-reorganize` |
| `evolve-revert` | `evolve-reward-ema-cleanup` | `evolve-scaffold-advisory` |
| `evolve-score-noise` | `evolve-tier` | `evolve-usage-log` |

## Hooks (24 registered entries across 12 events)

`hooks/hooks.json` is the source of truth. Its 24 registrations include repeated `PostToolUse` registrations for Edit, Write, and MultiEdit; those entries invoke 19 distinct hook scripts at zero LLM cost.

| Hook script | Event / matcher | Primary effect |
|-------------|-----------------|----------------|
| `correction_detect` | UserPromptSubmit | Records correction feedback |
| `ctx_guard` | UserPromptSubmit | Warns when context occupancy crosses its configured threshold |
| `pitfall_injector` | UserPromptSubmit | Injects relevant registered pitfalls |
| `workflow_context` | PreToolUse / Skill | Records workflow context |
| `pitfall_commit_gate` | PreToolUse / Bash | Blocks unsafe registered-pitfall changes |
| `skill_activation_log` | PostToolUse / Skill | Records skill activation |
| `observe` | PostToolUse / Skill, Agent | Records usage and errors |
| `post_tool_use_memory` | PostToolUse / Edit, Write, MultiEdit | Records memory candidates |
| `pitfall_lint` | PostToolUse / Edit, Write, MultiEdit | Warns about pitfall-format drift |
| `subagent_observe` | SubagentStop | Records subagent completion data |
| `session_summary` | Stop | Records session and workflow summaries |
| `record_verbosity` | Stop | Records answer-length telemetry |
| `stop_failure` | StopFailure | Records API failures |
| `instructions_loaded` | InstructionsLoaded | Restores state and emits guidance |
| `save_state` | PreCompact | Saves the checkpoint |
| `post_compact` | PostCompact | Emits compact-recovery guidance |
| `file_changed` | FileChanged / CLAUDE.md\|SKILL.md | Suggests audit after relevant edits |
| `permission_denied` | PermissionDenied | Records denied permissions |
| `restore_state` | SessionStart | Restores session state |

### Auto Trigger

On session end / when corrections accumulate, evolve/audit execution is automatically *suggested* (not executed).

| Condition | Default threshold | Evaluated at |
|-----------|-------------------|--------------|
| Sessions since last evolve | ≥ 10 | Session end |
| Days since last evolve | ≥ 7 | Session end |
| Accumulated corrections | ≥ 10 | On correction detection |
| Days since last audit | ≥ 30 | Session end |

Settings can be overridden via `trigger_config` in `~/.claude/evolve-anything/evolve-state.json`:

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
/evolve-anything:evolve --dry-run    # Preview (recommended)
/evolve-anything:evolve              # Execute
```

Phases: Diagnose (Discover + Audit + Reorganize) → Compile (Optimize + Remediation + Reflect) → Housekeeping (Prune + Fitness Evolution) → Report

If fewer than 3 sessions have elapsed, or fewer than 10 observations have been collected since the last run, skipping is recommended.

### discover

```
/evolve-anything:discover                    # Pattern detection + candidate generation (enrich integrated)
/evolve-anything:discover --scope global     # Detect at global scope
```

Detection criteria: behavioral patterns (5+ occurrences) → skill candidates; error patterns (3+) → rule candidates; rejection reasons (3+) → rule candidates. Built-in Agents are split out into `agent_usage_summary`. Missing recommended rules/hooks are also detected. Existing-skill matching uses Jaccard similarity (enrich integration).

### prune

```
/evolve-anything:prune                 # Detect prune candidates
/evolve-anything:prune --restore       # Restore from archive
/evolve-anything:prune --list-archive  # List archive
```

Each candidate gets a recommendation label (archive / keep / needs review) and a description. TF-IDF similarity filtering reduces false positives. Reference-type skills are excluded from pruning.

### reflect

```
/evolve-anything:reflect                          # Interactive review
/evolve-anything:reflect --view                   # List pending
/evolve-anything:reflect --dry-run                # Preview only
/evolve-anything:reflect --apply-all              # Apply high-confidence in bulk (>= 0.85)
/evolve-anything:reflect --apply-all --min-confidence 0.70  # Override threshold
/evolve-anything:reflect --skip-semantic          # Disable semantic verification
```

### evolve-loop

```
/evolve-anything:evolve-loop my-skill              # 1 loop
/evolve-anything:evolve-loop my-skill --loops 3    # 3 loops
/evolve-anything:evolve-loop my-skill --auto       # Skip human confirmation
```

### generate-fitness

```
/evolve-anything:generate-fitness                # Default
/evolve-anything:generate-fitness --ask          # Ask quality criteria first
/evolve-anything:generate-fitness --name bot     # Specify function name
```

### audit

```
/evolve-anything:audit [project-dir]
/evolve-anything:audit --skip-rescore    # Skip quality measurement
/evolve-anything:audit --memory-context  # Output JSON for MEMORY semantic verification
```

Report contents: Skill Quality Trends / MEMORY Health / Plugin Usage / OpenSpec Workflow Analytics / Hardcoded-value detection.

### backfill (deprecated — #215/#486)

The dedicated CLIs (`rl-backfill`, etc.) were removed in #215. Observation is now recorded
going forward by observe hooks, and ingest/analysis is folded into `evolve` / `audit`.
To bulk-collect only human utterances first:

```
bin/evolve-fleet ingest                # Ingest human utterances across all PJs into utterances.db (zero LLM)
/evolve-anything:evolve --dry-run      # Ingest + improvement proposals (dry-run preview)
```

</details>

<details>
<summary><strong>Data flow</strong></summary>

All data is stored under `~/.claude/evolve-anything/`.

```
~/.claude/evolve-anything/
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
| `usage.jsonl` | observe hook | discover, prune, audit |
| `errors.jsonl` | observe hook | discover, audit |
| `sessions.jsonl` | session_summary hook | audit, evolve, discover |
| `workflows.jsonl` | session_summary hook | audit, discover |
| `corrections.jsonl` | correction_detect hook | reflect, discover, evolve, prune |
| `false_positives.jsonl` | reflect | correction_detect |
| `workflow_stats.json` | workflow_analysis.py | optimize, evolve-scorer, generate-fitness |
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

Once 30+ accept/reject records accumulate, `/evolve-anything:evolve-fitness` proposes improvements:
- score-acceptance correlation < 0.50 → recalibration recommended
- same `rejection_reason` 3+ times → propose adding a new axis

</details>

<details>
<summary><strong>evolve-scorer domain auto-detection</strong></summary>

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

After installation, hooks automatically record skill usage, errors, and correction feedback. In one project, `/bot-create` was repeatedly missing personality settings.

### Act 2: Discover → Optimize — From patterns to improvements

`/evolve-anything:discover` detected the pattern "personality is manually added after `/bot-create`" and auto-generated a rule candidate. Direct-patch optimization further improved the skill itself, raising its score from 0.62 → 0.84.

### Act 3: Reflect — Feedback comes alive

The correction "no, set personality first" was auto-reflected into CLAUDE.md via `/evolve-anything:reflect`, eliminating the recurring mistake.

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
/evolve-anything:reflect --view

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
