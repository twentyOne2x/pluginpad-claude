#!/usr/bin/env python3
"""Check the PluginPad marketplace and plugin before anyone installs or publishes it.

What it checks (Python standard library only):
  1. .claude-plugin/marketplace.json, each listed plugin's .claude-plugin/plugin.json
     and its .mcp.json parse and carry the fields Claude Code and the directory need, and
     hold nothing else that could run code: no hooks, scripts, ! shell blocks, extra server
     keys or marketplace entry fields, and no URL that is not https on a PluginPad host.
  2. Every MCP tool a skill or command names exists on the server, is visible to the
     model in Claude Code, needs no sign-in, and every tool a skill or command
     pre-approves through allowed-tools is read-only.
  3. Copy rules: no em or en dashes, no old product name or launchpad words outside tool ids, the
     30/70 fee split stated, a plugin README of 40 words or more. A fee split, limit, size or
     image format the copy states, and a field or value a skill or command names in backticks,
     must come from the schemas or descriptions of the tools the plugin uses.

Usage:
  python3 scripts/check_plugin.py --live                      tool list read from the live server
  python3 scripts/check_plugin.py --tools-file FILE           tool list read from a saved snapshot
  python3 scripts/check_plugin.py --live --snapshot-out FILE  also save the reduced live list

Exit codes: 0 clean, 1 problems found (one ERROR line each), 2 the tool list could not be read.
"""

import argparse
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

KEBAB = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
TOOL_TOKEN = re.compile(r"mcp__[A-Za-z0-9_-]+")
# Claude Code runs a command written as ! plus a backtick, and a fenced block opened by three or more
# backticks and !. Spaces, tabs and zero width marks between the marks are skipped, so they are refused too.
GAP = r"(?:[^\S\r\n]|[\u200b-\u200f\u2060\ufeff])*"
SHELL_BANG = re.compile("!" + GAP + "`|`{3,}" + GAP + "!")
# Old product names that must not reach the copy. The server still prefixes its tool ids with the first
# one, so it is written in two parts here and this file does not spell it.
OLD_PREFIX = "at" + "tn"
OLD_NAMES = re.compile("(?i)%s|launchpad" % OLD_PREFIX)
OLD_SPLIT = re.compile(r"(?i)\b(?:25|75)\s*(?:percent|%)|\b(?:25/75|75/25|2500/7500)\b")
DASHES = ("—", "–")
YAML_SPECIAL = set("{}[]*&#!|>%@`")
# The only frontmatter keys the plugin uses. Any other spelling (quoted, escaped, tagged, anchored,
# camelCase) could be what a YAML loader reads as allowed-tools, so it fails closed.
FRONTMATTER_KEYS = {"name", "description", "allowed-tools", "argument-hint"}
# The one transport the check can read: it lists the tools by POSTing JSON-RPC (Streamable HTTP).
REMOTE_TYPE = "http"
# A server entry holds nothing else: no command, args, env, headers, headersHelper or oauth settings.
SERVER_KEYS = {"type", "url"}
REQUIRED_FACTS = ("30 percent", "70 percent")
# A limit or a percentage in the copy is a claim; the server's tool list must state it.
LIMIT = re.compile(r"(?i)\b\d+(?:\.\d+)?\s+(?:launch(?:es)?\s+|SOL\s+)?(?:an?|per)\s+(?:hour|day|week)")
PERCENT = re.compile(r"(?i)\b(\d{1,3})\s*(?:percent|%)")
SIZE = re.compile(r"(?i)\b\d+(?:\.\d+)?\s*(?:[kmg]i?b|bytes?)\b")
# A file name such as icon.png is a path, not a claim about image formats.
IMAGE_FORMAT = re.compile(r"(?i)(?<!\w\.)\b(png|jpe?g|webp|gif|svg)\b")
IMAGE_TYPES = {"jpg": "jpeg", "svg": "svg+xml"}
# A name in backticks in a skill or command: a field, or a value such as pumpfun, 3000 or "0".
NAME = re.compile(r'`"?([A-Za-z0-9_][A-Za-z0-9_:.-]*)"?`')
# Public copy and config may link only these hosts (no internal hosting addresses), exactly as written.
ALLOWED_HOSTS = {"pluginpad.app", "api.pluginpad.app", "anthropic.com", "json.schemastore.org"}
# A link in the copy runs to the next space or closing mark; sentence punctuation after it is not part of it.
LINK = re.compile(r"""(?i)https?://[^\s<>"'`)\]]+""")
# Other spellings a reader or a browser follows as a link: https:\\host, //host, www.host, mailto:, ftp:// and javascript:.
OTHER_LINK = re.compile(r"(?i)https?:\\|(?<![\w:/.])//[\w-]+\.|\bwww\.|\bmailto:|\bftp://|\bjavascript:")
# The fee split keeps its direction: a percentage is followed by its own party, and a field name by its own value.
PARTY = re.compile(r"(?i)\b(pluginpad|platform|creator)\b")
CREATOR_FEES = re.compile(r"(?i)\bcreator fees?\b")
BPS_NAME = re.compile(r"(platformBps|creatorBps)")
BPS_VALUE = re.compile(r"(platformBps|creatorBps)`?\s*(\d+)?")
SENTENCE_END = re.compile(r"[.!?](?:\s|$)|\n")
FACT_FILES = ("skills/launch-token/SKILL.md", "README.md")
MIN_README_WORDS = 40
PROTOCOL_VERSION = "2025-06-18"
# Server tools whose answer Claude Code will not inline (over 50,000 characters, so it is
# saved to a file). Value: characters measured in a headless session on 2026-10-18.
OVERSIZED_TOOLS = {OLD_PREFIX + ".launchpad.catalog": 207690}
# What the plugin folder may ship: the README promises no hooks, no agents and no local scripts.
SHIPPED = re.compile(r"\.claude-plugin/plugin\.json|\.mcp\.json|README\.md|LICENSE|skills/[a-z0-9-]+/SKILL\.md|commands/[a-z0-9-]+\.md")
MANIFEST_KEYS = {"$schema", "name", "displayName", "version", "description", "author", "homepage", "repository",
                 "license", "keywords", "icon", "documentationUrl", "supportUrl", "privacyPolicyUrl", "termsOfServiceUrl"}
