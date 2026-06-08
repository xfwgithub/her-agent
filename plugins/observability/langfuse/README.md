# Langfuse Observability Plugin

This plugin ships bundled with her but is **opt-in** — it only loads when
you explicitly enable it.

## Enable

Pick one:

```bash
# Interactive: walks you through credentials + SDK install + enable
her tools  # → Langfuse Observability

# Manual
pip install langfuse
her plugins enable observability/langfuse
```

## Required credentials

Set these in `~/.her/.env` (or via `her tools`):

```bash
HER_LANGFUSE_PUBLIC_KEY=pk-lf-...
HER_LANGFUSE_SECRET_KEY=sk-lf-...
HER_LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or your self-hosted URL
```

Without the SDK or credentials the hooks no-op silently — the plugin fails
open.

## Verify

```bash
her plugins list                 # observability/langfuse should show "enabled"
her chat -q "hello"              # then check Langfuse for a "her turn" trace
```

## Optional tuning

```bash
HER_LANGFUSE_ENV=production       # environment tag
HER_LANGFUSE_RELEASE=v1.0.0       # release tag
HER_LANGFUSE_SAMPLE_RATE=0.5      # sample 50% of traces
HER_LANGFUSE_MAX_CHARS=12000      # max chars per field (default: 12000)
HER_LANGFUSE_DEBUG=true           # verbose plugin logging
```

## Disable

```bash
her plugins disable observability/langfuse
```
