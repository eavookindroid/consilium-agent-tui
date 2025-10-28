# Installing Consilium Agent

## System Requirements

**Operating Systems:**
- Linux (x86_64, aarch64, riscv64)
- macOS (x86_64, arm)
- Windows - planned

**Dependencies:**
- No external dependencies for application operation
- For using agents: installed CLI utilities (`claude`, `codex`, `gemini`)

---

## Installing from Release

### 1. Download the Binary

Go to the [Releases](https://github.com/eavookindroid/consilium-agent-tui/releases) page and download the version for your system


### 2. Rename and Install

**Option 1: Install for Current User (recommended)**

```bash
# Create directory if it doesn't exist
mkdir -p ~/.local/bin

# Rename and move
mv consilium-*-* ~/.local/bin/consilium

# Set execute permissions
chmod +x ~/.local/bin/consilium
```

Ensure `~/.local/bin` is in your `$PATH`:
```bash
echo $PATH | grep -q "$HOME/.local/bin" || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

**Option 2: System Installation (requires sudo)**

```bash
# Rename and move
sudo mv consilium-*-* /usr/local/bin/consilium

# Set permissions
sudo chmod +x /usr/local/bin/consilium
```

### 3. Verify Installation

```bash
consilium --version
```

You should see the application version.

### 4. Install Default Role Prompts

```bash
consilium --install
```

This command will install standard role prompts.

**What gets installed:**
- Product Owner
- Scrum Master
- Developer
- Architect
- QA Engineer
- DevOps
- Code Reviewer
- Security Expert
- _... the list may be expanded in future releases_
---

## First Launch

After installation, launch the application in your project directory:

```bash
cd /path/to/your/project
consilium
```

On first launch:
1. You'll see the message "Press Ctrl+G to begin..."
2. Press `Ctrl+S` to open settings
3. Add agents through `Members` → `Add Agent`
4. Assign roles to each agent
5. Press `Ctrl+G` to start

For more details see [Application Settings](settings.md) and [User Guide](usage.md).

---

## Installing CLI Agents

To work with the application, you need to install at least one CLI agent:

### Claude CLI (Anthropic)

```bash
npm install -g @anthropic-ai/claude-code
claude
```

### Codex CLI (OpenAI)

```bash
npm install -g @openai/openai-codex
codex
```

### Gemini CLI (Artel fork)

```bash
git clone https://github.com/eavookindroid/gemini-artel-fork.git
cd gemini-artel-fork
./install.sh
gemini
```

For detailed instructions see [CLI Agents](cli-agents.md).

---

## Updating

### Updating the Application

1. Download new version from releases
2. Replace old binary:

```bash
# For user installation
mv consilium-*-* ~/.local/bin/consilium
chmod +x ~/.local/bin/consilium

# For system installation
sudo mv consilium-*-* /usr/local/bin/consilium
sudo chmod +x /usr/local/bin/consilium
```

3. Check version:
```bash
consilium --version
```

### Updating Role Prompts

If standard roles were updated in the new version:

```bash
consilium --install
```

⚠️ **Warning:** This will overwrite standard roles. Your custom roles will not be affected.

---

## Uninstallation

### Uninstalling the Application

**User installation:**
```bash
rm ~/.local/bin/consilium
```

**System installation:**
```bash
sudo rm /usr/local/bin/consilium
```

### Removing User Data

```bash
# Settings and sessions
rm -rf ~/.consilium/
```

⚠️ **Caution:** This will remove all chat history and Consilium settings for your project workspaces.

---

## Troubleshooting

### Command Not Found

```bash
# Check that binary is in PATH
which consilium

# If not found, check location
ls -la ~/.local/bin/consilium
ls -la /usr/local/bin/consilium

# Ensure PATH is configured
echo $PATH
```

**Solution for macOS:**
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**Solution for Linux:**
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### Permission Error

```bash
# Set execute permissions
chmod +x ~/.local/bin/consilium
```

### macOS Gatekeeper Blocks Launch

On first launch on macOS, a security warning may appear:

```bash
# Remove quarantine from binary
xattr -d com.apple.quarantine ~/.local/bin/consilium

# Or allow in System Preferences:
# System Preferences → Security & Privacy → General → Allow
```

### Issues on Apple Silicon (M1/M2/M3)

If you get architecture error on launch:

```bash
# Check architecture
file ~/.local/bin/consilium

# Should be: Mach-O 64-bit executable arm64
```
Download the correct version: `consilium-macos-arm64-vX.Y.Z`

---

## See Also

- [CLI Agents](cli-agents.md) — Installing and configuring CLI utilities
- [Settings](settings.md) — Configuring agents and roles
- [User Guide](usage.md) — Working with the application
