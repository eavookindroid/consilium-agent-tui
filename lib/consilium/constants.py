"""
Consilium Agent - Constants and Text Strings

All hardcoded constants, user-visible strings, and system prompts.
Extracted for easy maintenance and localization.

Copyright (c) 2025 Artel Team
Licensed under Artel Team Non-Commercial License
"""

import re

# ============================================================================
# CONFIGURATION CONSTANTS
# ============================================================================

STREAM_READER_LIMIT = 20 * 1024 * 1024  # 20 MiB to accommodate large JSON chunks
SHUTDOWN_GRACE_PERIOD = 0.1  # seconds to wait between terminate() and kill()
HISTORY_TAIL_LINES = 2000  # Match ChatLog max_lines limit
SILENT_RESPONSE_PATTERN = re.compile(r"^[\.\u2024\u2025\u2026\u2027\u22ef\u205d]+$")

# System prompt refresh period (experimental)
# How often to include IDENTITY + PROTOCOL + PARTICIPANTS + ROLE in messages
# 0 = once (only first), 1 = always, N = every Nth message
# Fibonacci sequence for testing: 2, 3, 5, 8, 13, 21, 34, 55, 89
SYSTEM_PROMPT_PERIOD = 13  # TODO: make configurable via UI

# ============================================================================
# HARDCODED TEXT CONSTANTS
# ============================================================================
# All user-visible strings and prompt fragments extracted from code
# for easy maintenance and localization

# Identity and context templates
IDENTITY_TEMPLATE = "Your name is {display_name}."
PARTICIPANTS_TEMPLATE = "You are in a group chat with {participants}."

# Status messages - Processing
STATUS_READY = "Ready..."
STATUS_PROCESSING = "{display_name}: processing the request..."
STATUS_ERROR_PROCESSING = "⚠️ {display_name}: error processing message ({handler_error})"
STATUS_EMPTY_RESPONSE = "{display_name} returned an empty response"
STATUS_STAYED_SILENT = "{display_name} stayed silent"
STATUS_CONTEXT_OVERFLOW = "{display_name} context overflow, compacting..."
STATUS_SESSION_RESET = "⚠️ {display_name} session reset due to error, retry message"
STATUS_CODEX_COMPACT_FAILED = "⚠️ Codex failed to compact context, session reset"
STATUS_CODEX_COMPACT_SUCCESS = "Codex context compacted, retrying message"
STATUS_AGENT_PARTICIPATION = "{display_name} {status} for chat participation"
STATUS_MEMBER_ADDED = "Added member: {display_name}"
STATUS_MEMBER_REMOVED = "Removed member: {display_name}"

# Status messages - UI/UX
STATUS_STARTING_AGENTS = "Starting agents..."
STATUS_FIRST_LAUNCH = "First launch detected. Configure the workspace, then press Ctrl+G to begin."
STATUS_PRESS_CTRL_G = "Press Ctrl+G to begin before sending messages."
STATUS_CREATING_PROMPTS_TOML = "Creating ~/.consilium/prompts.toml..."
STATUS_PROMPT_SAVED = "✅ Prompt saved."
STATUS_EDITING_CANCELLED = "Editing canceled"
STATUS_UNKNOWN_AGENT = "Unknown agent: {agent_name}"
STATUS_PROMPT_SELECTION_FAILED = "Failed to determine the selected prompt."
STATUS_ROLE_CREATED = "Created a new role: {role_name}"
STATUS_ROLE_SAVED = "✅ Role saved."
STATUS_ROLE_NAME_REQUIRED = "Role name cannot be empty."
STATUS_ROLE_NAME_EXISTS = "A role with this name already exists."

# Status messages - Russian (editor/prompts)
STATUS_EDIT_CANCELLED_RU = "{display_name}: edit cancelled."
STATUS_PROMPT_EMPTY_RU = "Prompt cannot be empty."
STATUS_PROMPT_SAVED_RU = "{display_name}: prompt saved."

