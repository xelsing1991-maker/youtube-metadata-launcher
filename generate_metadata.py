import json
import random
import logging
import re
import os
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from fuzzy_utils import dedupe_by_similarity
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Функция загрузки конфигурации

def load_config(path: str) -> Dict:
    """Load config and resolve relative paths against the config file location."""
    cfg_path = Path(path).expanduser().resolve()
    base_dir = cfg_path.parent
    with open(cfg_path, 'r', encoding='utf-8-sig') as f:
        config = json.load(f)

    paths = config.get('paths', {})
    resolved = {}
    for key, value in paths.items():
        p = Path(value)
        resolved[key] = str(p if p.is_absolute() else base_dir / p)
    config['paths'] = resolved
    config["_config_dir"] = str(base_dir)

    llm_cfg = config.setdefault("llm", {})
    env_ollama_url = os.getenv("OLLAMA_URL", "").strip()
    env_ollama_model = os.getenv("OLLAMA_MODEL", "").strip()
    env_openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    env_openai_base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    env_openai_model = os.getenv("OPENAI_MODEL", "").strip()
    if env_ollama_url:
        llm_cfg["ollama_url"] = env_ollama_url
    if env_ollama_model:
        llm_cfg["model_name"] = env_ollama_model
    if env_openai_key:
        llm_cfg["openai_api_key"] = env_openai_key
    if env_openai_base_url:
        llm_cfg["openai_base_url"] = env_openai_base_url
    if env_openai_model:
        llm_cfg["openai_model_name"] = env_openai_model
    return config

# Аутентификация YouTube API

def authenticate_youtube(config: Dict) -> Optional[object]:
    logger.info("Начинается аутентификация YouTube API.")
    try:
        scopes = config.get('metadata', {}).get(
            'scopes',
            ["https://www.googleapis.com/auth/youtube.force-ssl"]
        )
        creds_folder = Path(config['paths']['credentials_folder'])
        if not creds_folder.exists():
            logger.error(f"Папка {creds_folder} не существует.")
            raise FileNotFoundError(f"Папка {creds_folder} не существует.")
        credentials = None
        credentials_path = Path(config['paths']['credentials_file'])
        if credentials_path.exists():
            try:
                credentials = Credentials.from_authorized_user_file(str(credentials_path), scopes)
                logger.info("Загружены существующие учетные данные.")
            except ValueError as e:
                logger.warning(f"Файл credentials.json поврежден: {e}. Удаление и повторная аутентификация.")
                credentials_path.unlink()
        if credentials and credentials.valid:
            logger.info("Учетные данные действительны.")
        elif credentials and credentials.expired and credentials.refresh_token:
            logger.info("Обновление истекших учетных данных.")
            try:
                credentials.refresh(Request())
                logger.info("Токен успешно обновлен.")
            except RefreshError as e:
                logger.warning(f"Не удалось обновить токен: {e}. Запуск новой аутентификации.")
                credentials = None
        if not credentials:
            client_secret_path = Path(config['paths']['client_secret_file'])
            if not client_secret_path.exists():
                logger.error(f"Файл client_secret.json не найден: {client_secret_path}.")
                raise FileNotFoundError(f"Файл client_secret.json не найден: {client_secret_path}")
            # Validate client_secret.json early to give понятное сообщение
            try:
                json.load(open(client_secret_path, "r", encoding="utf-8"))
            except Exception as e:
                logger.error(f"Файл client_secret.json поврежден или пуст: {e}")
                print(f"✖ Файл client_secret.json поврежден или пуст: {client_secret_path}. Замените на актуальный JSON из Google Cloud Console.")
                return None
            logger.info("Запуск нового OAuth 2.0 потока.")
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), scopes)
            credentials = flow.run_local_server(port=0)
            logger.info("Получены новые учетные данные.")
            credentials_path.write_text(credentials.to_json(), encoding='utf-8')
            logger.info(f"Учетные данные сохранены в {credentials_path}")
        # cache_discovery=False убирает предупреждение "file_cache is only supported with oauth2client<4.0.0"
        return build("youtube", "v3", credentials=credentials, cache_discovery=False)
    except FileNotFoundError as e:
        logger.error(f"Ошибка доступа к файлам: {e}")
        print(f"❌ Ошибка доступа к файлам: {e}")
        return None
    except Exception as e:
        logger.error(f"Ошибка аутентификации: {e}")
        print(f"❌ Ошибка аутентификации: {e}")
        return None

# Генерация метаданных

