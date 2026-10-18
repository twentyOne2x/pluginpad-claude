# PluginPad for Claude Code

PluginPad drafts a plugin and its Solana token together. With this plugin, Claude drafts both with you and then hands you a PluginPad link. You continue on the PluginPad website, where you sign in and press Launch. Claude never signs, pays or launches.

## Install

In Claude Code:

```
/plugin marketplace add twentyOne2x/pluginpad-claude
/plugin install pluginpad@pluginpad
```

Or from your shell:

```
claude plugin marketplace add twentyOne2x/pluginpad-claude
claude plugin install pluginpad@pluginpad
```

To try it for one session without installing, start Claude Code with `claude --plugin-dir ./plugins/pluginpad` from a copy of this repository.

## Use it

- `/pluginpad:launch` with a token name, ticker or idea: Claude drafts the token and gives you your PluginPad link.
- `/pluginpad:create` with what the plugin should do: Claude starts from the plugin idea, then the token.
- `/pluginpad:status`: checks that the PluginPad server answers.

You can also just ask, for example: launch a token called Sunny with ticker SUN on PluginPad.

## Fees

- The draft records the creator fee split: 30 percent of the token's creator fees for PluginPad, 70 percent for the creator.
- Check the terms on the PluginPad website before you press Launch.

## What the plugin sends and where

- It connects to one server, the PluginPad MCP server at https://api.pluginpad.app/mcp, over HTTPS.
- It sends only the details you give for the draft: plugin name and brief, token name, ticker, description, social links, an image link if you give one, platform and network. You add the token image on the PluginPad website.
- It gets back the draft and a PluginPad link. A draft creates no project, no on chain operation and no payment.
- It has no hooks, no local scripts and no telemetry, and it does not sign in to anything from Claude Code.

## Safety

- Claude never launches. You launch on the PluginPad website, where you sign in and press Launch.
- Never share a seed phrase or a private key.
- New tokens are risky and can lose all value. Claude gives no investment advice.

## Links

- PluginPad website: https://pluginpad.app
- PluginPad is not made by Anthropic.