# A marketplace entry lists the same metadata plus its source: no inline hooks, servers, agents or settings.
ENTRY_KEYS = (MANIFEST_KEYS - {"$schema"}) | {"source", "category", "tags"}


def normalize(name):
    """Claude Code replaces every character outside A-Z, a-z, 0-9, _ and - with _."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def fee_splits(node, found):
    """Collect [platformBps, creatorBps] const pairs anywhere in a JSON schema."""
    if isinstance(node, dict):
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        pair = [(props.get(k) or {}).get("const") for k in ("platformBps", "creatorBps")]
        if all(type(v) is int for v in pair) and pair not in found:
            found.append(pair)
        for value in node.values():
            fee_splits(value, found)
    elif isinstance(node, list):
        for value in node:
            fee_splits(value, found)
    return found


def schema_terms(node, found):
    """Collect the property names and the enum and const values declared anywhere in a JSON schema."""
    if isinstance(node, dict):
        if isinstance(node.get("properties"), dict):
            found.update(node["properties"])
        for key in ("enum", "const"):
            if key in node:
                found.update(str(v) for v in (node[key] if isinstance(node[key], list) else [node[key]]))
        for value in node.values():
            schema_terms(value, found)
    elif isinstance(node, list):
        for value in node:
            schema_terms(value, found)
    return found


def reduce_tool(tool):
    """Keep what the checks need from a live tools/list entry or a saved snapshot entry."""
    meta = tool.get("_meta") or {}
    schemes = tool.get("securitySchemes") or meta.get("securitySchemes") or []
    if "visibility" in tool:
        visibility = tool["visibility"]
    else:
        visibility = (meta.get("ui") or {}).get("visibility")
    schemas = {key: tool[key] for key in ("inputSchema", "outputSchema") if key in tool}
    # The fee split is read from the schemas whenever the tool has them; a stored split is used only without schemas.
    splits = fee_splits(list(schemas.values()), []) if schemas else tool.get("feeSplits", [])
    return {
        "name": tool["name"],
        "description": tool.get("description") or "",
        "annotations": tool.get("annotations") or {},
        "securitySchemes": schemes,
        "visibility": visibility,
        "feeSplits": splits,
        **schemas,
    }


def model_visible(tool):
    vis = tool.get("visibility")
    return vis is None or "model" in vis


def needs_sign_in(tool):
    schemes = tool.get("securitySchemes") or []
    return bool(schemes) and not any(s.get("type") == "noauth" for s in schemes)


def read_only(tool):
    ann = tool.get("annotations") or {}
    return ann.get("readOnlyHint") is True and ann.get("destructiveHint") is not True


def url_problem(url):
    """Why a URL may not appear in the plugin, or None: https only, no user info, a PluginPad host exactly."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "is not a valid URL"
    if parts.scheme != "https":
        return "is not https"
    if "@" in parts.netloc:
        return "carries user info"
    if parts.netloc not in ALLOWED_HOSTS:
        return "names the host %s" % parts.netloc
    return None