def generate_metadata(config: Dict, original_title: str = "") -> Tuple[Optional[str], Optional[str], Optional[str]]:
    logger.info("Генерация метаданных.")
    # Меню выбора устройства
    device = input('Выберите устройство для продвижения (ios/android): ').strip().lower()
    if device not in ['ios', 'android']:
        print('Некорректный выбор. Используется ios по умолчанию.')
        device = 'ios'

    # Пути к файлам
    titles_path = Path(config['paths']['titles_file'])
    keywords_path = Path(config['paths']['keywords_file'])
    hashtags_path = Path(config['paths']['hashtags_file'])
    device_keywords_path = Path(config['paths'][f'{device}_keywords_file'])

    # Считывание данных из файлов
    titles = []
    for line in titles_path.read_text(encoding='utf-8-sig').splitlines():
        if '|' in line:
            # Извлекаем титул после последней |
            title = line.split('|')[2].strip()
            if title:
                titles.append(title)
        elif line.strip():
            titles.append(line.strip())
    # Fuzzy-dedupe titles to build a cleaner title core
    titles = dedupe_by_similarity(titles, threshold=0.85, keep="first")

    # Парсинг ключевых слов с правильным извлечением после последней |
    keywords_text = keywords_path.read_text(encoding='utf-8-sig')
    print(f"\n🔍 Отладка keywords.txt:")
    print(f"Первые 100 символов: {keywords_text[:100]}")

    if '|' in keywords_text:
        # Извлекаем часть после последней |
        keywords_data = keywords_text.split('|')[-1]
        print(f"Данные после последней |: {keywords_data[:100]}")
    else:
        keywords_data = keywords_text
        print(f"Нет символа |, используем весь текст")

    # Разделяем по запятым и очищаем от лишних пробелов
    keywords = [keyword.strip() for keyword in keywords_data.split(',') if keyword.strip()]
    # Fuzzy-dedupe keywords to form a cleaner core
    keywords = dedupe_by_similarity(keywords, threshold=0.85, keep="first")
    print(f"Первые 10 keywords: {keywords[:15]}")

    # Парсинг ключевых слов устройств с правильным извлечением после последней |
    device_keywords_text = device_keywords_path.read_text(encoding='utf-8-sig')
    print(f"\n🔍 Отладка {device}_keywords.txt:")
    print(f"Первые 100 символов: {device_keywords_text[:100]}")

    if '|' in device_keywords_text:
        # Извлекаем часть после последней |
        device_keywords_data = device_keywords_text.split('|')[-1]
        print(f"Данные после последней |: {device_keywords_data[:100]}")
    else:
        device_keywords_data = device_keywords_text
        print(f"Нет символа |, используем весь текст")

    # Разделяем по запятым и очищаем от лишних пробелов
    device_keywords = [keyword.strip() for keyword in device_keywords_data.split(',') if keyword.strip()]
    # Fuzzy-dedupe device-specific keywords as well
    device_keywords = dedupe_by_similarity(device_keywords, threshold=0.85, keep="first")
    print(f"Первые 10 device_keywords: {device_keywords[:15]}")

    hashtags = ['#' + hashtag.strip() for hashtag in hashtags_path.read_text(encoding='utf-8-sig').splitlines() if hashtag.strip()]

# Генерация заголовка строго по новому шаблону: emoji + заголовок + на устройство + ключевое слово
    emoji = random.choice(config['metadata']['emojis'])
    title = random.choice(titles).split('|')[1] if '|' in titles[0] else random.choice(titles)
    device_keyword = random.choice(device_keywords)
    keyword = random.choice(keywords)
    final_title = f"{emoji} {title} на {device_keyword} {keyword}"

# Формирование списка хэштегов (20 штук случайно)
    random_hashtags = random.sample(hashtags, min(50, len(hashtags)))
    hashtags_string = ', '.join(random_hashtags)

    # Формирование списка ключевых слов (20 штук случайно)
    random_keywords = random.sample(keywords, min(50, len(keywords)))
    keywords_string = ', '.join(random_keywords)

    # Формирование описания с учетом требований: 1 строка — название, 2 строка — бот
    description = (
        f"{final_title}\n"
        f"🤖 Бот: {config['metadata']['referral_link']}\n"
        f"{config['metadata']['vpn_description']}\n\n"
        f"{hashtags_string}\n\n"
        f"Ключевые слова: {keywords_string}\n"
    )[:config['youtube']['max_description_length']]

