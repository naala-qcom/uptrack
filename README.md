# Uptrack

Generic upstream patch tracker for lore + patchwork. It is configured for Qualcomm display/PHY by default, and can be extended to any subsystem by editing `config.toml`.

## Files

- `config.toml` - feeds, patchwork projects, keywords, subsystem tags, notify, b4 options
- `uptrack.py` - main CLI
- `tracker.py` - compatibility wrapper (execs `uptrack.py`)
- `install.sh` - local install helper

## Install

```bash
cd /local/mnt/workspace/naala/upstream/upstream-tracker
./install.sh
```

Install creates:

- `~/.local/bin/uptrack` -> `uptrack.py`
- `~/.local/bin/qcom-tracker` -> `uptrack.py` (legacy alias)

## Usage

```bash
uptrack --help
uptrack fetch
uptrack list
uptrack list --interactive
uptrack --subsystem display list
uptrack --subsystem phy list
uptrack show 1
uptrack am 1
uptrack open 1
uptrack open '<20261030064149.14736-3-kaipeng94@gmail.com>'
uptrack open 'https://lore.kernel.org/all/20261030064149.14736-3-kaipeng94@gmail.com/'
uptrack mark 1 seen
uptrack mark 1 ignored
uptrack watch --interval 300
uptrack projects
uptrack projects --config-only
```

## Commands

- `fetch` - poll lore Atom feeds + patchwork API, dedupe by `msgid`, save state
- `list` - colorized list, paged via `less` by default
- `list --interactive` - interactive selection (`fzf` if available, else numbered prompt)
- `show <id>` - patch metadata + lore thread URL
- `am <id>` - run `b4 am <msgid>` with options from `[b4].am_opts`
- `open <id-or-link>` - open full thread content (cover letter + patches), lore-like cleaned view
- `watch` - continuous polling loop with optional desktop notifications
- `projects` - list patchwork projects (API, or config-only)
- `mark <id> seen|ignored` - update local state labels

`<id>` can be:

- exact message-id from state
- numeric index from `list`
- unique substring in msgid/title
- lore URL (for `open`)

## Subsystem Scaling

Use `subsystem = "..."` on each feed/project in `config.toml`.

Example:

```toml
[general]
default_subsystem = "display"

[sources.lore]
feeds = [
  { url = "https://lore.kernel.org/all/?q=drm%2Fmsm&x=A", label = "drm/msm", subsystem = "display" },
  { url = "https://lore.kernel.org/all/?q=audio+qcom&x=A", label = "qcom-audio", subsystem = "audio" },
]

[sources.patchwork]
projects = [
  { id = 21, name = "dri-devel", subsystem = "display" },
  { id = 107, name = "linux-arm-msm", subsystem = "display" },
  { id = 999, name = "alsa-devel", subsystem = "audio" },
]
```

Then filter views/actions with:

- `uptrack --subsystem display list`
- `uptrack --subsystem audio list`
- `uptrack --subsystem audio show 1`

## State

Default state file:

- `~/.local/share/uptrack/state.json`

Entry shape:

```json
{
  "<msgid>": {
    "title": "...",
    "date": "...",
    "url": "...",
    "source": "lore|patchwork",
    "subsystem": "display",
    "seen": false,
    "label": "new"
  }
}
```

## Notes

- Python 3.10 compatible (`tomllib` -> `tomli` -> internal fallback parser)
- `notify-send` optional (`[general].notify`, `[notify].command`)
- `b4` required for `am` and `open`

## Copyright

Copyright (c) 2026 Uptrack contributors.
SPDX-License-Identifier: MIT
