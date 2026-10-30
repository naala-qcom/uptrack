# Uptrack

A generic upstream patch tracker for monitoring mailing-list and patchwork activity across multiple Linux subsystems.

Uptrack polls configured lore Atom feeds and Patchwork projects, stores normalized patch metadata in local state, and provides commands to list, inspect, open, and apply patches.

## Features

- Polls both lore Atom feeds and Patchwork REST API
- Deduplicates by message-id (`msgid`)
- Tracks patch state (`new`, `seen`, `ignored`)
- Supports subsystem tagging and filtering (`--subsystem`)
- Paged list output (via `less`) with optional interactive selection
- Opens full thread content (cover letter + patches) in a cleaned, readable view
- Applies patches via `b4 am`

## Requirements

- Python 3.10+
- `curl`
- `b4` (required for `am` and `open`)
- Optional: `fzf` for interactive list selection
- Optional: `notify-send` for desktop notifications

## Installation

```bash
git clone <your-repo-url>
cd uptrack
./install.sh
```

This installs:

- `~/.local/bin/uptrack` (primary command)

If needed, add `~/.local/bin` to your `PATH`.

## Quick Start

```bash
uptrack fetch
uptrack list
uptrack show 1
uptrack open 1
uptrack am 1
```

## Commands

- `fetch` - Poll sources and update local state
- `list` - Show paged patch list
- `list --interactive` - Interactive selection mode
- `show <id>` - Show patch metadata
- `open <id-or-link>` - Open full thread content in lore-style cleaned view
- `am <id>` - Run `b4 am <msgid>`
- `watch --interval <seconds>` - Continuous polling loop
- `projects` - List Patchwork projects from API
- `projects --config-only` - List configured projects only
- `mark <id> seen|ignored` - Update patch label/state

## ID Resolution

For commands that take an id, you can use:

- list index (for example `1`)
- exact message-id (for example `<abc@host>`)
- unique title/msgid substring
- lore URL (for `open`)

## Subsystem Filtering

Use `--subsystem` before the subcommand:

```bash
uptrack --subsystem display list
uptrack --subsystem phy show 1
uptrack --subsystem net open 3
```

Index-based ids are resolved within the filtered subsystem list.

## Configuration

Default config file: `config.toml`

Key sections:

- `[general]` - state/cache paths, color, notify, default subsystem
- `[sources.lore]` - feed list with labels and optional `subsystem`
- `[sources.patchwork]` - project list with optional `subsystem`, keyword filter
- `[notify]` - notification command settings
- `[b4]` - `b4 am` options

### Example subsystem tagging

```toml
[general]
default_subsystem = "default"

[sources.lore]
feeds = [
  { url = "https://lore.kernel.org/all/?q=drm%2Fmsm&x=A", label = "drm/msm", subsystem = "display" },
  { url = "https://lore.kernel.org/all/?q=phy%2Fqualcomm&x=A", label = "phy/qualcomm", subsystem = "phy" },
]

[sources.patchwork]
projects = [
  { id = 21, name = "dri-devel", subsystem = "display" },
  { id = 456, name = "linux-phy", subsystem = "phy" },
]
```

If `subsystem` is omitted on a feed/project, Uptrack uses `[general].default_subsystem`.

## State File

Default state path:

- `~/.local/share/uptrack/state.json`

Each entry includes:

- `title`
- `date`
- `url`
- `source` (`lore` or `patchwork`)
- `subsystem`
- `seen`
- `label`

## Notes

- Python TOML loading order: `tomllib` -> `tomli` -> built-in minimal fallback parser
- `open` uses `b4 mbox` and renders a cleaned thread view
- `watch` can send notifications when new patches are found

## License

MIT (SPDX: `MIT`)
