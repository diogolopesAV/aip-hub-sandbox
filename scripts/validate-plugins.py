#!/usr/bin/env python3
"""
Validate local hub plugins under plugins/.

Checks that each plugin directory has required manifests, at least one skill
whose frontmatter follows the Agent Skills spec (name equals the folder, kebab
case, description up to 1024 chars, metadata a map of string to string), and
that declared primitive paths exist.

Usage:
    python scripts/validate-plugins.py

Run from the repository root. Exits non-zero on any error.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import yaml

PLUGINS_DIR = Path("plugins")
REQUIRED_MANIFESTS = (
    Path("plugin.json"),
    Path(".cursor-plugin/plugin.json"),
    Path(".claude-plugin/plugin.json"),
    Path(".codex-plugin/plugin.json"),
)
PATH_FIELDS = ("hooks", "mcpServers", "rules", "agents", "skills")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SKILL_NAME_MAX = 64
SKILL_DESCRIPTION_MAX = 1024


def load_json(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print(f"error: {path}: expected a JSON object", file=sys.stderr)
        return None
    return data


def parse_frontmatter(text: str) -> dict | None:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    try:
        fields = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    return fields if isinstance(fields, dict) else None


def resolve_declared_path(plugin_dir: Path, raw: str) -> Path:
    cleaned = raw[2:] if raw.startswith("./") else raw
    return plugin_dir / cleaned


def validate_skill_md(skill_md: Path, errors: list[str]) -> None:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"{skill_md}: cannot read ({exc})")
        return
    fields = parse_frontmatter(text)
    if fields is None:
        errors.append(f"{skill_md}: missing or unparseable frontmatter")
        return

    name = fields.get("name")
    if not name:
        errors.append(f"{skill_md}: missing frontmatter 'name'")
    else:
        name = str(name)
        if name != skill_md.parent.name:
            errors.append(f"{skill_md}: 'name' ({name!r}) must match directory ({skill_md.parent.name!r})")
        if not SKILL_NAME_RE.match(name) or len(name) > SKILL_NAME_MAX:
            errors.append(f"{skill_md}: 'name' must be lowercase letters, digits and single hyphens, max {SKILL_NAME_MAX} chars")

    description = fields.get("description")
    if not description:
        errors.append(f"{skill_md}: missing frontmatter 'description'")
    elif len(str(description)) > SKILL_DESCRIPTION_MAX:
        errors.append(f"{skill_md}: 'description' is {len(str(description))} chars, max is {SKILL_DESCRIPTION_MAX}")

    metadata = fields.get("metadata")
    if metadata is None:
        return
    if not isinstance(metadata, dict):
        errors.append(f"{skill_md}: 'metadata' must be a map of string to string")
        return
    for key, value in metadata.items():
        if not isinstance(value, str):
            errors.append(
                f"{skill_md}: metadata.{key} must be a string (use a comma-separated string for lists, quote numbers), got {type(value).__name__}"
            )


def validate_manifest_paths(plugin_dir: Path, manifest: Path, data: dict, errors: list[str]) -> None:
    for field in PATH_FIELDS:
        raw = data.get(field)
        if not isinstance(raw, str) or not raw.strip():
            continue
        target = resolve_declared_path(plugin_dir, raw.strip())
        if not target.exists():
            errors.append(f"{manifest}: '{field}' path does not exist: {raw}")


def validate_plugin(plugin_dir: Path) -> list[str]:
    errors: list[str] = []
    name = plugin_dir.name

    for rel in REQUIRED_MANIFESTS:
        path = plugin_dir / rel
        if not path.is_file():
            errors.append(f"{name}: missing required manifest {rel}")
            continue
        data = load_json(path)
        if data is None:
            errors.append(f"{name}: invalid JSON in {rel}")
            continue
        if not data.get("name"):
            errors.append(f"{path}: missing required 'name'")
        elif data["name"] != name and rel == Path("plugin.json"):
            errors.append(f"{path}: 'name' ({data['name']!r}) must match directory ({name!r})")
        validate_manifest_paths(plugin_dir, path, data, errors)

    skills_root = plugin_dir / "skills"
    if not skills_root.is_dir():
        errors.append(f"{name}: missing skills/ directory")
        return errors

    skill_files = sorted(skills_root.glob("*/SKILL.md"))
    if not skill_files:
        errors.append(f"{name}: no skills/*/SKILL.md found")
    for skill_md in skill_files:
        validate_skill_md(skill_md, errors)

    return errors


def main() -> int:
    if not PLUGINS_DIR.is_dir():
        print("error: plugins/ not found — run from the repository root", file=sys.stderr)
        return 1

    plugin_dirs = sorted(p for p in PLUGINS_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not plugin_dirs:
        print("error: no plugin directories found under plugins/", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    for plugin_dir in plugin_dirs:
        all_errors.extend(validate_plugin(plugin_dir))

    if all_errors:
        print("Plugin validation failed:", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"ok: validated {len(plugin_dirs)} plugin(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
