# PluginPad for Claude Code

This repository is the PluginPad marketplace for Claude Code. It holds one plugin, `pluginpad`, in `plugins/pluginpad`. With it, Claude drafts a plugin and its Solana token with you, then hands you a PluginPad link where you sign in and launch it on the PluginPad website. Claude never signs, pays or launches.

## Install

```
/plugin marketplace add twentyOne2x/pluginpad-claude
/plugin install pluginpad@pluginpad
```

Read [the plugin README](plugins/pluginpad/README.md) for the commands, the fees, and exactly what the plugin sends.

## Layout

- `.claude-plugin/marketplace.json`: the marketplace, named `pluginpad`.
- `plugins/pluginpad/.claude-plugin/plugin.json`: the plugin manifest.
- `plugins/pluginpad/.mcp.json`: the PluginPad MCP server, https://api.pluginpad.app/mcp.
- `plugins/pluginpad/skills/launch-token/SKILL.md`: how Claude drafts the plugin and token and hands over the link.
- `plugins/pluginpad/commands/`: `/pluginpad:launch`, `/pluginpad:create` and `/pluginpad:status`.
- `scripts/check_plugin.py` and `tests/`: checks for this repository, not part of the plugin.

## Checks

Run these before every release:

```
python3 -m unittest discover -s tests -v
python3 scripts/check_plugin.py --live
claude plugin validate --strict .
claude plugin validate --strict plugins/pluginpad
```

`check_plugin.py --live` reads the tool list from the PluginPad server and fails when a skill or command names a tool that is missing, hidden from the model in Claude Code, needs sign-in, or is pre-approved without being read-only or with an answer too large for Claude Code. It also fails if the server shows any destructive tool to the model, so "Claude never launches" stays checked.

It checks the copy against the tools. A fee split, launch limit, size or image format that the text states must come from the tool list. A field or value that a skill or command names in backticks must be one that a tool the plugin uses declares in its schema. Links must be https URLs on PluginPad hosts with no user info, and the text has no dashes.

It checks that the plugin runs nothing: no hooks, agents or scripts, no shell command in a skill or command (neither the `!` backtick form nor the fenced form), no server key in `.mcp.json` but `type` and `url` (and type `http`), no other top-level key beside `mcpServers`, and no marketplace entry field that is not metadata.

It exits 1 on any problem (a bad config stops it before any network call) and 2, never 0, when the server cannot be read.

`tests/fixtures/live-tools-20261018.json` holds the four tools the plugin names. Their descriptions, annotations and sign-in settings were read from the live server on 2026-10-18. Their input and output schemas were built offline from the server source of that release. To refresh the file, run `python3 scripts/check_plugin.py --live --snapshot-out FILE` and keep the four tools the plugin names.

`claude plugin validate` checks the manifests only. On Claude Code 2.1.302 it passed a skill whose frontmatter was broken, so the frontmatter check in `check_plugin.py` is the one to trust.
