#!/usr/bin/env python3
#
# Copyright (c) 2026 Uptrack contributors
# SPDX-License-Identifier: MIT
#
"""Uptrack: generic upstream patch tracker."""

from __future__ import annotations

import argparse
import json
import mailbox
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

USER_AGENT = "b4/0.16"
LORE_USER_AGENTS = [
    "b4/0.16",
    "b4/0.16-dev",
    "Wget/1.21",
    "Mozilla/5.0",
]
DEFAULT_CONFIG = "config.toml"


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


def expand_path(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


def _parse_value(raw: str) -> Any:
    value = raw.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value in ("true", "false"):
        return value == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _parse_inline_table(raw: str) -> dict[str, Any]:
    body = raw.strip().strip("{} ")
    fields: dict[str, Any] = {}
    parts = re.split(r",\s*(?=\w+\s*=)", body)
    for part in parts:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        fields[k.strip()] = _parse_value(v)
    return fields


def _parse_toml_minimal(path: Path) -> dict[str, Any]:
    """Schema-focused parser for this project's config.toml shape."""
    text = path.read_text(encoding="utf-8")
    cfg: dict[str, Any] = {
        "general": {},
        "sources": {"lore": {}, "patchwork": {}},
        "notify": {},
        "b4": {},
    }
    section: str | None = None
    array_key: str | None = None
    array_buf: list[str] = []

    def set_value(sec: str, key: str, value: Any) -> None:
        if sec == "sources.lore":
            cfg["sources"]["lore"][key] = value
        elif sec == "sources.patchwork":
            cfg["sources"]["patchwork"][key] = value
        else:
            cfg.setdefault(sec, {})[key] = value

    for line in text.splitlines():
        line = re.sub(r"\s+#.*$", "", line).strip()
        if not line:
            continue

        if array_key:
            array_buf.append(line)
            if line.endswith("]"):
                combined = " ".join(array_buf)
                inner = combined[combined.find("[") + 1 : combined.rfind("]")]
                if "{" in inner:
                    items = [_parse_inline_table(x) for x in re.findall(r"\{[^{}]+\}", inner)]
                else:
                    items = [s for s in re.findall(r'"([^"]+)"', inner)]
                set_value(section or "", array_key, items)
                array_key = None
                array_buf = []
            continue

        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue

        if "=" not in line or not section:
            continue

        key, value = [x.strip() for x in line.split("=", 1)]
        if value.startswith("[") and not value.endswith("]"):
            array_key = key
            array_buf = [value]
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if inner.startswith("{"):
                set_value(section, key, [_parse_inline_table(x) for x in re.findall(r"\{[^{}]+\}", inner)])
            else:
                set_value(section, key, [s for s in re.findall(r'"([^"]+)"', inner)])
            continue

        set_value(section, key, _parse_value(value))

    return cfg


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    data = None

    try:
        import tomllib  # py311+

        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        try:
            import tomli

            data = tomli.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = _parse_toml_minimal(path)

    general = data.get("general", {})
    state_file = expand_path(general.get("state_file", "~/.local/share/uptrack/state.json"))
    cache_dir = expand_path(general.get("cache_dir", "~/.cache/uptrack"))

    data.setdefault("general", {})
    data["general"]["state_file"] = str(state_file)
    data["general"]["cache_dir"] = str(cache_dir)
    data["general"].setdefault("notify", True)
    data["general"].setdefault("color", True)
    data["general"].setdefault("default_subsystem", "default")

    data.setdefault("sources", {})
    data["sources"].setdefault("lore", {})
    data["sources"].setdefault("patchwork", {})
    data["sources"]["lore"].setdefault("enabled", True)
    data["sources"]["lore"].setdefault("feeds", [])
    data["sources"]["patchwork"].setdefault("enabled", True)
    data["sources"]["patchwork"].setdefault("base_url", "https://patchwork.kernel.org/api")
    data["sources"]["patchwork"].setdefault("projects", [])
    data["sources"]["patchwork"].setdefault("keywords", [])

    data.setdefault("notify", {})
    data["notify"].setdefault("command", "notify-send")
    data["notify"].setdefault("urgency", "normal")

    data.setdefault("b4", {})
    data["b4"].setdefault("am_opts", "--outdir /tmp/uptrack-patches")

    return data


def load_state(state_path: Path) -> dict[str, dict[str, Any]]:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state_path: Path, state: dict[str, dict[str, Any]]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def http_get_json(url: str) -> Any:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def http_get_text(url: str, accept: str = "application/atom+xml") -> str:
    # lore can return 403 HTML for some user-agents; try a small fallback set.
    if shutil.which("curl"):
        for ua in LORE_USER_AGENTS:
            cmd = [
                "curl",
                "-sSL",
                "-A",
                ua,
                "-H",
                f"Accept: {accept}",
                url,
            ]
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
            out = proc.stdout or ""
            if proc.returncode == 0 and ("<feed" in out or out.lstrip().startswith("<?xml")):
                return out
    for ua in LORE_USER_AGENTS:
        req = Request(url, headers={"User-Agent": ua, "Accept": accept})
        with urlopen(req, timeout=30) as resp:
            out = resp.read().decode("utf-8", errors="replace")
            if "<feed" in out or out.lstrip().startswith("<?xml"):
                return out
    raise RuntimeError(f"Failed to fetch XML feed: {url}")


def normalize_msgid(msgid: str) -> str:
    m = msgid.strip()
    if m.startswith("<") and m.endswith(">"):
        return m
    if "@" in m and not (m.startswith("<") and m.endswith(">")):
        return f"<{m}>"
    return m


def extract_msgid_from_text(text: str) -> str | None:
    if not text:
        return None
    text = unquote(text)
    m = re.search(r"<([^>]+@[^>]+)>", text)
    if m:
        return normalize_msgid(f"<{m.group(1)}>")
    m = re.search(r"([A-Za-z0-9_.%+\-]+@[A-Za-z0-9_.\-]+)", text)
    if m:
        return normalize_msgid(m.group(1))
    return None


def extract_msgid_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    segments = [s for s in parsed.path.split("/") if s]
    candidates = list(reversed(segments))
    for c in candidates:
        msgid = extract_msgid_from_text(c)
        if msgid:
            return msgid
    return extract_msgid_from_text(url)


def fetch_lore_items(config: dict[str, Any]) -> list[dict[str, Any]]:
    lore_cfg = config["sources"]["lore"]
    if not lore_cfg.get("enabled", True):
        return []

    items: list[dict[str, Any]] = []
    default_subsystem = str(config.get("general", {}).get("default_subsystem", "default"))
    for feed in lore_cfg.get("feeds", []):
        url = feed.get("url")
        if not url:
            continue
        try:
            xml_text = http_get_text(url)
            root = ET.fromstring(xml_text)
        except Exception as exc:
            print(f"[warn] lore fetch failed: {url}: {exc}", file=sys.stderr)
            continue

        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns):
            title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
            date = (entry.findtext("a:updated", default="", namespaces=ns) or "").strip()
            entry_id = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()

            link_url = ""
            link_elem = entry.find("a:link", ns)
            if link_elem is not None:
                link_url = (link_elem.attrib.get("href") or "").strip()
            url_value = link_url or entry_id

            msgid = (
                extract_msgid_from_text(entry_id)
                or extract_msgid_from_url(link_url)
                or extract_msgid_from_url(url_value)
            )
            if not msgid:
                continue

            items.append(
                {
                    "msgid": msgid,
                    "title": title or msgid,
                    "date": date,
                    "url": url_value,
                    "source": "lore",
                    "feed_label": feed.get("label", ""),
                    "subsystem": str(feed.get("subsystem", default_subsystem)),
                }
            )
    return items


