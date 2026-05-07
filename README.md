# Лаунчер метаданных YouTube \ YouTube Metadata Launcher

🎬 **YouTube Metadata Launcher** — локальный Python-лаунчер для подготовки YouTube-видео: генерация заголовков, описаний, хэштегов, тегов, превью и массовое обновление роликов через YouTube API.

## ✨ Возможности

- 🧠 Генерация SEO-метаданных через OpenAI или Ollama.
- 📦 Работа с несколькими data-pack проектами.
- 📝 Создание заголовка, описания, хэштегов и ключевых слов по строгому пайплайну.
- 🖼️ Генерация YouTube-превью через OpenAI Image API.
- ✅ Проверка структуры данных, лимитов YouTube и качества результата.
- 🚀 Массовое обновление метаданных и публикация видео через YouTube API.
- 🧾 HTML/JSON отчеты по запускам в локальной истории.

## 📦 Проекты данных

Названия проектов в `data/` используются как стабильные заголовки паков:

| Папка | Заголовок проекта | Для чего |
| --- | --- | --- |
| `data/bf6` | Battlefield 6 | Гайды, настройки, мультиплеер и оптимизация BF6 |
| `data/cs2` | Counter-Strike 2 | Гайды, раскидки, настройки и тактика CS2 |
| `data/roblox` | Roblox VPN | VPN-подключение для Roblox |
| `data/router-vpn-zm` | Keenetic WireGuard VPN | Настройка WireGuard VPN на роутерах Keenetic |
| `data/vpn-groza` | VPN GROZA | VLESS VPN для Android, iPhone и приложений |
| `data/vpn-raketa` | VPN RAKETA | VPN для Android, iPhone, Windows и QR-подключения |

В каждом проекте должны быть файлы:

- `titles.txt` — варианты заголовков, по одному на строку.
- `description.txt` — базовый текст описания.
- `hashtags.txt` — хэштеги через запятую или по строкам.
- `keywords.txt` — ключевые слова через запятую.

## 🛠️ Установка из GitHub

```bash
git clone https://github.com/xelsing1991-maker/youtube-metadata-launcher.git
cd youtube-metadata-launcher
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Для Linux/macOS активация окружения:

```bash
source .venv/bin/activate
```

## 🔐 Настройка авторизации

Секреты не хранятся в репозитории. Добавьте их локально после установки:

1. Создайте папку `credentials/`.
2. Скачайте OAuth client JSON в Google Cloud Console.
3. Положите файл как `credentials/client_secret.json`.
4. Задайте OpenAI ключ через переменную окружения:

```bash
set OPENAI_API_KEY=sk-...
```

Для PowerShell:

```powershell
$env:OPENAI_API_KEY="sk-..."
```

При первой публикации YouTube откроет OAuth-авторизацию, после чего локально появится `credentials/credentials.json`. Эта папка добавлена в `.gitignore`.

## ⚙️ Конфигурация

Главный конфиг лежит в `config/config.json`.

Что обычно менять:

- `paths.data_folder` — папка проекта данных.
- `metadata.referral_link` — ссылка или хвост описания.
- `llm.provider` — `openai` или `ollama`.
- `llm.openai_model_name` — модель для генерации текста.
- `image_generation.model`, `size`, `quality` — настройки превью.

Для Ollama можно задать:

```bash
set OLLAMA_URL=http://127.0.0.1:11434
set OLLAMA_MODEL=qwen3:30
```

## ▶️ Запуск

Основная точка входа:

```bash
python launcher.py
```

В меню можно:

- выбрать data-pack;
- проверить конфигурацию;
- сгенерировать тестовые метаданные;
- обработать плейлист;
- очистить локальные OAuth-файлы;
- запустить генерацию превью.

## 🖼️ Генерация превью

Dry-run без платного запроса:

```bash
python launcher.py --generate-preview --data-folder data/router-vpn-zm --preview-topic "VPN для Keenetic WireGuard" --thumbnail-text "Keenetic + VPN" --dry-run
```

Реальная генерация:

```bash
python launcher.py --generate-preview --data-folder data/vpn-raketa --preview-topic "VPN для Android и iPhone" --thumbnail-text "VPN за 5 минут"
```

Результаты сохраняются локально в `generated_previews/` и не отправляются в git.

## 🧩 Промты LLM

Промты лежат в `config/prompts/`:

- `stage_title.prompt.txt` — заголовок.
- `stage_description.prompt.txt` — описание.
- `stage_hashtags_keys.prompt.txt` — хэштеги и промежуточные ключи.
- `stage_keywords.prompt.txt` — финальные keywords.

Пайплайн: `title -> description -> hashtags+keys -> keywords`.

## 🗂️ Основные файлы

- `launcher.py` — меню, генерация, публикация, превью.
- `generate_metadata.py` — загрузка конфига и YouTube OAuth.
- `quality_checks.py` — проверки данных и результата.
- `fuzzy_utils.py` — дедупликация и похожесть строк.
- `config/config.json` — настройки без секретов.
- `data/<project>/` — проекты данных.
- `credentials/` — локальные OAuth-файлы, не коммитятся.

## 🚀 Публикация изменений в Git

```bash
git status
git add .
git commit -m "Обновить документацию и безопасную конфигурацию"
git push
```

Перед коммитом проверьте, что в индексе нет:

- `credentials/`
- `generated_previews/`
- `history_reports/`
- `.env`
- API-ключей в `config/config.json`