def refuse_repeats(pairs):
    """A key written twice is read as its last value by most parsers, so the first value would hide from every check."""
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise ValueError("the key %r appears twice" % key)
        seen.add(key)
    return dict(pairs)


def load_json(path, errors):
    rel = path.name if path.parent.name != ".claude-plugin" else ".claude-plugin/" + path.name
    if not path.is_file():
        errors.append("%s is missing (%s)" % (rel, path))
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=refuse_repeats)
    except (ValueError, UnicodeDecodeError) as exc:
        errors.append("%s does not parse: %s (%s)" % (rel, exc, path))
        return None


def frontmatter(text):
    """Return the key/value pairs of a simple YAML frontmatter block, or None.

    A key with no value followed by "- item" lines holds the list of items. Any other
    indented line sets its key to None, so callers fail closed instead of skipping it.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    out, key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        if not line.strip() or line.startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            item = line.strip()
            if isinstance(out.get(key), list) and item.startswith("- "):
                out[key].append(item[2:].strip())
            else:
                out[key or "(indented line)"] = None
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        out[key] = False if key in out else (value.strip() or []) if sep else None
    return None


def check_frontmatter(path, rel, errors, require=("description",)):
    text = path.read_text(encoding="utf-8")
    fm = frontmatter(text)
    if fm is None:
        errors.append("%s has no closed frontmatter block" % rel)
        return {}
    for key in require:
        if not fm.get(key):
            errors.append("%s frontmatter has no %s" % (rel, key))
    for key, value in fm.items():
        if key not in FRONTMATTER_KEYS:
            errors.append("%s frontmatter key %r is not a plain known key (%s); the check fails closed" % (rel, key, ", ".join(sorted(FRONTMATTER_KEYS))))
            continue
        if value is False:
            errors.append("%s frontmatter key %r appears twice; the check fails closed" % (rel, key))
            continue
        if value is None:
            errors.append("%s frontmatter key %r is not one line or a - list; the check fails closed" % (rel, key))
            continue
        for item in value if isinstance(value, list) else [value]:
            quoted = len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'"
            if not quoted and (YAML_SPECIAL & set(item) or ": " in item):
                errors.append("%s frontmatter value of %s needs quotes or plain words" % (rel, key))
    return fm


def plugin_dirs(root, errors):
    """Yield (entry, plugin_dir) for every marketplace entry with a local source."""
    market = load_json(root / ".claude-plugin" / "marketplace.json", errors)
    if market is None:
        return []
    if not KEBAB.match(str(market.get("name", ""))):
        errors.append("marketplace name %r is not kebab-case" % market.get("name"))
    if not (market.get("owner") or {}).get("name"):
        errors.append("marketplace.json has no owner.name")
    if not market.get("description"):
        errors.append("marketplace.json has no description")
    entries = market.get("plugins") or []
    if not entries:
        errors.append("marketplace.json lists no plugins")
    found = []
    for entry in entries:
        name = entry.get("name", "")
        for key in sorted(set(entry) - ENTRY_KEYS):
            errors.append("marketplace entry %s declares %s; only metadata and a source may be listed (no hooks, servers, agents or settings)" % (name, key))
        if not KEBAB.match(str(name)):
            errors.append("marketplace entry name %r is not kebab-case" % name)
        source = entry.get("source")
        if not isinstance(source, str):
            errors.append("marketplace entry %s uses a remote source; this check reads only local plugin folders, so it refuses it" % name)
            continue
        if not source.startswith("./") or ".." in source:
            errors.append("marketplace entry %s source %r must start with ./ and stay inside the repo" % (name, source))
            continue
        pdir = (root / source).resolve()
        if not (pdir / ".claude-plugin" / "plugin.json").is_file():
            errors.append("marketplace entry %s points at %s, which has no .claude-plugin/plugin.json" % (name, source))
            continue
        found.append((entry, pdir))
    return found


def check_manifest(entry, pdir, errors):
    manifest = load_json(pdir / ".claude-plugin" / "plugin.json", errors)
    if manifest is None:
        return None
    name = manifest.get("name", "")
    if not KEBAB.match(str(name)):
        errors.append("plugin.json name %r is not kebab-case" % name)
    if name != entry.get("name"):
        errors.append("marketplace entry name %r does not match plugin.json name %r" % (entry.get("name"), name))
    if not manifest.get("version"):
        errors.append("plugin.json has no version")
    desc = manifest.get("description") or ""
    if not 10 <= len(desc) <= 2000 or desc != desc.strip():
        errors.append("plugin.json description must be 10 to 2000 characters with no outer spaces")
    if not (manifest.get("author") or {}).get("name"):
        errors.append("plugin.json has no author.name")
    if not manifest.get("license") and not (pdir / "LICENSE").is_file():
        errors.append("plugin has no license field in plugin.json and no LICENSE file")
    for key in sorted(set(manifest) - MANIFEST_KEYS):
        errors.append("plugin.json declares %s; only metadata may ship (no hooks, agents, servers or settings)" % key)
    return manifest


def check_surface(pdir, errors):
    for path in sorted(p for p in pdir.rglob("*") if (p.is_file() or p.is_symlink()) and p.name != ".DS_Store"):
        rel = path.relative_to(pdir).as_posix()
        if path.is_symlink():
            errors.append("plugin folder holds the symlink %s; a link can bring hooks, agents or scripts in from outside the folder" % rel)
            continue
        if not SHIPPED.fullmatch(rel):
            errors.append("plugin folder ships %s; only the manifest, .mcp.json, README, LICENSE, skills and commands may (no hooks, agents or scripts)" % rel)


def check_servers(pdir, errors):
    data = load_json(pdir / ".mcp.json", errors)
    if data is None:
        return {}
    servers = data.get("mcpServers", data) if isinstance(data, dict) else {}
    beside = sorted(set(data) - {"mcpServers", "$schema"}) if isinstance(data, dict) and "mcpServers" in data else []
    if beside:
        errors.append(".mcp.json holds %s beside mcpServers; only the servers may be listed" % ", ".join(beside))
    if not isinstance(servers, dict) or not servers:
        errors.append(".mcp.json declares no MCP server")
        return {}
    if len(servers) != 1:
        errors.append(".mcp.json declares %d servers; this check supports one per plugin" % len(servers))
    for key, cfg in servers.items():
        for extra in sorted(set(cfg) - SERVER_KEYS):
            errors.append("MCP server %s declares %s; only type and url may be set (no commands, headers or helpers)" % (key, extra))
        if cfg.get("type") != REMOTE_TYPE:
            errors.append("MCP server %s must be type http: this check reads the tools over Streamable HTTP, and Claude Code would read another type as another transport or a local command" % key)
        url = str(cfg.get("url", ""))
        problem = url_problem(url)
        if problem:
            errors.append("MCP server %s url %r %s; it must be an https URL on a PluginPad host" % (key, url, problem))
    return servers


def tool_refs(pdir, errors):
    """Return {(relative file, token): pre_approved} for every tool token in skills and commands."""
    refs = {}
    files = sorted(pdir.glob("skills/*/SKILL.md")) + sorted(pdir.glob("commands/*.md"))
    for path in files:
        rel = str(path.relative_to(pdir))
        fm = check_frontmatter(path, rel, errors)
        raw = fm.get("allowed-tools") or ""
        entries = [e for e in re.split(r"[,\s]+", " ".join(raw) if isinstance(raw, list) else raw) if e]
        for entry in entries:
            if not TOOL_TOKEN.fullmatch(entry):
                errors.append("%s pre-approves %s, which is not one of this plugin's MCP tools" % (rel, entry))
        allowed = {e for e in entries if TOOL_TOKEN.fullmatch(e)}
        text = path.read_text(encoding="utf-8")
        if SHELL_BANG.search(text):
            errors.append("%s runs a shell command through the ! backtick or ```! block syntax; the plugin promises no local scripts" % rel)
        for token in TOOL_TOKEN.findall(text):
            key = (rel, token)
            refs[key] = refs.get(key, False) or token in allowed
    return refs


def check_tools(manifest, servers, refs, tools, errors):
    reduced = [reduce_tool(t) for t in tools]
    index = {}
    for tool in reduced:
        norm = normalize(tool["name"])
        if norm in index:
            errors.append("server tools %s and %s share the Claude Code name %s" % (index[norm]["name"], tool["name"], norm))
        index[norm] = tool
    for tool in reduced:
        if model_visible(tool) and tool["annotations"].get("destructiveHint") is True:
            errors.append("server tool %s is destructive and visible to the model in Claude Code; the plugin promises Claude never launches" % tool["name"])
    prefixes = ["mcp__plugin_%s_%s__" % (manifest.get("name"), key) for key in servers]
    used = []
    for (rel, token), pre_approved in sorted(refs.items()):
        prefix = next((p for p in prefixes if token.startswith(p)), None)
        if prefix is None:
            errors.append("%s names %s, which does not use this plugin's tool prefix %s" % (rel, token, " or ".join(prefixes)))
            continue
        tool = index.get(token[len(prefix):])
        if tool is None:
            errors.append("%s names %s, which is not on the server" % (rel, token))
            continue
        used.append(tool)
        if not model_visible(tool):
            errors.append("%s names %s (%s), which is hidden from the model in Claude Code (app-only visibility)" % (rel, token, tool["name"]))
        if needs_sign_in(tool):
            errors.append("%s names %s (%s), which needs sign-in that Claude Code cannot start for this server" % (rel, token, tool["name"]))
        if pre_approved and not read_only(tool):
            errors.append("%s pre-approves %s (%s) in allowed-tools, but it is not read-only" % (rel, token, tool["name"]))
        if pre_approved and tool["name"] in OVERSIZED_TOOLS:
            errors.append("%s pre-approves %s (%s), whose answer is too large for Claude Code (%d characters, saved to a file)" % (rel, token, tool["name"], OVERSIZED_TOOLS[tool["name"]]))
    return used


def words(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'.,/-]*", text))


def copy_files(root, pdirs):
    files = [root / "README.md", root / ".claude-plugin" / "marketplace.json"]
    for pdir in pdirs:
        files += sorted(p for p in pdir.rglob("*") if p.is_file() and p.suffix in (".md", ".json"))
    return [f for f in files if f.is_file()]


def strings_of(node):
    """Every string in a parsed JSON document: key names and values, at any depth."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from strings_of(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings_of(value)