def _iter_patchwork_results(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return [x for x in data["results"] if isinstance(x, dict)]
    return []


def fetch_patchwork_items(config: dict[str, Any]) -> list[dict[str, Any]]:
    pw_cfg = config["sources"]["patchwork"]
    if not pw_cfg.get("enabled", True):
        return []

    base = pw_cfg.get("base_url", "https://patchwork.kernel.org/api").rstrip("/")
    keywords = [k.lower() for k in pw_cfg.get("keywords", [])]
    default_subsystem = str(config.get("general", {}).get("default_subsystem", "default"))

    items: list[dict[str, Any]] = []
    for project in pw_cfg.get("projects", []):
        pid = project.get("id")
        if pid is None:
            continue
        url = f"{base}/patches/?project={pid}&order=-date&page=1"
        try:
            data = http_get_json(url)
        except Exception as exc:
            print(f"[warn] patchwork fetch failed for project {pid}: {exc}", file=sys.stderr)
            continue

        for patch in _iter_patchwork_results(data):
            title = str(patch.get("name") or patch.get("title") or "").strip()
            if keywords and title:
                t = title.lower()
                if not any(k in t for k in keywords):
                    continue

            msgid = normalize_msgid(str(patch.get("msgid") or "").strip())
            if not msgid:
                continue

            date = str(patch.get("date") or patch.get("submitter_date") or "")
            web_url = str(patch.get("web_url") or patch.get("url") or "")
            items.append(
                {
                    "msgid": msgid,
                    "title": title or msgid,
                    "date": date,
                    "url": web_url,
                    "source": "patchwork",
                    "project": project.get("name", str(pid)),
                    "subsystem": str(project.get("subsystem", default_subsystem)),
                }
            )
    return items


def merge_items(state: dict[str, dict[str, Any]], items: list[dict[str, Any]]) -> tuple[int, int]:
    added = 0
    updated = 0
    for item in items:
        msgid = item["msgid"]
        prev = state.get(msgid)
        if not prev:
            state[msgid] = {
                "title": item.get("title", msgid),
                "date": item.get("date", ""),
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "subsystem": item.get("subsystem", "default"),
                "seen": False,
                "label": "new",
            }
            added += 1
            continue

        # Keep user state fields, refresh metadata.
        prev["title"] = item.get("title", prev.get("title", msgid))
        prev["date"] = item.get("date", prev.get("date", ""))
        prev["url"] = item.get("url", prev.get("url", ""))
        prev["source"] = item.get("source", prev.get("source", ""))
        prev["subsystem"] = item.get("subsystem", prev.get("subsystem", "default"))
        updated += 1
    return added, updated


def is_tty() -> bool:
    return sys.stdout.isatty() and sys.stdin.isatty()


def colorize(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{color}{text}{Colors.RESET}"


def parse_date_key(s: str) -> float:
    if not s:
        return 0.0
    s2 = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s2).timestamp()
    except Exception:
        return 0.0


def sorted_entries(
    state: dict[str, dict[str, Any]],
    subsystem: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    rows = state.items()
    if subsystem:
        rows = [(k, v) for (k, v) in rows if str(v.get("subsystem", "default")) == subsystem]
    return sorted(rows, key=lambda kv: parse_date_key(kv[1].get("date", "")), reverse=True)


def lore_thread_url(msgid: str) -> str:
    clean = msgid.strip("<>")
    return f"https://lore.kernel.org/all/{quote(clean, safe='@._+-')}/"


def resolve_id(query: str, state: dict[str, dict[str, Any]], subsystem: str | None = None) -> str | None:
    if query in state:
        if subsystem and str(state[query].get("subsystem", "default")) != subsystem:
            return None
        return query

    entries = sorted_entries(state, subsystem=subsystem)
    if query.isdigit():
        idx = int(query)
        if 1 <= idx <= len(entries):
            return entries[idx - 1][0]

    pool = entries if subsystem else list(state.items())
    matches = [msgid for (msgid, item) in pool if query in msgid or query.lower() in item.get("title", "").lower()]
    if len(matches) == 1:
        return matches[0]
    return None


def resolve_any_target(query: str, state: dict[str, dict[str, Any]], subsystem: str | None = None) -> str | None:
    msgid = resolve_id(query, state, subsystem=subsystem)
    if msgid:
        return msgid
    msgid = extract_msgid_from_url(query) or extract_msgid_from_text(query)
    if msgid:
        return normalize_msgid(msgid)
    return None


def maybe_notify(config: dict[str, Any], title: str, body: str) -> None:
    if not config["general"].get("notify", True):
        return
    cmd = (config.get("notify", {}).get("command") or "").strip()
    if not cmd:
        return
    if shutil.which(cmd) is None:
        return
    # Avoid noisy failures in non-desktop/headless shells.
    if cmd == "notify-send":
        if not (
            os.environ.get("DISPLAY")
            or os.environ.get("WAYLAND_DISPLAY")
            or os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        ):
            return
    urgency = config.get("notify", {}).get("urgency", "normal")
    try:
        subprocess.run([cmd, "-u", urgency, title, body], check=False, capture_output=True)
    except Exception:
        pass


def cmd_fetch(args: argparse.Namespace, config: dict[str, Any], state: dict[str, dict[str, Any]]) -> int:
    lore_items = fetch_lore_items(config)
    pw_items = fetch_patchwork_items(config)
    all_items = lore_items + pw_items

    added, updated = merge_items(state, all_items)
    save_state(expand_path(config["general"]["state_file"]), state)

    print(f"Fetched {len(all_items)} items (lore={len(lore_items)} patchwork={len(pw_items)}), new={added}, updated={updated}")

    if added > 0:
        maybe_notify(config, "uptrack", f"{added} new patch(es)")
    return 0


def print_list(entries: list[tuple[str, dict[str, Any]]], color: bool) -> None:
    if not entries:
        print("No patches in state. Run: uptrack fetch")
        return

    for i, (msgid, item) in enumerate(entries, start=1):
        label = item.get("label", "new")
        seen = item.get("seen", False)
        source = item.get("source", "?")
        subsystem = item.get("subsystem", "default")
        marker = " " if seen else "*"
        if label == "ignored":
            label_str = colorize(label, Colors.RED, color)
        elif label == "new":
            label_str = colorize(label, Colors.GREEN, color)
        else:
            label_str = colorize(label, Colors.YELLOW, color)

        idx = colorize(f"{i:3d}", Colors.CYAN, color)
        src = colorize(source, Colors.MAGENTA, color)
        print(f"{idx} {marker} [{src}] [{subsystem}] [{label_str}] {item.get('date', '')} {item.get('title', '')}")
        if msgid:
            print(f"      {msgid}")


def format_list(entries: list[tuple[str, dict[str, Any]]], color: bool) -> str:
    if not entries:
        return "No patches in state. Run: uptrack fetch\n"

    lines: list[str] = []
    for i, (msgid, item) in enumerate(entries, start=1):
        label = item.get("label", "new")
        seen = item.get("seen", False)
        source = item.get("source", "?")
        subsystem = item.get("subsystem", "default")
        marker = " " if seen else "*"
        if label == "ignored":
            label_str = colorize(label, Colors.RED, color)
        elif label == "new":
            label_str = colorize(label, Colors.GREEN, color)
        else:
            label_str = colorize(label, Colors.YELLOW, color)

        idx = colorize(f"{i:3d}", Colors.CYAN, color)
        src = colorize(source, Colors.MAGENTA, color)
        lines.append(f"{idx} {marker} [{src}] [{subsystem}] [{label_str}] {item.get('date', '')} {item.get('title', '')}")
        if msgid:
            lines.append(f"      {msgid}")
    return "\n".join(lines) + "\n"


def output_with_pager(text: str) -> None:
    if not is_tty() or shutil.which("less") is None:
        print(text, end="")
        return
    env = os.environ.copy()
    env.setdefault("LESS", "FRX")
    subprocess.run(["less"], input=text, text=True, env=env, check=False)


def _list_with_fzf(entries: list[tuple[str, dict[str, Any]]]) -> str | None:
    if shutil.which("fzf") is None or not is_tty():
        return None

    lines = []
    for i, (msgid, item) in enumerate(entries, start=1):
        line = (
            f"{i}\t{item.get('source', '')}\t{item.get('subsystem', 'default')}\t"
            f"{item.get('label', '')}\t{item.get('date', '')}\t{item.get('title', '')}\t{msgid}"
        )
        lines.append(line)

    try:
        proc = subprocess.run(
            ["fzf", "--ansi", "--prompt", "patch> ", "--delimiter", "\t", "--with-nth", "1,2,3,4,5,6"],
            input="\n".join(lines),
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception:
        return None

    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    selected = proc.stdout.strip().split("\t")
    if len(selected) < 7:
        return None
    return selected[-1]


def _list_with_prompt(entries: list[tuple[str, dict[str, Any]]]) -> str | None:
    if not is_tty():
        return None
    try:
        raw = input("Select patch index to show (Enter to skip): ").strip()
    except EOFError:
        return None
    if not raw:
        return None
    if not raw.isdigit():
        return None
    idx = int(raw)
    if idx < 1 or idx > len(entries):
        return None
    return entries[idx - 1][0]


def show_patch(msgid: str, item: dict[str, Any]) -> None:
    print(f"msgid:   {msgid}")
    print(f"title:   {item.get('title', '')}")
    print(f"date:    {item.get('date', '')}")
    print(f"source:  {item.get('source', '')}")
    print(f"subsys:  {item.get('subsystem', 'default')}")
    print(f"label:   {item.get('label', '')}")
    print(f"seen:    {item.get('seen', False)}")
    print(f"url:     {item.get('url', '')}")
    print(f"thread:  {lore_thread_url(msgid)}")


def cmd_list(args: argparse.Namespace, config: dict[str, Any], state: dict[str, dict[str, Any]]) -> int:
    entries = sorted_entries(state, subsystem=args.subsystem)
    color = bool(config["general"].get("color", True))
    if args.interactive:
        print_list(entries, color)
    else:
        output_with_pager(format_list(entries, color))
        return 0

    selected = _list_with_fzf(entries)
    if selected is None:
        selected = _list_with_prompt(entries)
    if selected and selected in state:
        print()
        show_patch(selected, state[selected])
    return 0


def cmd_show(args: argparse.Namespace, config: dict[str, Any], state: dict[str, dict[str, Any]]) -> int:
    msgid = resolve_id(args.id, state, subsystem=args.subsystem)
    if not msgid:
        print(f"Patch not found: {args.id}", file=sys.stderr)
        return 2
    show_patch(msgid, state[msgid])
    return 0


def cmd_am(args: argparse.Namespace, config: dict[str, Any], state: dict[str, dict[str, Any]]) -> int:
    msgid = resolve_id(args.id, state, subsystem=args.subsystem)
    if not msgid:
        print(f"Patch not found: {args.id}", file=sys.stderr)
        return 2

    b4_path = shutil.which("b4") or "/usr2/naala/bin/b4"
    if not Path(b4_path).exists():
        print("b4 not found in PATH", file=sys.stderr)
        return 2

    am_opts = config.get("b4", {}).get("am_opts", "--outdir /tmp/uptrack-patches")
    cmd = [b4_path, "am", msgid] + am_opts.split()
    print("Running:", " ".join(cmd))
    return subprocess.run(cmd, check=False).returncode


def _decode_message_text(msg: mailbox.mboxMessage) -> str:
    if msg.is_multipart():
        parts: list[str] = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_filename():
                continue
            ctype = part.get_content_type()
            if ctype != "text/plain":
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            parts.append(payload.decode(charset, errors="replace"))
        return "\n".join(parts).strip()
    payload = msg.get_payload(decode=True)
    if payload is None:
        raw = msg.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace").strip()


def _render_lore_like_thread(mbox_text: str) -> str:
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as tf:
        tf.write(mbox_text)
        tmp_path = tf.name

    out_lines: list[str] = []
    try:
        mbox = mailbox.mbox(tmp_path)
        messages = list(mbox)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    for idx, msg in enumerate(messages, start=1):
        if idx > 1:
            out_lines.append("\n" + ("-" * 78) + "\n")
        out_lines.append(f"Message {idx}/{len(messages)}")
        out_lines.append(f"From: {msg.get('From', '')}")
        out_lines.append(f"To: {msg.get('To', '')}")
        cc = msg.get("Cc", "")
        if cc:
            out_lines.append(f"Cc: {cc}")
        out_lines.append(f"Subject: {msg.get('Subject', '')}")
        out_lines.append(f"Date: {msg.get('Date', '')}")
        out_lines.append(f"Message-Id: {msg.get('Message-Id', msg.get('Message-ID', ''))}")
        out_lines.append("")
        body = _decode_message_text(msg)
        out_lines.append(body if body else "[no text/plain body]")
    return "\n".join(out_lines).rstrip() + "\n"


def cmd_open(args: argparse.Namespace, config: dict[str, Any], state: dict[str, dict[str, Any]]) -> int:
    target = args.id_or_link
    msgid = resolve_any_target(target, state, subsystem=args.subsystem)
    if not msgid:
        print(f"Could not resolve patch target: {target}", file=sys.stderr)
        return 2

    b4_path = shutil.which("b4") or "/usr2/naala/bin/b4"
    if not Path(b4_path).exists():
        print("b4 not found in PATH", file=sys.stderr)
        return 2

    b4_cmd = [b4_path, "mbox", msgid, "-o", "-"]
    proc = subprocess.run(b4_cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if err:
            print(err, file=sys.stderr)
        return proc.returncode

    rendered = _render_lore_like_thread(proc.stdout or "")
    if is_tty() and shutil.which("less"):
        env = os.environ.copy()
        env.setdefault("LESS", "FRX")
        return subprocess.run(["less", "-R"], input=rendered, text=True, env=env, check=False).returncode
    print(rendered, end="")
    return 0


def cmd_watch(args: argparse.Namespace, config: dict[str, Any], state: dict[str, dict[str, Any]]) -> int:
    interval = args.interval
    state_path = expand_path(config["general"]["state_file"])
    print(f"Watching for new patches every {interval}s. Ctrl-C to stop.")

    while True:
        before = len(state)
        lore_items = fetch_lore_items(config)
        pw_items = fetch_patchwork_items(config)
        added, _updated = merge_items(state, lore_items + pw_items)

        if added > 0:
            print(f"[{datetime.now().isoformat(timespec='seconds')}] new patches: {added}")
            maybe_notify(config, "uptrack", f"{added} new patch(es)")
        else:
            print(f"[{datetime.now().isoformat(timespec='seconds')}] no new patches (total={len(state)})")

        if len(state) != before:
            save_state(state_path, state)

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0


def cmd_projects(args: argparse.Namespace, config: dict[str, Any], state: dict[str, dict[str, Any]]) -> int:
    pw_cfg = config["sources"]["patchwork"]
    base = pw_cfg.get("base_url", "https://patchwork.kernel.org/api").rstrip("/")

    if args.config_only:
        for p in pw_cfg.get("projects", []):
            print(f"{p.get('id')}\t{p.get('name', '')}\t{p.get('subsystem', '')}")
        return 0

    try:
        data = http_get_json(f"{base}/projects/?page=1")
    except Exception as exc:
        print(f"Failed to fetch projects from patchwork API: {exc}", file=sys.stderr)
        return 2

    rows = _iter_patchwork_results(data)
    if not rows:
        print("No projects returned by API.")
        return 0

    for p in rows:
        pid = p.get("id")
        name = p.get("name", "")
        link = p.get("link_name", "")
        print(f"{pid}\t{name}\t{link}")
    return 0


def cmd_mark(args: argparse.Namespace, config: dict[str, Any], state: dict[str, dict[str, Any]]) -> int:
    msgid = resolve_id(args.id, state, subsystem=args.subsystem)
    if not msgid:
        print(f"Patch not found: {args.id}", file=sys.stderr)
        return 2

    entry = state[msgid]
    if args.status == "seen":
        entry["seen"] = True
        entry["label"] = "seen"
    elif args.status == "ignored":
        entry["seen"] = True
        entry["label"] = "ignored"

    save_state(expand_path(config["general"]["state_file"]), state)
    print(f"Marked {msgid} as {entry['label']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="uptrack: generic upstream patch tracker")
    p.add_argument("--config", default=DEFAULT_CONFIG, help="Path to config.toml")
    p.add_argument(
        "--subsystem",
        default=None,
        help="Filter to a subsystem tag (from feed/project 'subsystem' in config)",
    )

    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("fetch", help="Fetch lore + patchwork and update state")
    p_list = sp.add_parser("list", help="Patch list (paged via less by default)")
    p_list.add_argument(
        "--interactive",
        action="store_true",
        help="Enable interactive selection (fzf/numbered prompt)",
    )

    p_show = sp.add_parser("show", help="Show patch details")
    p_show.add_argument("id", help="msgid, list index, or unique substring")

    p_am = sp.add_parser("am", help="Run b4 am for a patch")
    p_am.add_argument("id", help="msgid, list index, or unique substring")

    p_open = sp.add_parser("open", help="Open full patch thread content (series + cover letter)")
    p_open.add_argument("id_or_link", help="list index, msgid, unique substring, or lore URL")

    p_watch = sp.add_parser("watch", help="Fetch loop with notifications")
    p_watch.add_argument("--interval", type=int, default=300, help="Polling interval in seconds")

    p_projects = sp.add_parser("projects", help="List patchwork project IDs")
    p_projects.add_argument("--config-only", action="store_true", help="Only print projects from config")

    p_mark = sp.add_parser("mark", help="Mark patch state")
    p_mark.add_argument("id", help="msgid, list index, or unique substring")
    p_mark.add_argument("status", choices=["seen", "ignored"])

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config(Path(args.config))
    state_path = expand_path(config["general"]["state_file"])
    state = load_state(state_path)

    handlers = {
        "fetch": cmd_fetch,
        "list": cmd_list,
        "show": cmd_show,
        "am": cmd_am,
        "open": cmd_open,
        "watch": cmd_watch,
        "projects": cmd_projects,
        "mark": cmd_mark,
    }
    return handlers[args.cmd](args, config, state)


if __name__ == "__main__":
    raise SystemExit(main())