# Error messages
ERROR_EDITOR_INIT = "Failed to initialize editor for {agent_name}: {exc}"
ERROR_PROMPT_SAVE = "Failed to save prompt: {exc}"
ERROR_CRITICAL = "Critical application error: {error}"
ERROR_EDITOR_OPEN = "Failed to open prompt editor for {agent_name}: {exc}"
ERROR_PROMPT_SELECT = "Failed to select prompt: {exc}"
ERROR_PROMPT_MENU = "Failed to open prompt menu: {exc}"
ERROR_ROLE_MENU = "Failed to open roles panel: {exc}"
ERROR_ROLE_EDITOR = "Failed to open role editor: {exc}"
ERROR_ROLE_CREATE = "Failed to create role: {exc}"
ERROR_MEMBER_REMOVE = "Failed to remove member {display_name}: {exc}"
ERROR_MEMBER_NOT_FOUND = "Member not found: {display_name}"
ERROR_MEMBER_UNKNOWN = "Unknown member: {agent_id}"

# Text messages (styled)
TEXT_INTERRUPTED = "Interrupted by user"
TEXT_PROMPT_SAVED = "✅ Prompt saved."

# System messages
MSG_STEP_MODE_ON = "⏸  Step-by-step mode ENABLED (Ctrl+N reveals each next response)"
MSG_STEP_MODE_OFF = "▶  Step-by-step mode DISABLED"
MSG_STEP_MODE_AUTO_CONTINUE = "▶  Step-by-step mode disabled, continuing automatically..."
MSG_STEP_MODE_DISABLED = "Step-by-step mode is disabled!"
MSG_NO_PENDING_STEPS = "⏸  No pending steps"

# ============================================================================
# SYSTEM PROMPTS
# ============================================================================

# DEFAULT_INIT_PROMPT - for first introduction only
# NOTE: First line about chat participants will be added dynamically
DEFAULT_INIT_PROMPT = """

FIRST INTRODUCTION:
1. Introduce yourself - what is your name, role, and skills?
2. WAIT for a task from the participants before acting
3. DO NOT start working on your own
4. DO NOT suggest starting anything until someone asks
5. DO NOT demand work from teammates unless someone proposes it"""

# DEFAULT_SYSTEM_PROMPT - for all subsequent messages
# NOTE: First line about chat participants will be added dynamically
DEFAULT_SYSTEM_PROMPT = """
You are in a workspace chat with teammates.
IMPORTANT: you share a workspace in the current directory.

RULES:
1. ACTIVELY discuss technical topics, build on teammates' ideas, offer solutions.
2. Stay engaged, friendly, and critical — answer questions, contribute ideas, review solutions.
3. Evaluate every answer objectively.
4. DO NOT repeat what a teammate already said — add your own perspective.
5. If the user asks you to stay silent, do so immediately without explanation.
6. Avoid looping conversations — if you have nothing to add, stay silent.
7. To stay silent: either respond with an empty message or exactly five dots: .....

"""

# Protocol prompt for agents with chat metadata support
REPLY_CHAT_PROTOCOL_PROMPT = """Reply in two blocks.

Block 1 — JSON header with metadata:
{
  "replyto": <msg_id or null>,
  "to": ["nickname1", "nickname2", ...]
}

<Your message here out of the any JSON>

- `replyto` — id of the message you are replying to (take it from headers like `HEADER:{"#msg_id#": {msd_id}}`; use `null` if you start a new thread)
- `to` — list of recipients. Use their nicknames (e.g., "John", "Ellis.Smith"). To address everyone, return ["all"]

Block 2 — after a single blank line, write your actual message in Markdown. Do not wrap the message in JSON or add extra keys.
"""

# Template for message headers shown to agents
CHAT_HEADER_TEMPLATE = 'HEADER:{{"#msg_id#": {msd_id},}}'
