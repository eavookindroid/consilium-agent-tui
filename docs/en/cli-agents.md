# Configuring CLI Agents

## Overview

Consilium Agent works with external CLI tools from AI providers to interact with language models. Each agent connects through its own CLI utility, which must be installed and configured in your system.

### Official Support

The application **officially supports** only two types of CLI utilities:
- ✅ **Claude CLI** (Anthropic) — `@anthropic-ai/claude-code`
- ✅ **Codex CLI** (OpenAI) — `@openai/openai-codex`

### Gemini CLI (special fork)

⚠️ **Important:** Official Gemini CLI from Google **is not supported**.

Instead, use the special fork created by the authors of Consilium Agent:
- 🔗 [gemini-artel-fork](https://github.com/eavookindroid/gemini-artel-fork)

This fork adds the necessary functionality for working with Consilium (session management).

### Other Providers and Models

You can **create wrapper scripts yourself** to use other LLM providers and models:
- Zhipu AI (GLM)
- Alibaba Qwen
- DeepSeek
- Sber GigaChat
- etc.

See the section [Connecting Alternative Providers](#connecting-alternative-providers) with configuration examples through Claude CLI and Codex CLI.

---

## Official CLI Utilities

### Claude CLI (Anthropic)

**Installation:**
```bash
npm install -g @anthropic-ai/claude-code
```

**Authentication:**
```bash
claude --login
```

**Testing:**
```bash
claude -p "Hello, test message"
```

**Configuration in Consilium:**
- `Agent type`: `Anthropic Claude CLI`
- `Command path`: `claude`

---

### Codex CLI (OpenAI)

**Installation:**
```bash
npm install -g @openai/openai-codex
```

**Authentication:**
```bash
codex auth
```

**Testing:**
```bash
codex -p "Hello, test message"
```

**Configuration in Consilium:**
- `Agent type`: `OpenAI Codex CLI`
- `Command path`: `codex`

---

### Gemini CLI (Artel fork)

⚠️ **Important:** Special fork with session support required!

Standard Gemini CLI from Google does not support:
- Session ID in non-interactive mode

**Installation:**
```bash
git clone https://github.com/eavookindroid/gemini-artel-fork.git
cd gemini-artel-fork
./install.sh
```

**Version check:**
```bash
gemini --version
# Should contain "-artel-" suffix
```

If there's no suffix — standard CLI is installed, which will not work with Consilium.

**Configuration in Consilium:**
- `Agent type`: `Gemini CLI`
- `Command path`: `gemini`

**Features:**
- JSON output with session ID
- Support for role="assistant" messages
- Processing of tool_use events

---

## Connecting Alternative Providers

Consilium supports connecting any LLM providers through wrappers for Claude CLI or Codex CLI.

>💡 You can use official CLI agents `claude` and `codex` for cheap or completely free usage by configuring wrapper scripts to access third-party LLM providers and models, if you know where to find them :)


### Via Claude CLI (Anthropic API standard)

Suitable for providers compatible with Anthropic API.

#### Example: Zhipu AI (GLM-4)

Ready-made wrapper script is located in `custom-agents-cli/bin/glm` with config in `custom-agents-cli/configs/.glm/settings.json`.

**1. Copy the script:**

```bash
cp custom-agents-cli/bin/glm ~/.local/bin/glm
chmod +x ~/.local/bin/glm
```

**2. Copy and configure config:**

```bash
mkdir -p ~/.glm
cp custom-agents-cli/configs/.glm/settings.json ~/.glm/settings.json
```

**3. Edit `~/.glm/settings.json`:**

```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "YOUR_API_KEY",
    "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-4.6",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-4.6"
  }
}
```

Replace `YOUR_API_KEY` with your API key from the provider.

**4. Configure agent in Consilium:**
- `Ctrl+S` → Members → Add Agent
- **Nickname**: `GLM`
- **Agent type**: `Anthropic Claude CLI`
- **Command path**: `glm`
- **Avatar**: `🇨🇳`

**How the script works:**
- Automatically searches for settings in `~/.glm/` with priorities
- Passes `--settings` to `claude` CLI
- Supports environment variables `GLM_SETTINGS_FILE`, `GLM_PROFILE`

---

### Via Codex CLI (OpenAI API standard)

Suitable for providers compatible with OpenAI API.

Ready-made wrapper scripts are located in `custom-agents-cli/bin/` with configs in `custom-agents-cli/configs/`.

#### Example 1: Alibaba Qwen

**1. Copy the script:**

```bash
cp custom-agents-cli/bin/qwen ~/.local/bin/qwen
chmod +x ~/.local/bin/qwen
```

**2. Copy and configure config:**

```bash
mkdir -p ~/.qwen
cp custom-agents-cli/configs/.qwen/config.toml ~/.qwen/config.toml
```

**3. Edit `~/.qwen/config.toml`:**

```toml
sandbox_mode = "danger-full-access"
approval_policy = "never"
model = "Qwen/Qwen3-Coder-480B-A35B-Instruct"
model_provider = "MYPROVIDER"
model_reasoning_effort = "high"

[sandbox_workspace_write]
network_access = true

[model_providers.MYPROVIDER]
name = "AnyTEXT"
base_url = "https://api-host.example.org/blah/blah/v1"
env_key = "API_KEY"
wire_api = "chat"
```

**4. Set API key:**

```bash
export API_KEY="YOUR_QWEN_API_KEY"
```

**5. Configure agent in Consilium:**
- `Ctrl+S` → Members → Add Agent
- **Nickname**: `Qwen`
- **Agent type**: `OpenAI Codex CLI`
- **Command path**: `qwen`
- **Avatar**: `🚀`

#### Example 2: DeepSeek

**1. Copy the script:**

```bash
cp custom-agents-cli/bin/deepseek ~/.local/bin/deepseek
chmod +x ~/.local/bin/deepseek
```

**2. Copy and configure config:**

```bash
mkdir -p ~/.deepseek
cp custom-agents-cli/configs/.deepseek/config.toml ~/.deepseek/config.toml
```

**3. Edit `~/.deepseek/config.toml`:**

```toml
sandbox_mode = "danger-full-access"
approval_policy = "never"
model = "PREFIX/MODEL-NAME"
model_provider = "MyProvider"
model_reasoning_effort = "high"

[sandbox_workspace_write]
network_access = true

[model_providers.MyProvider]
name = "MyDeepSeek"
base_url = "https://api.hostname.com/v1"
env_key = "API_KEY"
wire_api = "chat"
```

**4. Set API key:**

```bash
export API_KEY="YOUR_DEEPSEEK_API_KEY"
```

**5. Configure agent in Consilium:**
- **Nickname**: `DeepSeek`
- **Agent type**: `OpenAI Codex CLI`
- **Command path**: `deepseek`

#### Example 3: GigaChat (Sber)

**1. Copy the script:**

```bash
cp custom-agents-cli/bin/gigachat ~/.local/bin/gigachat
chmod +x ~/.local/bin/gigachat
```

**2. Copy and configure config:**

```bash
mkdir -p ~/.gigachat
cp custom-agents-cli/configs/.gigachat/config.toml ~/.gigachat/config.toml
```

**3. Edit `~/.gigachat/config.toml`:**

```toml
sandbox_mode = "danger-full-access"
approval_policy = "never"
model = "GigaChat/GigaChat-2-Max"
model_provider = "MYPROVIDER"
model_reasoning_effort = "high"

[sandbox_workspace_write]
network_access = true

[model_providers.MYPROVIDER]
name = "AnyText"
base_url = "https://host-api.example.com/v1"
env_key = "MY_API_KEY"
wire_api = "chat"
```

**4. Set API key:**

```bash
export MY_API_KEY="YOUR_GIGACHAT_CREDENTIALS"
```

**5. Configure agent in Consilium:**
- **Nickname**: `GigaChat`
- **Agent type**: `OpenAI Codex CLI`
- **Command path**: `gigachat`
- **Avatar**: `🇷🇺`

#### How These Scripts Work

All wrapper scripts for Codex CLI have a simple structure:

```bash
#!/usr/bin/env bash
set -euo pipefail

export CODEX_HOME="${HOME}/.provider"
export API_KEY="YOUR_API_KEY"

exec codex "$@"
```

- `CODEX_HOME` — specifies where `codex` should look for `config.toml`
- `API_KEY` — environment variable with API key (variable name corresponds to `env_key` in config.toml)
- `exec codex "$@"` — runs `codex` with passed arguments

---

## Recommendations

### Backend Selection

| Provider | API standard | Agent type | Command path |
|-----------|--------------|-----------|--------------|
| Anthropic Claude | Anthropic | `Anthropic Claude CLI` | `claude` |
| OpenAI GPT | OpenAI | `OpenAI Codex CLI` | `codex` |
| Google Gemini | Custom | `Gemini CLI` | `gemini` (Artel fork) |
| Zhipu GLM | Anthropic | `Anthropic Claude CLI` | `glm` (wrapper) |
| MiniMax-M2 | Anthropic | `Anthropic Claude CLI` | `minimax` (wrapper) |
| Alibaba Qwen | OpenAI | `OpenAI Codex CLI` | `qwen` (wrapper) |
| DeepSeek | OpenAI | `OpenAI Codex CLI` | `deepseek` (wrapper) |
| Sber GigaChat | OpenAI | `OpenAI Codex CLI` | `gigachat` (wrapper) |

### API Key Security

**✅ Good:**
- Store in `~/.provider/settings.json` or `~/.provider/config.toml`
- Use environment variables (`export API_KEY=...`)
- Files with permissions `600` (`chmod 600 settings.json` or `chmod 600 config.toml`)

**❌ Bad:**
- Hardcode in scripts
- Commit to git
- Store in plain sight

**Examples:**
```bash
# For Claude CLI wrappers
chmod 600 ~/.glm/settings.json

# For Codex CLI wrappers
chmod 600 ~/.qwen/config.toml
chmod 600 ~/.deepseek/config.toml
chmod 600 ~/.gigachat/config.toml
```

### Testing New Agents

1. Test CLI manually
2. Run Consilium with logs: `consilium DEBUG [ or TRACE ]`
3. Create test agent through `Ctrl+S` → Members
4. Send test message: `@TestAgent hello`
5. Check output in chat and, if necessary, in logs

---

## See Also

- [Installation](install.md) - Application installation
- [Settings](settings.md) — Configuring agents and roles
- [User Guide](usage.md) — Working with the application