def copy_text(path):
    """The text the copy rules read: a Markdown file as written, a JSON file as its decoded strings, so that
    a JSON escape cannot hide a link, a dash or a word. A JSON file that does not parse, or repeats a key, is read
    as written, so nothing in it hides either."""
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".json":
        return text
    try:
        return "\n".join(strings_of(json.loads(text, object_pairs_hook=refuse_repeats)))
    except ValueError:
        return text


def party_of(word):
    return "platform" if word.lower() in ("pluginpad", "platform", "platformbps") else "creator"


def fee_direction_problems(prose, pair):
    """Problems with the way the prose states the split [platformBps, creatorBps]: each percentage must be followed
    by its own party (or be introduced by its own field name), never by the other party, and a field name must be
    followed by its own value."""
    problems = []
    owner = {pair[0] // 100: "platform", pair[1] // 100: "creator"}
    value = {"platformBps": pair[0], "creatorBps": pair[1]}
    for m in BPS_VALUE.finditer(prose):
        if m.group(2) is None or int(m.group(2)) != value[m.group(1)]:
            problems.append("%s must be followed by its own value %d" % (m.group(1), value[m.group(1)]))
    matches = list(PERCENT.finditer(prose))
    for i, m in enumerate(matches):
        number = int(m.group(1))
        end = SENTENCE_END.search(prose, m.end())
        stop = end.start() + 1 if end else len(prose)
        after = prose[m.end():min(stop, matches[i + 1].start()) if i + 1 < len(matches) else stop]
        starts = [e.end() for e in SENTENCE_END.finditer(prose, 0, m.start())]
        floor = max([0] + starts + ([matches[i - 1].end()] if i else []))
        before = prose[floor:m.start()]
        after_parties = {party_of(w) for w in PARTY.findall(CREATOR_FEES.sub(" ", after))}
        before_parties = {party_of(w) for w in BPS_NAME.findall(before)}
        if number not in owner:
            continue
        parties = after_parties or before_parties
        if parties != {owner[number]}:
            problems.append("%d percent is not stated with %s, the party that gets it" % (number, owner[number]))
    return problems


def check_copy(root, pdirs, errors, tools, used):
    stated = set()
    pairs = [pair for t in used for pair in t["feeSplits"]]
    direction = {tuple(pair): [] for pair in pairs}
    terms = schema_terms([[t.get("inputSchema"), t.get("outputSchema")] for t in used], set())
    for path in copy_files(root, pdirs):
        rel = str(path.relative_to(root))
        text = copy_text(path)
        if any(d in text for d in DASHES):
            errors.append("%s has an em or en dash" % rel)
        other = OTHER_LINK.search(text)
        if other:
            errors.append("%s holds the link form %r; public copy may only link https URLs on PluginPad hosts" % (rel, other.group(0)))
        for url in sorted({u.rstrip(".,;:!?") for u in LINK.findall(text)}):
            problem = url_problem(url)
            if problem:
                errors.append("%s links %s, which %s; public copy may only link https URLs on PluginPad hosts" % (rel, url, problem))
        prose = TOOL_TOKEN.sub(" ", text)
        hit = OLD_NAMES.search(prose)
        if hit:
            errors.append("%s uses the brand word %r outside a tool id" % (rel, hit.group(0)))
        if OLD_SPLIT.search(prose):
            errors.append("%s states the old fee split; PluginPad is 30 percent platform, 70 percent creator" % rel)
        for limit in LIMIT.findall(prose):
            if not any(limit.lower() in t["description"].lower() for t in tools):
                errors.append("%s states the launch limit %r, which no server tool states" % (rel, limit))
        for size in SIZE.findall(prose):
            if not any(size.lower() in t["description"].lower() for t in tools):
                errors.append("%s states the size %r, which no server tool states" % (rel, size))
        for fmt in IMAGE_FORMAT.findall(prose):
            if "image/" + IMAGE_TYPES.get(fmt.lower(), fmt.lower()) not in terms:
                errors.append("%s names the image format %s, which no tool the plugin uses accepts" % (rel, fmt))
        if {"skills", "commands"} & set(path.relative_to(root).parts):
            for name in NAME.findall(text):
                if name not in terms and not name.startswith("mcp__"):
                    errors.append("%s names `%s`, which no tool the plugin uses declares as a field or value" % (rel, name))
        stated |= {int(n) for n in PERCENT.findall(prose)}
        for pair, found in direction.items():
            found += ["%s: %s" % (rel, p) for p in fee_direction_problems(prose, pair)]
    if stated and not any(stated <= {a // 100, b // 100} for a, b in pairs):
        errors.append("the copy states a fee split of %s percent, which no tool the plugin uses states" % sorted(stated))
    if direction and all(direction.values()):
        for problem in next(iter(direction.values())):
            errors.append("fee copy: " + problem)
    for pdir in pdirs:
        readme = pdir / "README.md"
        if not readme.is_file():
            errors.append("%s has no README.md" % pdir.name)
        else:
            count = words(readme.read_text(encoding="utf-8"))
            if count < MIN_README_WORDS:
                errors.append("%s/README.md has %d words; the directory needs at least 40 words" % (pdir.name, count))
        for rel in FACT_FILES:
            path = pdir / rel
            if not path.is_file():
                errors.append("%s/%s is missing" % (pdir.name, rel))
                continue
            text = path.read_text(encoding="utf-8")
            for fact in REQUIRED_FACTS:
                if fact not in text:
                    errors.append("%s/%s does not state %r" % (pdir.name, rel, fact))


def check_repo(root, tools):
    root = pathlib.Path(root).resolve()
    errors, pdirs, used = [], [], []
    for entry, pdir in plugin_dirs(root, errors):
        pdirs.append(pdir)
        manifest = check_manifest(entry, pdir, errors)
        check_surface(pdir, errors)
        servers = check_servers(pdir, errors)
        refs = tool_refs(pdir, errors)
        if manifest is not None and servers:
            used += check_tools(manifest, servers, refs, tools, errors)
    check_copy(root, pdirs, errors, [reduce_tool(t) for t in tools], used)
    return errors


def parse_rpc_body(body, content_type):
    if "text/event-stream" in (content_type or ""):
        for block in body.replace("\r\n", "\n").split("\n\n"):
            data = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
            if not data:
                continue
            msg = json.loads("\n".join(data))
            if "result" in msg or "error" in msg:
                return msg
        raise ValueError("no JSON-RPC response in the event stream")
    return json.loads(body)


def post(url, payload, session=None, timeout=30):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "User-Agent": "pluginpad-claude-check/0.1",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return body, resp.headers.get("Content-Type", ""), resp.headers.get("Mcp-Session-Id") or session


def fetch_tools(url, timeout=30):
    """initialize, notifications/initialized, then every page of tools/list. Reads only."""
    body, ctype, session = post(url, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                   "clientInfo": {"name": "pluginpad-claude-check", "version": "0.1.0"}},
    }, timeout=timeout)
    init = parse_rpc_body(body, ctype)
    if "error" in init:
        raise RuntimeError("initialize failed: %s" % init["error"])
    try:
        post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session, timeout)
    except urllib.error.HTTPError:
        pass
    tools, cursor, rid = [], None, 2
    while True:
        params = {"cursor": cursor} if cursor else {}
        body, ctype, session = post(url, {"jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": params}, session, timeout)
        msg = parse_rpc_body(body, ctype)
        if "error" in msg:
            raise RuntimeError("tools/list failed: %s" % msg["error"])
        tools.extend(msg["result"].get("tools", []))
        cursor = msg["result"].get("nextCursor")
        rid += 1
        if not cursor:
            return tools


