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

APM_YML = Path("apm.yml")
TAXONOMY_YML = Path("taxonomy.yml")
BU_PREFIX = "bu:"
COMPANY_WIDE = "company-wide"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FREE_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9 -]*$")
MAX_FREE_TAGS = 5
# Classification belongs to the plugin; a skill inherits it and must not carry its own.
SKILL_FORBIDDEN_METADATA = ("category", "author", "bu", "business-unit")


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
    for key in SKILL_FORBIDDEN_METADATA:
        if key in metadata:
            errors.append(f"{skill_md}: metadata.{key} not allowed; a skill inherits classification from its plugin")
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


def validate_catalog() -> list[str]:
    """Validate classification of every apm.yml marketplace entry (hub and external) against taxonomy.yml."""
    errors: list[str] = []
    try:
        taxonomy = yaml.safe_load(TAXONOMY_YML.read_text(encoding="utf-8")) or {}
        catalog = yaml.safe_load(APM_YML.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [f"cannot read {TAXONOMY_YML} or {APM_YML}: {exc}"]

    areas = {str(a) for a in taxonomy.get("functional_areas") or []}
    units = {str(u.get("key")) for u in taxonomy.get("business_units") or [] if isinstance(u, dict)}
    packages = (catalog.get("marketplace") or {}).get("packages") or []

    for pkg in packages:
        name = str(pkg.get("name"))
        where = f"apm.yml package {name!r}"

        if pkg.get("category") not in areas:
            errors.append(f"{where}: category {pkg.get('category')!r} is not in taxonomy functional_areas {sorted(areas)}")

        tags = [str(t) for t in pkg.get("tags") or []]
        bu_keys = [t[len(BU_PREFIX):] for t in tags if t.startswith(BU_PREFIX)]
        other_prefixed = [t for t in tags if ":" in t and not t.startswith(BU_PREFIX)]
        free_tags = [t for t in tags if ":" not in t]
        if not bu_keys:
            errors.append(f"{where}: needs at least one '{BU_PREFIX}<key>' tag (use '{BU_PREFIX}{COMPANY_WIDE}' for company-wide)")
        for key in bu_keys:
            if key not in units:
                errors.append(f"{where}: unknown business unit {BU_PREFIX}{key} (allowed: {sorted(units)})")
        if COMPANY_WIDE in bu_keys and len(bu_keys) > 1:
            errors.append(f"{where}: '{BU_PREFIX}{COMPANY_WIDE}' cannot be combined with other business units")
        for tag in other_prefixed:
            errors.append(f"{where}: tag {tag!r} uses a reserved prefix (only '{BU_PREFIX}' is allowed)")
        if len(free_tags) > MAX_FREE_TAGS:
            errors.append(f"{where}: {len(free_tags)} search tags, max is {MAX_FREE_TAGS}")
        for tag in free_tags:
            if not FREE_TAG_RE.match(tag):
                errors.append(f"{where}: search tag {tag!r} must be lowercase letters, digits, spaces or hyphens")

        author = pkg.get("author")
        if not isinstance(author, str) or not EMAIL_RE.match(author):
            errors.append(f"{where}: author must be a team email address string, got {author!r}")
            continue

        source = str(pkg.get("source", ""))
        if source.startswith("./"):
            for rel in REQUIRED_MANIFESTS:
                manifest = Path(source) / rel
                data = load_json(manifest) if manifest.is_file() else None
                m_author = (data or {}).get("author") or {}
                if m_author.get("name") != author or m_author.get("email", author) != author:
                    errors.append(f"{manifest}: author must be name={author!r} (and email, if set) to match apm.yml")

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
    all_errors.extend(validate_catalog())

    if all_errors:
        print("Plugin validation failed:", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"ok: validated {len(plugin_dirs)} plugin(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
