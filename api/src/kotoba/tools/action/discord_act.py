"""Change the Discord server: channels, categories, roles, members — a batch at a time, one card."""
from __future__ import annotations

from kotoba.discord.actions import MAX_ACTIONS, OPS

SCHEMA = {
    "type": "function",
    "name": "discord_act",
    "description": (
        "Change the Discord server: create, rename, move or delete channels and categories, create "
        "and hand out roles, move people between voice channels, rename, time out, kick or ban. "
        "Send every change of one request TOGETHER in one call — one call is one approval card for "
        "the person, not fifteen. Read the server with `discord_guild_read` first: a change written "
        "from memory moves channels that are not there. Every op is declarative: say the state you "
        "want, never 'do it again'. You cannot hand out administrator, and you cannot touch roles "
        "or people above you in the role list.\n"
        "To make a channel only one group can see: create the role, create the channel, then "
        "`make_private` on it with `roles` listing who gets in — that denies @everyone and lets "
        "those in, in one step. `set_permissions` is the precise version when they want particular "
        "permissions rather than plain access.\n"
        "You can build a FORUM the same way: create_channel with value 'forum', a `topic` for the "
        "posting guidelines, `tags` for what people label their posts with, `emoji` for the default "
        "reaction, and `first_post` so it is not born empty — that is Discord's own setup checklist, "
        "and a forum that finishes it is one people actually use. On a forum that ALREADY exists, "
        "`set_forum` changes its tags, guidelines and default reaction, and `forum_post` opens a "
        "post in it — creating never overwrites something already there, so those two are how you "
        "finish one. Order matters inside one batch: create the role or the channel BEFORE the "
        "action that uses it, and it works."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "description": f"Up to {MAX_ACTIONS} changes, applied in the order you give them.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": sorted(OPS)},
                        "target": {"type": "string", "description":
                                   "What it acts on: '#channel', a category name, '@role', or a "
                                   "person. For create_* it is the NEW name. For post_message, "
                                   "leave it out to post in the channel you are in."},
                        "to": {"type": "string", "description":
                               "Where it goes or what it becomes: the new name, the destination "
                               "category or voice channel, the role to give or take."},
                        "user": {"type": "string", "description":
                                 "The person, when the op is about one."},
                        "value": {"type": "string", "description":
                                  "For post_message, the text to post. For create_channel, the KIND: 'text', "
                                  "'voice', 'forum', 'media', "
                                  "'stage' or 'announcement'. Otherwise: slowmode seconds, timeout "
                                  "minutes, a colour like '#7c4dff', or message text."},
                        "topic": {"type": "string", "description":
                                  "For create_channel: the topic, or a forum's posting guidelines."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description":
                                 "For a forum: the tags people pick from when they post. Put the "
                                 "emoji first and it becomes the tag's emoji, e.g. "
                                 "['❓ Duda', '📚 Recurso', '💬 Debate']. Up to 20."},
                        "emoji": {"type": "string", "description":
                                  "For a forum: the default reaction on every post, e.g. '🙏'."},
                        "first_post": {"type": "string", "description":
                                       "For a forum: the title of the opening post, so the forum "
                                       "is not born empty."},
                        "first_body": {"type": "string", "description":
                                       "The text of that opening post."},
                        "roles": {"type": "array", "items": {"type": "string"}, "description":
                                  "For make_private: everyone who SHOULD see it. '@everyone' is "
                                  "denied automatically, so list only the roles or people allowed "
                                  "in."},
                        "allow": {"type": "array", "items": {"type": "string"}, "description":
                                  "For set_permissions: Discord permission names to switch ON for "
                                  "`to` on this channel, e.g. ['view_channel','send_messages']."},
                        "deny": {"type": "array", "items": {"type": "string"}, "description":
                                 "For set_permissions: permission names to switch OFF."},
                        "hoist": {"type": "boolean", "description":
                                  "For create_role and set_role_flags: show its members in their "
                                  "own group in the member list."},
                        "mentionable": {"type": "boolean", "description":
                                        "For create_role and set_role_flags: let anyone ping the "
                                        "role."},
                        "reason": {"type": "string", "description":
                                   "Shown in the server's own audit log. One line, say why."},
                    },
                    "required": ["op"],
                    "additionalProperties": False,
                },
            },
            "why": {"type": "string", "description":
                    "One line for the approval card: what this batch is for, in their own terms."},
        },
        "required": ["actions", "why"],
        "additionalProperties": False,
    },
}

BUILT_IN = False
TOOLSET = "discord"
RISK = "write"

# A carded tool gets no approval headroom for free — only RISK="exec" does. At the default 30s the
# loop would cancel this one long before its own card expires, leaving the card orphaned on screen.
TIMEOUT = 240

ANNOUNCE = "Let me set that up."
HEARTBEAT = ["Working through the changes...", "Almost there..."]
COMPLETE = "Done. Here's what changed:"
FAIL = "I couldn't make those changes."

EXPRESSIONS = {"focus": "determined", "fail": "embarrassed"}


def check() -> bool:
    from kotoba.discord import state

    return state.runtime_live()


async def execute(args: dict, ctx) -> str:
    from kotoba.core import interaction
    from kotoba.core import text_security
    from kotoba.discord import actions as act_mod
    from kotoba.discord import authority, state

    who = state.actor()
    if who is None or not who.is_guild_admin:
        return authority.refusal(who, "change this server")

    client = state.client()
    gid = state.guild_id()
    guild = client.get_guild(gid) if client and gid else None
    if guild is None:
        return "I can only change a server from inside one."

    actions = [a for a in (args.get("actions") or []) if isinstance(a, dict)]
    if not actions:
        return "There was nothing in that to change."
    if len(actions) > MAX_ACTIONS:
        return (f"That is {len(actions)} changes and I take {MAX_ACTIONS} at a time. Send the first "
                f"{MAX_ACTIONS} and I'll take the rest after.")

    # Every other Discord tool means "here" when no channel is named; this one refused with "there is
    # no channel called ''". Asked to greet somebody in the room she was standing in, she could not.
    here = getattr(client.get_channel(state.channel_id() or 0), "name", "")
    for act in actions:
        if act.get("op") == "post_message" and not str(act.get("target") or "").strip() and here:
            act["target"] = here

    stopped = act_mod.refusals(guild, actions)
    if stopped:
        return ("I can't do some of that, so I did none of it:\n" + "\n".join(stopped)
                + "\nTell me how you want to handle those and I'll run the rest.")

    parts = act_mod.summarise(actions)
    facts = [["What for", text_security.scrub(str(args.get("why") or "")) or "not said"]]
    for label, items in (("Creates", parts["creates"]), ("Changes", parts["changes"]),
                         ("DELETES", parts["deletes"])):
        if items:
            facts.append([label, text_security.scrub(" · ".join(items))[:1000]])

    verdict = await interaction.ask_approval(
        ctx, f"Make {len(actions)} change(s) to “{guild.name}”?",
        family="",
        notice={"head": f"Change “{guild.name}”?", "facts": facts, "surface": "discord"})
    if verdict != interaction.APPROVED:
        return interaction.refusal_note(verdict, f"changing “{guild.name}”")

    done, problems = await act_mod.apply(guild, actions, reason=str(args.get("why") or ""))
    said = []
    if done:
        said.append("Done:\n" + "\n".join(f"- {d}" for d in done))
    if problems:
        said.append(f"Then I stopped: {problems[0]}")
        said.append(f"{len(actions) - len(done)} of the {len(actions)} changes did not run.")
    return "\n".join(said) or "Nothing ran."