def server_url(root, errors):
    """The plugin's MCP URL, or None when the config has any problem, so nothing is fetched."""
    urls = []
    for _entry, pdir in plugin_dirs(pathlib.Path(root).resolve(), errors):
        urls += [cfg.get("url") for cfg in check_servers(pdir, errors).values()]
    if not urls and not errors:
        errors.append("no MCP server URL found")
    return None if errors else urls[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[1]))
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--live", action="store_true", help="read the tool list from the server in .mcp.json")
    src.add_argument("--tools-file", help="read the tool list from a saved snapshot")
    ap.add_argument("--snapshot-out", help="write the reduced tool list to this file")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root)
    try:
        if args.live:
            config_errors = []
            source = server_url(root, config_errors)
            if source is None:
                for err in config_errors:
                    print("ERROR %s" % err)
                print("CHECK FAIL %d" % len(config_errors))
                return 1
            tools = fetch_tools(source)
        else:
            source = args.tools_file
            data = json.loads(pathlib.Path(source).read_text(encoding="utf-8"))
            tools = data["tools"] if isinstance(data, dict) else data
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print("ERROR could not read the tool list: %s" % exc)
        print("CHECK UNPROVED")
        return 2
    reduced = [reduce_tool(t) for t in tools]
    visible = [t for t in reduced if model_visible(t)]
    print("tool source: %s" % source)
    print("tools on server: %d; visible to the model in Claude Code: %d" % (len(reduced), len(visible)))
    if args.snapshot_out:
        doc = {"source": source, "count": len(reduced), "tools": reduced}
        pathlib.Path(args.snapshot_out).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print("snapshot written: %s" % args.snapshot_out)
    errors = check_repo(root, tools)
    for err in errors:
        print("ERROR %s" % err)
    print("CHECK PASS" if not errors else "CHECK FAIL %d" % len(errors))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
