# Настройка CLI-агентов

## Обзор

Consilium Agent работает с внешними CLI-инструментами AI-провайдеров для взаимодействия с языковыми моделями. Каждый агент подключается через свою CLI-утилиту, которая должна быть установлена и настроена в вашей системе.

### Официальная поддержка

Приложение **официально поддерживает** только два типа CLI-утилит:
- ✅ **Claude CLI** (Anthropic) — `@anthropic-ai/claude-code`
- ✅ **Codex CLI** (OpenAI) — `@openai/openai-codex`

### Gemini CLI (специальный форк)

⚠️ **Важно:** Официальный CLI Gemini от Google **не поддерживается**.

Вместо этого используйте специальный форк, созданный авторами Consilium Agent:
- 🔗 [gemini-artel-fork](https://github.com/eavookindroid/gemini-artel-fork)

Этот форк добавляет необходимую функциональность для работы с Consilium (session management).

### Другие провайдеры и модели

Вы можете **самостоятельно создать скрипты-обёртки** для использования других LLM провайдеров и моделей:
- Zhipu AI (GLM)
- Alibaba Qwen
- DeepSeek
- Сбер GigaChat
- и т.д.

См. раздел [Подключение альтернативных провайдеров](#подключение-альтернативных-провайдеров) с примерами настройки через Claude CLI и Codex CLI.

---

## Официальные CLI-утилиты

### Claude CLI (Anthropic)

**Установка:**
```bash
npm install -g @anthropic-ai/claude-code
```

**Старт**
```bash
claude
```

**Проверка:**
```bash
claude -p "Hello, test message"
```

**Конфигурация в Consilium:**
- `Agent type`: `Anthropic Claude CLI`
- `Command path`: `claude`

---

### Codex CLI (OpenAI)

**Установка:**
```bash
npm install -g @openai/openai-codex
```

**Старт**
```bash
codex
```

**Проверка:**
```bash
codex exec --skip-git-repo-check "Hello!"
```

**Конфигурация в Consilium:**
- `Agent type`: `OpenAI Codex CLI`
- `Command path`: `codex`

---

### Gemini CLI (форк Artel)

⚠️ **Важно:** Требуется специальный форк с поддержкой сессий!

Стандартный Gemini CLI от Google не поддерживает:
- Session ID в неинтерактивном режиме

**Установка:**
```bash
git clone https://github.com/eavookindroid/gemini-artel-fork.git
cd gemini-artel-fork
./install.sh
```

**Проверка версии:**
```bash
gemini --version
# Должен содержать суффикс "-artel-"
```

Если суффикса нет — установлен стандартный CLI, который не будет работать с Consilium.

**Конфигурация в Consilium:**
- `Agent type`: `Gemini CLI`
- `Command path`: `gemini`

**Особенности:**
- JSON output с session ID
- Поддержка role="assistant" сообщений
- Обработка tool_use событий

---

## Подключение альтернативных провайдеров

Consilium поддерживает подключение любых LLM провайдеров через обёртки для Claude CLI или Codex CLI.

>💡 Вы можете использовать официальные cli-агенты `claude` и `codex` для дешевого или полностью бесплатного использования, настроив скрипты-обертки для доступа к сторонним LLM-провайдерам и моделям, если знаете где их найти :)

скачайте репозиторий проекта
```bash
git clone https://github.com/eavookindroid/consilium-agent-tui.git
cd consilium-agent-tui
```

или обновите его
```bash
cd consilium-agent-tui
git pull
```

### Через Claude CLI (Anthropic API стандарт)

Подходит для провайдеров, совместимых с Anthropic API.

#### Пример: Zhipu AI (GLM-4)

Готовый скрипт-обёртка находится в `custom-agents-cli/bin/glm` с конфигом в `custom-agents-cli/configs/.glm/settings.json`.

**1. Скопируйте скрипт:**

```bash
cp custom-agents-cli/bin/glm ~/.local/bin/glm
chmod +x ~/.local/bin/glm
```

**2. Скопируйте и настройте конфиг:**

```bash
mkdir -p ~/.glm
cp custom-agents-cli/configs/.glm/settings.json ~/.glm/settings.json
```

**3. Отредактируйте `~/.glm/settings.json`:**

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

Замените `YOUR_API_KEY` на ваш API-ключ от провайдера.

**4. Настройте агента в Consilium:**
- `Ctrl+S` → Members → Add Agent
- **Nickname**: `GLM`
- **Agent type**: `Anthropic Claude CLI`
- **Command path**: `glm`
- **Avatar**: `🇨🇳`

**Как работает скрипт:**
- Автоматически ищет настройки в `~/.glm/` с приоритетами
- Подставляет `--settings` для `claude` CLI
- Поддерживает переменные окружения `GLM_SETTINGS_FILE`, `GLM_PROFILE`

---

### Через Codex CLI (OpenAI API стандарт)

Подходит для провайдеров, совместимых с OpenAI API.

Готовые скрипты-обёртки находятся в `custom-agents-cli/bin/` с конфигами в `custom-agents-cli/configs/`.

#### Пример 1: Alibaba Qwen

**1. Скопируйте скрипт:**

```bash
cp custom-agents-cli/bin/qwen ~/.local/bin/qwen
chmod +x ~/.local/bin/qwen
```

**2. Скопируйте и настройте конфиг:**

```bash
mkdir -p ~/.qwen
cp custom-agents-cli/configs/.qwen/config.toml ~/.qwen/config.toml
```

**3. Отредактируйте `~/.qwen/config.toml`:**

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

**4. Установите API-ключ:**

```bash
export API_KEY="YOUR_QWEN_API_KEY"
```

**5. Настройте агента в Consilium:**
- `Ctrl+S` → Members → Add Agent
- **Nickname**: `Qwen`
- **Agent type**: `OpenAI Codex CLI`
- **Command path**: `qwen`
- **Avatar**: `🚀`

#### Пример 2: DeepSeek

**1. Скопируйте скрипт:**

```bash
cp custom-agents-cli/bin/deepseek ~/.local/bin/deepseek
chmod +x ~/.local/bin/deepseek
```

**2. Скопируйте и настройте конфиг:**

```bash
mkdir -p ~/.deepseek
cp custom-agents-cli/configs/.deepseek/config.toml ~/.deepseek/config.toml
```

**3. Отредактируйте `~/.deepseek/config.toml`:**

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

**4. Установите API-ключ:**

```bash
export API_KEY="YOUR_DEEPSEEK_API_KEY"
```

**5. Настройте агента в Consilium:**
- **Nickname**: `DeepSeek`
- **Agent type**: `OpenAI Codex CLI`
- **Command path**: `deepseek`

#### Пример 3: GigaChat (Сбер)

**1. Скопируйте скрипт:**

```bash
cp custom-agents-cli/bin/gigachat ~/.local/bin/gigachat
chmod +x ~/.local/bin/gigachat
```

**2. Скопируйте и настройте конфиг:**

```bash
mkdir -p ~/.gigachat
cp custom-agents-cli/configs/.gigachat/config.toml ~/.gigachat/config.toml
```

**3. Отредактируйте `~/.gigachat/config.toml`:**

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

**4. Установите API-ключ:**

```bash
export MY_API_KEY="YOUR_GIGACHAT_CREDENTIALS"
```

**5. Настройте агента в Consilium:**
- **Nickname**: `GigaChat`
- **Agent type**: `OpenAI Codex CLI`
- **Command path**: `gigachat`
- **Avatar**: `🇷🇺`

#### Как работают эти скрипты

Все скрипты-обёртки для Codex CLI имеют простую структуру:

```bash
#!/usr/bin/env bash
set -euo pipefail

export CODEX_HOME="${HOME}/.provider"
export API_KEY="YOUR_API_KEY"

exec codex "$@"
```

- `CODEX_HOME` — указывает где `codex` должен искать `config.toml`
- `API_KEY` — переменная окружения с API-ключом (имя переменной соответствует `env_key` в config.toml)
- `exec codex "$@"` — запускает `codex` с переданными аргументами

---

## Рекомендации

### Выбор Backend

| Провайдер | API стандарт | Agent type | Command path |
|-----------|--------------|-----------|--------------|
| Anthropic Claude | Anthropic | `Anthropic Claude CLI` | `claude` |
| OpenAI GPT | OpenAI | `OpenAI Codex CLI` | `codex` |
| Google Gemini | Custom | `Gemini CLI` | `gemini` (Artel fork) |
| Zhipu GLM | Anthropic | `Anthropic Claude CLI` | `glm` (обёртка) |
| MiniMax-M2 | Anthropic | `Anthropic Claude CLI` | `minimax` (обёртка) |
| Alibaba Qwen | OpenAI | `OpenAI Codex CLI` | `qwen` (обёртка) |
| DeepSeek | OpenAI | `OpenAI Codex CLI` | `deepseek` (обёртка) |
| Сбер GigaChat | OpenAI | `OpenAI Codex CLI` | `gigachat` (обёртка) |

### Безопасность API ключей

**✅ Хорошо:**
- Хранить в `~/.provider/settings.json` или `~/.provider/config.toml`
- Использовать переменные окружения (`export API_KEY=...`)
- Файлы с правами `600` (`chmod 600 settings.json` или `chmod 600 config.toml`)

**❌ Плохо:**
- Хардкодить в скриптах
- Коммитить в git
- Хранить в открытом виде

**Примеры:**
```bash
# Для Claude CLI обёрток
chmod 600 ~/.glm/settings.json

# Для Codex CLI обёрток
chmod 600 ~/.qwen/config.toml
chmod 600 ~/.deepseek/config.toml
chmod 600 ~/.gigachat/config.toml
```

### Тестирование новых агентов

1. Проверьте CLI вручную
2. Запустите Consilium с логами: `consilium DEBUG [ или TRACE ]`
3. Создайте тестового агента через `Ctrl+S` → Members
4. Отправьте тестовое сообщение: `@TestAgent hello`
5. Проверьте вывод в чате и, при необходимости, в логах

---

## См. также

- [Установка](install.md) - Установка приложения
- [Настройки](settings.md) — Настройка агентов и ролей
- [Руководство пользователя](usage.md) — Работа с приложением