# Формирование тегов по пропорциям: 60% keywords, 20% title words, 20% device keywords
    def clean_tag(tag):
        """Очищает тег от лишних символов и пробелов"""
        return tag.strip().replace('  ', ' ')

    max_tags_length = config['youtube'].get('max_tags_length', 500)

    # Подготавливаем все категории тегов
    # 1. Ключевые слова (60%)
    keywords_tags = [clean_tag(kw) for kw in keywords if len(clean_tag(kw)) > 1]

    # 2. Слова из заголовков (20%)
    title_tags = []
    for title in titles:
        title_words = title.split()
        # Добавляем пары слов
        if len(title_words) >= 2:
            title_tag = title_words[0] + ' ' + title_words[1]
            title_tags.append(title_tag)
        # Добавляем отдельные слова длиннее 2 символов
        for word in title_words:
            if len(word) > 2:
                title_tags.append(word)
    title_tags = [clean_tag(t) for t in title_tags if len(clean_tag(t)) > 1]

    # 3. Ключевые слова устройств (20%)
    device_tags = [clean_tag(dk) for dk in device_keywords if len(clean_tag(dk)) > 1]

    # Убираем дубли из каждой категории
    keywords_tags = list(dict.fromkeys(keywords_tags))
    title_tags = list(dict.fromkeys(title_tags))
    device_tags = list(dict.fromkeys(device_tags))

    # Fuzzy de-duplication by Levenshtein similarity to build a cleaner core
    # Keeps first variants, removes near-duplicates (>= 0.85 similarity)
    keywords_tags = dedupe_by_similarity(keywords_tags, threshold=0.85, keep="first")
    title_tags = dedupe_by_similarity(title_tags, threshold=0.85, keep="first")
    device_tags = dedupe_by_similarity(device_tags, threshold=0.85, keep="first")

    # Рассчитываем пропорции по символам
    total_available_length = max_tags_length
    keywords_target_length = int(total_available_length * 0.6)  # 60%
    title_target_length = int(total_available_length * 0.2)     # 20%
    device_target_length = int(total_available_length * 0.2)    # 20%

    # Собираем теги по категориям с учетом лимитов
    final_tags = []

    # 1. Добавляем keywords теги (60%)
    random.shuffle(keywords_tags)
    current_length = 0
    for tag in keywords_tags:
        if current_length + len(tag) <= keywords_target_length:
            final_tags.append(tag)
            current_length += len(tag)
        else:
            break

    # 2. Добавляем title теги (20%)
    random.shuffle(title_tags)
    current_length = 0
    for tag in title_tags:
        if current_length + len(tag) <= title_target_length:
            final_tags.append(tag)
            current_length += len(tag)
        else:
            break

    # 3. Добавляем device теги (20%)
    random.shuffle(device_tags)
    current_length = 0
    for tag in device_tags:
        if current_length + len(tag) <= device_target_length:
            final_tags.append(tag)
            current_length += len(tag)
        else:
            break

    # Убираем общие дубли и перемешиваем финальный список
    final_tags = list(dict.fromkeys(final_tags))
    random.shuffle(final_tags)

    # Окончательная проверка общего лимита 500 символов
    filtered_tags = []
    total_length = 0
    for tag in final_tags:
        if total_length + len(tag) <= max_tags_length:
            filtered_tags.append(tag)
            total_length += len(tag)
        else:
            break

    tags_string = ', '.join(filtered_tags)

    # Отладочная информация о пропорциях
    keywords_length = sum(len(tag) for tag in final_tags if tag in keywords_tags)
    title_length = sum(len(tag) for tag in final_tags if tag in title_tags)
    device_length = sum(len(tag) for tag in final_tags if tag in device_tags)

    print(f"\n🔍 Анализ тегов:")
    print(f"Общая длина тегов: {len(tags_string)} символов")
    print(f"Keywords теги: ~{keywords_length} символов ({round(keywords_length/len(tags_string)*100 if tags_string else 0)}%)")
    print(f"Title теги: ~{title_length} символов ({round(title_length/len(tags_string)*100 if tags_string else 0)}%)")
    print(f"Device теги: ~{device_length} символов ({round(device_length/len(tags_string)*100 if tags_string else 0)}%)")

    return final_title, description, tags_string

# Основная функция

def main():
    config = load_config('D:/Python_2/config/config.json')
    youtube = authenticate_youtube(config)
    if youtube:
        print("✅ Аутентификация YouTube API прошла успешно!")
        # Тестируем генерацию метаданных
        title, description, tags = generate_metadata(config)
        print(f"\n📝 Сгенерированные метаданные:")
        print(f"Заголовок: {title}")
        print(f"\nОписание:\n{description}")
        if tags is None:
            print("\nТеги (0 символов): ")
        else:
            print(f"\nТеги ({len(tags)} символов): {tags}")
    else:
        print("❌ Не удалось аутентифицироваться в YouTube API")

if __name__ == "__main__":
    main()

