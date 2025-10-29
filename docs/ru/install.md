# Установка Consilium Agent

## Системные требования

**Операционные системы:**
- Linux (x86_64, aarch64, riscv64)
- macOS (x86_64, arm)
- Windows - запланировано

**Зависимости:**
- Нет внешних зависимостей для работы приложения
- Для использования агентов: установленные CLI-утилиты (`claude`, `codex`, `gemini`)

---

## Установка из релиза

### 1. Скачайте бинарник

Перейдите на страницу [Releases](https://github.com/eavookindroid/consilium-agent-tui/releases) и скачайте версию для вашей системы


### 2. Переименуйте и установите

**Вариант 1: Установка для текущего пользователя (рекомендуется)**

```bash
# Создайте директорию если её нет
mkdir -p ~/.local/bin

# Переименуйте и переместите
mv consilium-*-* ~/.local/bin/consilium

# Установите права на выполнение
chmod +x ~/.local/bin/consilium
```

Убедитесь, что `~/.local/bin` в вашем `$PATH`:
```bash
echo $PATH | grep -q "$HOME/.local/bin" || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

**Вариант 2: Системная установка (требует sudo)**

```bash
# Переименуйте и переместите
sudo mv consilium-*-* /usr/local/bin/consilium

# Установите права
sudo chmod +x /usr/local/bin/consilium
```

### 3. Проверьте установку

```bash
consilium --version
```

Вы должны увидеть версию приложения.

### 4. Установите ролевые промпты по умолчанию

```bash
consilium --install
```

Эта команда установит стандартные ролевые промпты.

**Что устанавливается:**
- Product Owner
- Scrum Master
- Developer
- Architect
- QA Engineer
- DevOps
- Code Reviewer
- Security Expert
- _... список может пополняться в будущих релизах_
---

## Первый запуск

После установки запустите приложение в директории вашего проекта:

```bash
cd /path/to/your/project
consilium
```

При первом запуске:
1. Увидите сообщение "Press Ctrl+G to begin..."
2. Нажмите `Ctrl+S` чтобы открыть настройки
3. Добавьте агентов через `Members` → `Add Agent`
4. Назначьте роли каждому агенту
5. Нажмите `Ctrl+G` для запуска

Подробнее см. [Настройки приложения](settings.md) и [Руководство пользователя](usage.md).

---

## Установка CLI-агентов

Для работы приложения необходимо установить хотя бы один CLI-агент:

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

### Gemini CLI (форк Artel)

```bash
git clone https://github.com/eavookindroid/gemini-artel-fork.git
cd gemini-artel-fork
./install.sh
gemini
```

Подробные инструкции см. в [CLI-агенты](cli-agents.md).

---

## Обновление

### Обновление приложения

1. Скачайте новую версию из релизов
2. Замените старый бинарник:

```bash
# Для пользовательской установки
mv consilium-*-* ~/.local/bin/consilium
chmod +x ~/.local/bin/consilium

# Для системной установки
sudo mv consilium-*-* /usr/local/bin/consilium
sudo chmod +x /usr/local/bin/consilium
```

3. Проверьте версию:
```bash
consilium --version
```

### Обновление ролевых промптов

Если в новой версии обновлены стандартные роли:

```bash
consilium --install
```

⚠️ **Внимание:** Это перезапишет стандартные роли. Ваши кастомные роли не будут затронуты.

---

## Удаление

### Удаление приложения

**Пользовательская установка:**
```bash
rm ~/.local/bin/consilium
```

**Системная установка:**
```bash
sudo rm /usr/local/bin/consilium
```

### Удаление пользовательских данных

```bash
# Настройки и сессии
rm -rf ~/.consilium/
```

⚠️ **Осторожно:** Это удалит всю историю чатов и настройки Consilium для ваших проектных workspace.

---

## Устранение проблем

### Команда не найдена

```bash
# Проверьте, что бинарник в PATH
which consilium

# Если не найден, проверьте расположение
ls -la ~/.local/bin/consilium
ls -la /usr/local/bin/consilium

# Убедитесь что PATH настроен
echo $PATH
```

**Решение для macOS:**
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**Решение для Linux:**
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### Ошибка прав доступа

```bash
# Установите права на выполнение
chmod +x ~/.local/bin/consilium
```

### macOS Gatekeeper блокирует запуск

При первом запуске на macOS может появиться предупреждение безопасности:

```bash
# Снимите карантин с бинарника
xattr -d com.apple.quarantine ~/.local/bin/consilium

# Или разрешите в Системных настройках:
# System Preferences → Security & Privacy → General → Allow
```

### Проблемы на Apple Silicon (M1/../M4/..)

Если при запуске ошибка архитектуры:

```bash
# Проверьте архитектуру
file ~/.local/bin/consilium

# Должно быть: Mach-O 64-bit executable arm64
```
Скачайте правильную версию: `consilium-arm64-darwin`

---

## См. также

- [CLI-агенты](cli-agents.md) — Установка и настройка CLI-утилит
- [Настройки](settings.md) — Настройка агентов и ролей
- [Руководство пользователя](usage.md) — Работа с приложением
