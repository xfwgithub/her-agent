---
sidebar_position: 0
title: "Run Nemotron 3 Ultra free in her Agent"
description: "Try NVIDIA Nemotron 3 Ultra on Nous Portal — free June 4–18 — with day 0 support in her Agent"
---

# Run Nemotron 3 Ultra free in her Agent

Nous Research has been inducted into the **Nemotron Coalition** of leading AI labs working with **NVIDIA** to advance open frontier foundation models. In honor of this, we've partnered with **Nebius** to provide **Nemotron 3 Ultra** free on [Nous Portal](https://portal.nousresearch.com) for two weeks (**June 4th – June 18th**). Follow the instructions below to try the model in your her Agent today.

:::info Limited-time offer
The `nvidia/nemotron-3-ultra:free` tier is available from **June 4th to June 18th**. The `:free` tag is what keeps it on the no-cost plan — pick that exact variant.
:::


## Install & set up



### 1. Install her Agent

On macOS/Linux/Android, run

```bash
curl -fsSL https://her-agent.nousresearch.com/install.sh | bash
```

Prefer to review first? Download [`install.sh`](https://her-agent.nousresearch.com/install.sh), inspect it, then run it.

After it finishes, reload your shell:

```bash
source ~/.bashrc   # or source ~/.zshrc
```

### 2. Run Quick Setup

```bash
her setup
```

Select **Quick Setup**. her opens a browser tab and waits for you to finish the next steps.

### 3. Create a Nous Portal account

In the browser, create a [Nous Portal](https://portal.nousresearch.com) account (or sign in) and choose the **Free** plan.

### 4. Connect your account

When prompted to connect your account to her Agent, click **Connect**. You'll see a confirmation once it's linked.

### 5. Select the free Nemotron 3 Ultra model

Return to your terminal. From the model list, select:

```
nvidia/nemotron-3-ultra:free
```

The `:free` tag is what keeps it on the no-cost tier, so make sure you pick that variant.

### 6. Start chatting

Complete the remaining Quick Setup prompts, then run:

```bash
her
```

That's it — you're talking to Nemotron 3 Ultra, free.

## Switching to it later

Already set up with another model? Switch any time from inside a session with `/model nvidia/nemotron-3-ultra:free`, or run `/model` to open the picker and choose it from the list.

## Troubleshooting

- **Don't see the model in the list?** Make sure you finished the Nous Portal connection and that you're on the **Free** plan. In the CLI, `her portal info` confirms you're logged in and routing through Nous.
- **Picked the wrong variant?** Re-select `nvidia/nemotron-3-ultra:free` — the `:free` suffix is required to stay on the no-cost tier.
- **Browser didn't open / you're on a remote host (CLI)?** See [OAuth over SSH / Remote Hosts](/guides/oauth-over-ssh) for port-forwarding and manual-paste workarounds.

## See also

- **[Run her Agent with Nous Portal](/guides/run-her-with-nous-portal)** — Full Portal walkthrough: models, Tool Gateway, and verification
- **[Nous Portal integration](/integrations/nous-portal)** — What's in the subscription
- **[Quickstart](/getting-started/quickstart)** — Install-to-chat in under 5 minutes
