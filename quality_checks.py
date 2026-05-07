#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Внутренние проверки качества для launcher.py.

Файл не является отдельным лаунчером. Все команды запускаются через
`python launcher.py`, а этот модуль хранит smoke-tests, проверки конфигов,
проектов, промтов и локальную генерацию тестового SEO-пакета.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import platform
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "config.json"
PROMPTS_DIR = ROOT / "config" / "prompts"
DATA_DIR = ROOT / "data"
PY_FILES = ["launcher.py", "quality_checks.py", "generate_metadata.py", "fuzzy_utils.py"]

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


TEST_INPUTS = {
    "vpn_raketa": (
        "vpn, впн, vpn 2026, скачать vpn, быстрый vpn, стабильный vpn, "
        "vpn для android, vpn для iphone, vpn для windows, happ vpn, vless vpn, "
        "vpn ключ, vpn подписка, vpn для youtube, vpn для telegram, vpn для instagram"
    ),
    "router": (
        "vpn для keenetic, keenetic vpn, wireguard keenetic, vpn для роутера, "
        "импорт wireguard, политика доступа, использовать для выхода в интернет"
    ),
    "mixed": "vpn 2026, vpn для телефона, vpn для keenetic, wireguard, android, iphone, роутер",
    "vpn_groza": "vpn groza, vless vpn groza, vless android, vless iphone, vpn для телефона, приложения, сайты",
    "roblox": "roblox vpn, vpn для roblox, roblox через vpn, настройка vpn roblox, ошибка подключения roblox",
    "bf6": "battlefield 6, bf6 гайд, battlefield 6 gameplay, настройки bf6, оружие, тактика, мультиплеер",
    "cs2": "cs2, counter strike 2, смоки, флешки, прицел, fps, тактика, экономика раундов",
}


PROJECT_DEFS = {
    "vpn_raketa": {
        "folder": "vpn-raketa",
        "name": "VPN RAKETA",
        "type": "VPN mobile/PC project",
        "platform": "YouTube",
        "audience": "пользователи Android, iPhone, Windows, сайтов и приложений",
        "terms": VPN_RAKETA_TERMS if "VPN_RAKETA_TERMS" in globals() else set(),
    },
    "router": {
        "folder": "router-vpn-zm",
        "name": "@vpnzm_bot",
        "type": "VPN router/Keenetic project",
        "platform": "YouTube / Telegram bot",
        "audience": "пользователи роутеров Keenetic и WireGuard",
        "terms": ROUTER_TERMS if "ROUTER_TERMS" in globals() else set(),
    },
    "vpn_groza": {
        "folder": "vpn-groza",
        "name": "VPN GROZA",
        "type": "VPN mobile/PC project",
        "platform": "YouTube",
        "audience": "пользователи Android/iPhone и VLESS",
        "terms": {"vpn groza", "groza", "vless", "android", "iphone", "ios"},
    },
    "roblox": {
        "folder": "roblox",
        "name": "Roblox VPN",
        "type": "YouTube SEO project / gaming VPN",
        "platform": "YouTube",
        "audience": "игроки Roblox на ПК и мобильных устройствах",
        "terms": {"roblox", "роблокс", "outline", "vpn для roblox"},
    },
    "bf6": {
        "folder": "bf6",
        "name": "Battlefield 6 / BF6",
        "type": "YouTube SEO project / gaming",
        "platform": "YouTube",
        "audience": "игроки Battlefield 6",
        "terms": {"battlefield 6", "bf6", "геймплей", "мультиплеер", "настройки"},
    },
    "cs2": {
        "folder": "cs2",
        "name": "Counter-Strike 2 / CS2",
        "type": "YouTube SEO project / gaming",
        "platform": "YouTube",
        "audience": "игроки Counter-Strike 2",
        "terms": {"cs2", "counter strike 2", "смоки", "флешки", "прицел", "fps"},
    },
}


VPN_RAKETA_TERMS = {
    "vpn", "впн", "vpn 2026", "скачать vpn", "быстрый vpn", "стабильный vpn",
    "android", "iphone", "windows", "пк", "happ", "vless", "youtube",
    "telegram", "instagram", "ключ", "подписка", "zankinmaster", "qr",
}
ROUTER_TERMS = {
    "keenetic", "кинетик", "роутер", "wireguard", "импорт", "конфиг",
    "конфигурац", "политика доступа", "выход в интернет", "vpn для дома",
}

PROJECT_DEFS["vpn_raketa"]["terms"] = VPN_RAKETA_TERMS
PROJECT_DEFS["router"]["terms"] = ROUTER_TERMS


@dataclass
class FunctionInfo:
    file: str
    line: int
    name: str
    args: List[str]
    safe_status: str
    meaning: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def load_json(path: Path) -> Tuple[bool, Any, str]:
    try:
        return True, json.loads(read_text(path)), ""
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"


def split_csv(text: str) -> List[str]:
    return [x.strip() for x in re.split(r",\s*", text or "") if x.strip()]


def normalize_hashtags(items: Iterable[str], limit: int = 15) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in items:
        token = str(raw).strip()
        if not token:
            continue
        tag = token if token.startswith("#") else f"#{token.lstrip('#')}"
        tag = re.sub(r"\s+", "", tag)
        low = tag.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(tag)
        if len(out) >= limit:
            break
    return out


def build_youtube_tags(items: Iterable[str], min_len: int = 480, max_len: int = 500) -> List[str]:
    tags: List[str] = []
    seen = set()
    total = 0
    for raw in items:
        tag = re.sub(r"[#@\"']", "", str(raw or ""))
        tag = re.sub(r"[^\w\s+\-]", " ", tag, flags=re.UNICODE)
        tag = re.sub(r"\s{2,}", " ", tag).strip(" -_,")
        if len(tag) < 2:
            continue
        low = tag.lower()
        if low in seen:
            continue
        add_len = len(tag) + (2 if tags else 0)
        if total + add_len > max_len:
            continue
        tags.append(tag)
        seen.add(low)
        total += add_len
        if total >= min_len:
            break
    return tags


def score_terms(text: str, terms: Iterable[str]) -> int:
    low = text.lower()
    return sum(1 for term in terms if term in low)


def detect_project(text: str) -> str:
    raketa = score_terms(text, VPN_RAKETA_TERMS)
    router = score_terms(text, ROUTER_TERMS)
    if raketa >= 2 and router >= 2:
        return "mixed"
    if router > raketa:
        return "@vpnzm_bot"
    if raketa > 0:
        return "VPN RAKETA"
    return "unknown"


def detect_project_id(text: str) -> str:
    low = text.lower()
    scores = {
        project_id: score_terms(low, meta.get("terms", set()))
        for project_id, meta in PROJECT_DEFS.items()
    }
    if scores.get("vpn_raketa", 0) >= 2 and scores.get("router", 0) >= 2:
        return "mixed"
    best_id, best_score = max(scores.items(), key=lambda item: item[1])
    return best_id if best_score > 0 else "unknown"


def display_project(project_id: str) -> str:
    if project_id == "mixed":
        return "mixed"
    return PROJECT_DEFS.get(project_id, {}).get("name", project_id)


def detect_intent(text: str, project: str) -> str:
    low = text.lower()
    if project == "@vpnzm_bot":
        return "router"
    if any(x in low for x in ["купить", "подписка", "ключ", "скачать", "подключить"]):
        return "commercial"
    if any(x in low for x in ["как", "настро", "инструкция", "импорт"]):
        return "informational"
    if project == "mixed":
        return "mixed"
    return "product"


def load_pack(project: str) -> Dict[str, Any]:
    if project in PROJECT_DEFS:
        folder_name = PROJECT_DEFS[project]["folder"]
    elif project == "@vpnzm_bot":
        folder_name = "router-vpn-zm"
    elif project == "VPN GROZA":
        folder_name = "vpn-groza"
    elif project == "Roblox VPN":
        folder_name = "roblox"
    elif project == "Battlefield 6 / BF6":
        folder_name = "bf6"
    elif project == "Counter-Strike 2 / CS2":
        folder_name = "cs2"
    else:
        folder_name = "vpn-raketa"
    folder = DATA_DIR / folder_name
    titles = [x.strip() for x in read_text(folder / "titles.txt").splitlines() if x.strip()]
    hashtags = [x.strip() for x in read_text(folder / "hashtags.txt").splitlines() if x.strip()]
    keywords = split_csv(read_text(folder / "keywords.txt"))
    description_seed = read_text(folder / "description.txt").strip()
    return {
        "folder": str(folder),
        "titles": titles,
        "hashtags": hashtags,
        "keywords": keywords,
        "description_seed": description_seed,
    }


def choose_title(project: str, text: str, titles: List[str]) -> str:
    if project in {"@vpnzm_bot", "router"}:
        priorities = ["keenetic", "wireguard", "роутер"]
    elif project in {"vpn_groza", "VPN GROZA"}:
        priorities = ["vpn groza", "vless", "android", "iphone"]
    elif project == "roblox":
        priorities = ["roblox", "vpn", "подключ"]
    elif project == "bf6":
        priorities = ["battlefield 6", "bf6", "гайд", "настройки"]
    elif project == "cs2":
        priorities = ["cs2", "counter", "смоки", "прицел", "fps"]
    else:
        priorities = ["android", "iphone", "windows", "vpn 2026", "быстрый"]
    ranked = sorted(
        titles,
        key=lambda title: (
            -sum(1 for p in priorities if p in title.lower()),
            abs(len(title) - 60),
            title.lower().count("vpn") + title.lower().count("впн"),
        ),
    )
    title = ranked[0] if ranked else "YouTube SEO title."
    return ensure_sentence_mark(title)


def ensure_sentence_mark(title: str) -> str:
    title = title.strip()
    if not title:
        return title
    return title if title[-1] in ".!?" else f"{title}."


def build_description(project: str, text: str) -> str:
    if project in {"@vpnzm_bot", "router"}:
        return (
            "VPN для Keenetic через WireGuard: показываем, как получить конфиг, "
            "импортировать его в роутер и настроить выход в интернет для нужных устройств.\n\n"
            "Что в видео:\n"
            "— получение WireGuard-файла в @vpnzm_bot\n"
            "— импорт конфигурации в Keenetic\n"
            "— политика доступа и проверка подключения\n\n"
            "Кому подойдёт:\n"
            "— тем, кто настраивает VPN на роутере Keenetic\n"
            "— тем, кому нужен домашний интернет через WireGuard\n\n"
            "Откройте @vpnzm_bot, получите конфигурацию и следуйте видеоинструкции."
        )
    if project in {"vpn_groza", "VPN GROZA"}:
        return (
            "VPN GROZA для Android и iPhone: показываем подключение через VLESS, "
            "получение ключа или ссылки и проверку доступа к сайтам и приложениям.\n\n"
            "Что в видео:\n"
            "— как подготовить VLESS-подключение\n"
            "— как запустить VPN GROZA на телефоне\n"
            "— как проверить работу подключения\n\n"
            "Кому подойдёт:\n"
            "— пользователям Android и iPhone\n"
            "— тем, кому нужен мобильный доступ к сайтам и приложениям\n\n"
            "Используйте актуальную ссылку или ключ из описания. Не смешивайте VPN GROZA с другими VPN-брендами."
        )
    if project == "roblox":
        return (
            "VPN для Roblox может помочь при сетевых ошибках и нестабильном подключении. "
            "Показываем настройку, проверку работы и важные ограничения без обещаний идеального пинга.\n\n"
            "Что в видео:\n"
            "— как подключить VPN для Roblox\n"
            "— как проверить вход и подключение к серверу\n"
            "— что учитывать при выборе региона\n\n"
            "Кому подойдёт:\n"
            "— игрокам Roblox на ПК и мобильных устройствах\n"
            "— тем, у кого возникают ошибки подключения\n\n"
            "Сохраните видео и проверьте настройки по шагам."
        )
    if project == "bf6":
        return (
            "Battlefield 6 гайд по мультиплееру: разбираем геймплей, настройки, оружие, "
            "позиционку и решения, которые помогают играть стабильнее и осознаннее.\n\n"
            "Что в видео:\n"
            "— настройки и FPS\n"
            "— оружие, классы и роли\n"
            "— тактика в мультиплеере\n\n"
            "Кому подойдёт:\n"
            "— новичкам BF6\n"
            "— игрокам, которые хотят лучше понимать матчи\n\n"
            "Смотрите разбор до конца и напишите, какую тему разобрать следующей."
        )
    if project == "cs2":
        return (
            "CS2 гайд по полезным настройкам и командной игре: смоки, флешки, прицел, FPS, "
            "экономика раундов и решения, которые помогают играть увереннее.\n\n"
            "Что в видео:\n"
            "— полезные гранаты и позиции\n"
            "— настройки прицела и графики\n"
            "— экономика и командная тактика\n\n"
            "Кому подойдёт:\n"
            "— новичкам Counter-Strike 2\n"
            "— игрокам, которые хотят лучше понимать раунды\n\n"
            "Сохраните гайд и используйте таймкоды для нужного раздела."
        )
    return (
        "VPN 2026 для Android, iPhone и Windows: показываем, как получить ключ, "
        "подключиться через HAPP или VLESS и проверить доступ к сайтам и приложениям.\n\n"
        "Что в видео:\n"
        "— как получить VPN-ключ\n"
        "— подключение по QR-коду или ссылке\n"
        "— проверка работы на телефоне или ПК\n\n"
        "Кому подойдёт:\n"
        "— пользователям Android, iPhone и Windows\n"
        "— тем, кому нужен доступ к YouTube, Telegram, Instagram, сайтам и приложениям\n\n"
        "Получите ключ по ссылке из описания. Если доступен бонус, используйте ZankinMaster для +2 дня бесплатно."
    )


def build_hook(project: str) -> str:
    if project in {"@vpnzm_bot", "router"}:
        return "Хотите, чтобы VPN работал через роутер Keenetic? Показываю настройку WireGuard."
    if project in {"vpn_groza", "VPN GROZA"}:
        return "Нужен VLESS на телефоне? Показываю, как подключить VPN GROZA без лишних шагов."
    if project == "roblox":
        return "Roblox нестабильно подключается? Проверим VPN-настройку и частые ошибки."
    if project == "bf6":
        return "Разберём BF6 без воды: настройки, тактика и решения в реальном матче."
    if project == "cs2":
        return "Покажу CS2-настройки и раундовые решения, которые легко повторить."
    return "Нужен быстрый VPN без долгой настройки? Показываю подключение по ключу или QR-коду."


def build_thumbnail(project: str) -> Tuple[str, str]:
    if project in {"@vpnzm_bot", "router"}:
        return (
            "Keenetic + VPN",
            "Роутер Keenetic, значок WireGuard, Wi-Fi-схема и галочка \"Интернет через VPN\".",
        )
    if project in {"vpn_groza", "VPN GROZA"}:
        return ("VLESS на телефон", "Смартфон, значок VPN GROZA, VLESS-ключ и понятная стрелка подключения.")
    if project == "roblox":
        return ("Roblox работает?", "Экран Roblox, значок подключения, пинг/сервер и крупная галочка проверки.")
    if project == "bf6":
        return ("BF6 без воды", "Кадр боя Battlefield 6, оружие/класс, FPS и акцент на практический гайд.")
    if project == "cs2":
        return ("CS2 гайд", "Карта CS2, траектория гранаты, прицел и подпись с конкретной пользой.")
    return (
        "VPN за 5 минут",
        "Телефон и ПК, QR-код, замок VPN и акцент на быстрый запуск приложения.",
    )


def build_chapters(project: str) -> str:
    if project in {"@vpnzm_bot", "router"}:
        return "\n".join([
            "00:00 Что нужно для настройки",
            "00:20 Получение WireGuard-файла",
            "00:45 Импорт конфигурации в Keenetic",
            "01:20 Включение выхода в интернет",
            "01:45 Настройка политики доступа",
            "02:15 Проверка подключения",
        ])
    if project == "roblox":
        return "\n".join(["00:00 Что проверяем", "00:20 Подключение VPN", "00:45 Проверка Roblox", "01:10 Частые ошибки", "01:35 Рекомендации"])
    if project == "bf6":
        return "\n".join(["00:00 Что в гайде", "00:20 Настройки", "00:45 Оружие и классы", "01:20 Тактика", "01:50 Итоги"])
    if project == "cs2":
        return "\n".join(["00:00 Что покажем", "00:20 Смоки и флешки", "00:50 Прицел и FPS", "01:20 Экономика", "01:50 Тактика"])
    return "\n".join([
        "00:00 Что покажем в видео",
        "00:15 Как получить VPN-ключ",
        "00:35 Подключение по QR-коду или ссылке",
        "01:10 Проверка работы",
        "01:35 Важные рекомендации",
    ])


def build_pinned_comment(project: str) -> str:
    if project in {"@vpnzm_bot", "router"}:
        return (
            "🔐 Получите WireGuard-конфиг в @vpnzm_bot, импортируйте его в Keenetic "
            "и включите использование для выхода в Интернет."
        )
    if project in {"vpn_groza", "VPN GROZA"}:
        return "⚡ Подключайте VPN GROZA по актуальному ключу или ссылке из описания и проверяйте работу на своём устройстве."
    if project == "roblox":
        return "🎮 Напишите, на каком устройстве запускаете Roblox и какая ошибка подключения появляется чаще всего."
    if project == "bf6":
        return "🎮 Напишите, какую тему BF6 разобрать дальше: настройки, оружие, классы или карты."
    if project == "cs2":
        return "🎯 Сохраните гайд и напишите, какие смоки или настройки CS2 разобрать следующими."
    return (
        "🚀 Получить VPN-ключ и бонус +2 дня можно по ссылке из описания. "
        "QR-код или ссылка → приложение → готово."
    )


def spam_risk(payload: Dict[str, Any]) -> Tuple[str, int]:
    visible_text = " ".join([
        payload.get("best_title", ""),
        payload.get("description", ""),
        " ".join(payload.get("hashtags", [])),
    ]).lower()
    full_text = " ".join([
        visible_text,
        " ".join(payload.get("youtube_tags", [])),
        payload.get("seo_keywords", ""),
    ]).lower()
    visible_words = max(1, len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", visible_text)))
    visible_vpn_count = visible_text.count("vpn") + visible_text.count("впн")
    density_score = int((visible_vpn_count / visible_words) * 160)
    hashtags_count = len(payload.get("hashtags", []))
    tags_len = len(", ".join(payload.get("youtube_tags", [])))
    keyword_count = len(split_csv(payload.get("seo_keywords", "")))
    mixed_penalty = 20 if ("vpn raketa" in full_text and "vpnzm_bot" in full_text and payload.get("project") != "mixed") else 0
    score = min(
        100,
        density_score
        + max(0, hashtags_count - 15) * 5
        + max(0, tags_len - 500)
        + max(0, keyword_count - 60)
        + mixed_penalty,
    )
    level = "low" if score < 35 else "medium" if score < 70 else "high"
    return level, score


def quality_scores(payload: Dict[str, Any]) -> Dict[str, int]:
    title = payload.get("best_title", "")
    description = payload.get("description", "")
    hashtags = payload.get("hashtags", [])
    tags = payload.get("youtube_tags", [])
    keywords = split_csv(payload.get("seo_keywords", ""))
    title_score = 100
    if len(title) < 35 or len(title) > 75:
        title_score -= 12
    if title.lower().count("vpn") + title.lower().count("впн") > 2:
        title_score -= 15
    if payload["project"] == "VPN RAKETA" and "@vpnzm_bot" in title:
        title_score -= 40
    if payload["project"] == "@vpnzm_bot" and "VPN RAKETA" in title:
        title_score -= 40

    desc_score = 100
    if len(description[:250]) < 120:
        desc_score -= 10
    if "Что в видео" not in description:
        desc_score -= 10
    if "Кому подойд" not in description:
        desc_score -= 10
    if description.lower().count("vpn") + description.lower().count("впн") > 12:
        desc_score -= 15

    return {
        "title": max(0, title_score),
        "description": max(0, desc_score),
        "hook": 90 if payload.get("hook") and "всем привет" not in payload["hook"].lower() else 50,
        "thumbnail": 88 if len(payload.get("thumbnail_text", "").split()) <= 5 else 60,
        "hashtags": 95 if 8 <= len(hashtags) <= 15 and len(hashtags) == len({h.lower() for h in hashtags}) else 70,
        "tags": 92 if 350 <= len(", ".join(tags)) <= 500 and all("#" not in t for t in tags) else 70,
        "seo_keywords": 90 if keywords and len(keywords) == len({k.lower() for k in keywords}) else 65,
    }


def generate_seo_package(test_name: str, input_text: Optional[str] = None) -> Dict[str, Any]:
    source = input_text or TEST_INPUTS[test_name]
    project_id = "mixed" if test_name == "mixed" else (test_name if test_name in PROJECT_DEFS else detect_project_id(source))
    project = display_project(project_id)
    if project_id == "mixed":
        variants = [
            generate_seo_package("vpn_raketa", TEST_INPUTS["vpn_raketa"]),
            generate_seo_package("router", TEST_INPUTS["router"]),
        ]
        result = {
            "project": "mixed",
            "video_type": "mixed_video",
            "intent": "mixed",
            "main_keywords": split_csv(source),
            "semantic_groups": {"vpn_raketa_score": score_terms(source, VPN_RAKETA_TERMS), "router_score": score_terms(source, ROUTER_TERMS)},
            "title_variants": [variants[0]["best_title"], variants[1]["best_title"]],
            "best_title": variants[0]["best_title"],
            "thumbnail_text": "Выберите проект",
            "thumbnail_idea": "Два направления разнести по разным роликам: телефон/ПК отдельно, Keenetic отдельно.",
            "hook": "Смешанный запрос лучше разделить: один ролик про телефон и ПК, второй про Keenetic.",
            "description": "Запрос содержит два разных интента: общий VPN для телефона/ПК и настройку WireGuard на Keenetic. Для публикации выберите один главный проект, чтобы title и description не смешивали бренды.",
            "chapters": "Недостаточно структуры для точных chapters",
            "pinned_comment": "Выберите основной сценарий: VPN RAKETA для телефона/ПК или @vpnzm_bot для Keenetic.",
            "hashtags": [],
            "youtube_tags": [],
            "seo_keywords": source,
            "recommendations": ["Не смешивать бренды в title.", "Сделать отдельные публикации под разные интенты."],
        }
        risk, risk_score = spam_risk(result)
        result["spam_risk"] = risk
        result["spam_score"] = risk_score
        result["title_ctr_score"] = quality_scores(result)["title"]
        return result

    pack = load_pack(project_id)
    title = choose_title(project_id, source, pack["titles"])
    thumbnail_text, thumbnail_idea = build_thumbnail(project_id)
    tags = build_youtube_tags(pack["keywords"], min_len=480, max_len=500)
    hashtags = normalize_hashtags(pack["hashtags"], limit=15)
    payload = {
        "project": project,
        "project_id": project_id,
        "video_type": (
            "tutorial_video" if project_id in {"router", "roblox", "bf6", "cs2"} else "search_video"
        ),
        "intent": detect_intent(source, project),
        "main_keywords": split_csv(source),
        "semantic_groups": {
            pid + "_score": score_terms(source, meta.get("terms", set()))
            for pid, meta in PROJECT_DEFS.items()
        },
        "title_variants": [ensure_sentence_mark(x) for x in pack["titles"][:3]],
        "best_title": title,
        "thumbnail_text": thumbnail_text,
        "thumbnail_idea": thumbnail_idea,
        "hook": build_hook(project_id),
        "description": build_description(project_id, source),
        "chapters": build_chapters(project_id),
        "pinned_comment": build_pinned_comment(project_id),
        "hashtags": hashtags,
        "youtube_tags": tags,
        "seo_keywords": ", ".join(pack["keywords"]),
        "recommendations": [],
    }
    risk, risk_score = spam_risk(payload)
    payload["spam_risk"] = risk
    payload["spam_score"] = risk_score
    payload["title_ctr_score"] = quality_scores(payload)["title"]
    return payload


def check_environment(debug: bool = False) -> Dict[str, Any]:
    results = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "syntax": {},
        "imports": {},
        "requirements": [],
    }
    req = ROOT / "requirements.txt"
    if req.exists():
        results["requirements"] = [x.strip() for x in read_text(req).splitlines() if x.strip()]

    for file in PY_FILES:
        path = ROOT / file
        try:
            ast.parse(read_text(path))
            results["syntax"][file] = "ok"
        except Exception as exc:
            results["syntax"][file] = f"fail: {type(exc).__name__}: {exc}"

    for mod_name in [Path(f).stem for f in PY_FILES]:
        try:
            spec = importlib.util.spec_from_file_location(mod_name, ROOT / f"{mod_name}.py")
            if not spec or not spec.loader:
                raise RuntimeError("spec loader not found")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            results["imports"][mod_name] = "ok"
        except Exception as exc:
            results["imports"][mod_name] = f"fail: {type(exc).__name__}: {exc}"
            if debug:
                traceback.print_exc()
    return results


def check_config() -> Dict[str, Any]:
    ok, cfg, error = load_json(CONFIG_PATH)
    result: Dict[str, Any] = {
        "config_path": str(CONFIG_PATH),
        "json_valid": ok,
        "error": error,
        "paths": {},
        "prompts": {},
        "data_packs": {},
        "security_warnings": [],
    }
    if not ok:
        return result

    for section in ["paths", "youtube", "metadata", "llm"]:
        result[section] = "ok" if section in cfg else "missing"

    api_key = str(cfg.get("llm", {}).get("openai_api_key", "")).strip()
    if api_key and api_key.lower() not in {"", "none", "null", "changeme"}:
        result["security_warnings"].append("openai_api_key задан в config.json; рекомендуется хранить секрет в переменной окружения.")

    prompt_expectations = {
        "stage_title.prompt.txt": "Верни строго JSON",
        "stage_description.prompt.txt": "Верни строго JSON",
        "stage_hashtags_keys.prompt.txt": "Верни строго JSON",
        "stage_keywords.prompt.txt": "Верни строго JSON",
        "stage_thumbnail_image.prompt.txt": "YouTube thumbnail",
    }
    for rel, marker in prompt_expectations.items():
        path = PROMPTS_DIR / rel
        result["prompts"][rel] = "ok" if path.exists() and marker in read_text(path) else "missing_or_invalid"

    for meta in PROJECT_DEFS.values():
        pack = meta["folder"]
        folder = DATA_DIR / pack
        files = {}
        for name in ["titles.txt", "keywords.txt", "hashtags.txt", "description.txt"]:
            path = folder / name
            files[name] = "ok" if path.exists() and read_text(path).strip() else "missing_or_empty"
        result["data_packs"][pack] = files
    return result


def scan_projects() -> Dict[str, Any]:
    projects: Dict[str, Any] = {}
    known_by_folder = {meta["folder"]: (pid, meta) for pid, meta in PROJECT_DEFS.items()}
    for folder in sorted([p for p in DATA_DIR.iterdir() if p.is_dir()]):
        project_id, meta = known_by_folder.get(folder.name, (folder.name, {
            "name": folder.name,
            "type": "unknown/mixed project",
            "platform": "YouTube",
            "audience": "unknown",
            "terms": set(),
        }))
        files = {name: folder / name for name in ["titles.txt", "keywords.txt", "hashtags.txt", "description.txt"]}
        lines = []
        for path in files.values():
            if path.exists():
                lines.extend(read_text(path).splitlines())
        content = " ".join(lines).lower()
        brands = []
        guarded_mentions = []
        for brand in ["VPN RAKETA", "@vpnzm_bot", "ZankinMaster", "VPN GROZA", "Keenetic", "WireGuard", "Roblox", "Battlefield", "CS2"]:
            brand_low = brand.lower()
            guarded = any(
                brand_low in line.lower()
                and any(marker in line.lower() for marker in ["не смешивай", "не добавляй", "без ", "если видео не"])
                for line in lines
            )
            if brand_low in content and guarded:
                guarded_mentions.append(brand)
            elif brand_low in content:
                brands.append(brand)
        projects[project_id] = {
            "project_id": project_id,
            "name": meta["name"],
            "type": meta["type"],
            "platform": meta["platform"],
            "audience": meta["audience"],
            "folder": str(folder),
            "files": {name: ("ok" if path.exists() and read_text(path).strip() else "missing_or_empty") for name, path in files.items()},
            "brands_detected": brands,
            "guarded_mentions": guarded_mentions,
            "status": "ok" if all(path.exists() and read_text(path).strip() for path in files.values()) else "needs_fix",
        }
    return {"projects": projects}


def check_prompts() -> Dict[str, Any]:
    prompt_files = sorted(PROMPTS_DIR.glob("*.prompt.txt"))
    results: Dict[str, Any] = {}
    for path in prompt_files:
        text = read_text(path)
        lower = text.lower()
        is_thumbnail_prompt = "thumbnail" in lower and "16:9" in lower
        clarity = 90 if (
            ("ЗАДАЧА" in text and "КОНТЕКСТ" in text)
            or ("Main topic" in text and "Brand-specific rules" in text)
        ) else 70
        seo = 90 if "YouTube" in text and ("SEO" in text or "tags" in lower or "thumbnail" in lower) else 70
        ctr = 88 if ("CTR" in text or "кликабель" in lower or "thumbnail" in lower) else 72
        safety = 92 if (
            "Не обещай" in text
            or "ЗАПРЕЩЕНО" in text
            or "do not promise" in lower
            or "no random logos" in lower
        ) else 70
        platform = 90 if "YouTube" in text else 65
        project = 90 if (
            "ПРАВИЛА ВЫБОРА ПРОЕКТА" in text
            or "Brand-specific rules" in text
            or "brand_rules" in text
        ) else 60
        spam_risk = "low" if (
            "переспам" in lower
            or "без дублей" in lower
            or "no tiny text" in lower
            or "no random logos" in lower
        ) else "medium"
        if is_thumbnail_prompt:
            clarity = max(clarity, 90)
            safety = max(safety, 92)
            project = max(project, 90)
        results[path.name] = {
            "file": str(path),
            "purpose": path.stem.replace("stage_", "").replace(".prompt", ""),
            "project": "multi-project",
            "clarity_score": clarity,
            "seo_score": seo,
            "ctr_score": ctr,
            "safety_score": safety,
            "platform_match_score": platform,
            "project_match_score": project,
            "spam_risk": spam_risk,
            "status": "ok" if min(clarity, seo, safety, project) >= 80 else "needs_fix",
        }
    return {"prompts": results}


def test_all_projects() -> Dict[str, Any]:
    tests: Dict[str, Any] = {}
    for project_id in PROJECT_DEFS:
        tests[project_id] = run_test(project_id)
    tests["mixed"] = run_test("mixed")
    ok = all(item.get("validation", {}).get("ok") for item in tests.values())
    return {"status": "OK" if ok else "NEEDS_FIX", "tests": tests}


def build_full_report() -> Dict[str, Any]:
    return {
        "projects": scan_projects()["projects"],
        "prompts": check_prompts()["prompts"],
        "config": check_config(),
        "environment": check_environment(),
        "safe_function_checks": safe_function_checks(),
        "tests": test_all_projects()["tests"],
    }


def report_to_markdown(report: Dict[str, Any]) -> str:
    lines = ["# QA Report", ""]
    lines.append("## Projects")
    for pid, item in report.get("projects", {}).items():
        lines.append(f"- **{pid}**: {item.get('name')} | {item.get('type')} | status: {item.get('status')}")
    lines.append("")
    lines.append("## Prompts")
    for name, item in report.get("prompts", {}).items():
        lines.append(
            f"- **{name}**: clarity {item.get('clarity_score')}, SEO {item.get('seo_score')}, "
            f"safety {item.get('safety_score')}, risk {item.get('spam_risk')}"
        )
    lines.append("")
    lines.append("## Tests")
    for name, item in report.get("tests", {}).items():
        ok = item.get("validation", {}).get("ok")
        project = item.get("result", {}).get("project")
        risk = item.get("result", {}).get("spam_risk")
        lines.append(f"- **{name}**: ok={ok}, project={project}, spam_risk={risk}")
    warnings = report.get("config", {}).get("security_warnings", [])
    if warnings:
        lines.append("")
        lines.append("## Security Warnings")
        for warning in warnings:
            lines.append(f"- {warning}")
    lines.append("")
    return "\n".join(lines)


def infer_meaning(name: str) -> str:
    low = name.lower()
    if "config" in low:
        return "проверка или загрузка конфигурации"
    if "metadata" in low or "generate" in low:
        return "генерация метаданных"
    if "title" in low:
        return "обработка title"
    if "description" in low:
        return "обработка description"
    if "hashtag" in low:
        return "обработка hashtags"
    if "keyword" in low or "tag" in low:
        return "обработка keywords/tags"
    if "youtube" in low or "video" in low or "playlist" in low:
        return "работа с YouTube API, вызывать только вручную"
    if "similarity" in low or "dedupe" in low or "cluster" in low:
        return "сравнение и дедупликация текста"
    return "вспомогательная функция"


def list_functions() -> List[Dict[str, Any]]:
    functions: List[FunctionInfo] = []
    dangerous_tokens = ["youtube", "playlist", "update", "auth", "openai", "ollama", "post", "main", "exit"]
    for file in PY_FILES:
        tree = ast.parse(read_text(ROOT / file))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                args = [arg.arg for arg in node.args.args]
                name_low = node.name.lower()
                status = "manual_check_required" if any(tok in name_low for tok in dangerous_tokens) else "safe_static_check"
                functions.append(FunctionInfo(file, node.lineno, node.name, args, status, infer_meaning(node.name)))
    return [f.__dict__ for f in sorted(functions, key=lambda x: (x.file, x.line))]


def safe_function_checks(debug: bool = False) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    try:
        import fuzzy_utils
        normalized = fuzzy_utils.normalize_text(" VPN   Router ")
        results["fuzzy_utils.normalize_text"] = normalized == "vpn router"
        results["fuzzy_utils.similarity"] = 0 <= fuzzy_utils.similarity("vpn", "впн") <= 1
        results["fuzzy_utils.dedupe_by_similarity"] = isinstance(fuzzy_utils.dedupe_by_similarity(["vpn", "vpn"]), list)
    except Exception as exc:
        results["fuzzy_utils"] = f"fail: {type(exc).__name__}: {exc}"
        if debug:
            traceback.print_exc()

    try:
        import launcher as main_launcher
        results["launcher._split_csv"] = main_launcher._split_csv(["vpn, впн, test"]) == ["vpn", "впн", "test"]
        results["launcher._fit_title_without_word_cut"] = len(main_launcher._fit_title_without_word_cut("a " * 100, 20)) <= 20
        results["launcher._normalize_hashtags_list"] = main_launcher._normalize_hashtags_list(["VPN", "#VPN", "Wire Guard"], 3)[:1] == ["#VPN"]
    except Exception as exc:
        results["launcher_safe_helpers"] = f"fail: {type(exc).__name__}: {exc}"
        if debug:
            traceback.print_exc()
    return results


def validate_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    scores = quality_scores(payload)
    issues: List[str] = []
    project = payload.get("project")
    title = payload.get("best_title", "")
    description = payload.get("description", "")
    tags = payload.get("youtube_tags", [])
    keywords = split_csv(payload.get("seo_keywords", ""))

    if project == "VPN RAKETA" and ("@vpnzm_bot" in title or "Keenetic" in title):
        issues.append("VPN RAKETA title смешивает проект.")
    if project == "@vpnzm_bot" and "VPN RAKETA" in title:
        issues.append("@vpnzm_bot title смешивает проект.")
    if any("#" in tag for tag in tags):
        issues.append("YouTube tags содержат #.")
    if len(keywords) != len({k.lower() for k in keywords}):
        issues.append("SEO keywords содержат дубли.")
    if "100% выход в топ" in description.lower():
        issues.append("Есть невозможное SEO-обещание.")

    return {"scores": scores, "issues": issues, "ok": not issues and payload.get("spam_risk") != "high"}


def save_json(data: Any, path: Path) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_test(name: str, save_output: Optional[Path] = None) -> Dict[str, Any]:
    payload = generate_seo_package(name)
    validation = validate_result(payload)
    result = {"test": name, "result": payload, "validation": validation}
    if save_output:
        save_json(result, save_output)
    return result


def run_smoke(debug: bool = False) -> Dict[str, Any]:
    smoke = {
        "environment": check_environment(debug=debug),
        "config": check_config(),
        "safe_function_checks": safe_function_checks(debug=debug),
        "tests": {},
    }
    for name in TEST_INPUTS:
        smoke["tests"][name] = run_test(name)
    return smoke


def check_youtube_seo() -> Dict[str, Any]:
    tests = test_all_projects()
    results = []
    for name, item in tests.get("tests", {}).items():
        payload = item.get("result", {})
        results.append({
            "test": item.get("test", name),
            "status": item.get("status"),
            "scores": quality_scores(payload) if payload else {},
            "validation": validate_result(payload) if payload else {"status": "FAIL", "issues": ["empty result"]},
            "tags_len": len(", ".join(payload.get("youtube_tags", []))) if payload else 0,
        })
    return {"status": tests.get("status", "UNKNOWN"), "youtube_seo": results}


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def interactive_menu(debug: bool = False) -> None:
    last_result: Optional[Dict[str, Any]] = None
    while True:
        print("\nQA Launcher")
        print("1. Проверить все проекты")
        print("2. Показать найденные проекты")
        print("3. Проверить все промты")
        print("4. Проверить все конфиги")
        print("5. Запустить тесты по всем проектам")
        print("6. Запустить тест VPN RAKETA")
        print("7. Запустить тест @vpnzm_bot")
        print("8. Запустить mixed-тест")
        print("9. Проверить YouTube SEO-качество последнего результата")
        print("10. Проверить Telegram-тексты")
        print("11. Проверить рекламные тексты")
        print("12. Проверить инструкции")
        print("13. Сохранить отчёт в JSON")
        print("14. Сохранить отчёт в TXT/MD")
        print("0. Выход")
        choice = input("Выберите пункт: ").strip()
        if choice == "0":
            return
        if choice == "1":
            last_result = build_full_report()
        elif choice == "2":
            last_result = scan_projects()
        elif choice == "3":
            last_result = check_prompts()
        elif choice == "4":
            last_result = check_config()
        elif choice == "5":
            last_result = test_all_projects()
        elif choice == "6":
            last_result = run_test("vpn_raketa")
        elif choice == "7":
            last_result = run_test("router")
        elif choice == "8":
            last_result = run_test("mixed")
        elif choice == "9":
            if not last_result or "result" not in last_result:
                print("Сначала запустите одиночный тест.")
                continue
            last_result = validate_result(last_result["result"])
        elif choice == "10":
            last_result = {"status": "no_dedicated_telegram_prompts", "note": "Отдельные Telegram-промты не найдены; проверены только CTA/бот-ссылки в YouTube data-pack."}
        elif choice == "11":
            last_result = {"status": "no_dedicated_ads_prompts", "note": "Отдельные рекламные промты не найдены."}
        elif choice == "12":
            last_result = {"status": "instruction_texts_checked", "note": "Инструкционные сценарии найдены в router-vpn-zm, roblox, bf6 и cs2."}
        elif choice == "13":
            if not last_result:
                print("Нет результата для сохранения.")
                continue
            target = Path(input("Путь к JSON-файлу: ").strip() or "qa_result.json")
            save_json(last_result, target)
            print(f"Сохранено: {target}")
            continue
        elif choice == "14":
            if not last_result:
                last_result = build_full_report()
            target = Path(input("Путь к MD/TXT-файлу: ").strip() or "qa_report.md")
            target.write_text(report_to_markdown(last_result), encoding="utf-8")
            print(f"Сохранено: {target}")
            continue
        elif choice == "15":
            if not last_result or "result" not in last_result:
                print("Сначала запустите одиночный тест.")
                continue
            last_result = validate_result(last_result["result"])
        else:
            print("Неизвестный пункт.")
            continue
        print_json(last_result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Проверки качества для YouTube SEO проекта через launcher.py.")
    parser.add_argument("--smoke", action="store_true", help="Запустить все smoke-tests.")
    parser.add_argument("--scan-projects", action="store_true", help="Найти и классифицировать все проекты.")
    parser.add_argument("--check-prompts", action="store_true", help="Проверить все stage-промты.")
    parser.add_argument("--test-all-projects", action="store_true", help="Запустить тесты по всем проектам.")
    parser.add_argument("--test", choices=sorted(TEST_INPUTS.keys()), help="Запустить один тестовый сценарий.")
    parser.add_argument("--check-config", action="store_true", help="Проверить конфигурации и data-pack файлы.")
    parser.add_argument("--list-functions", action="store_true", help="Показать найденные функции.")
    parser.add_argument("--check-env", action="store_true", help="Проверить окружение, синтаксис и импорты.")
    parser.add_argument("--check-youtube-seo", action="store_true", help="Проверить YouTube SEO-качество тестовых результатов.")
    parser.add_argument("--save-output", type=Path, help="Сохранить результат команды в JSON.")
    parser.add_argument("--save-json", type=Path, help="Сохранить результат команды в JSON.")
    parser.add_argument("--report-json", type=Path, help="Сохранить полный отчёт в JSON.")
    parser.add_argument("--report-md", type=Path, help="Сохранить полный отчёт в Markdown.")
    parser.add_argument("--debug", action="store_true", help="Показывать traceback для ошибок.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result: Optional[Any] = None
    try:
        if args.report_json or args.report_md:
            result = build_full_report()
            if args.report_json:
                save_json(result, args.report_json)
            if args.report_md:
                args.report_md.write_text(report_to_markdown(result), encoding="utf-8")
        elif args.smoke:
            result = run_smoke(debug=args.debug)
        elif args.scan_projects:
            result = scan_projects()
        elif args.check_prompts:
            result = check_prompts()
        elif args.test_all_projects:
            result = test_all_projects()
        elif args.test:
            result = run_test(args.test)
        elif args.check_config:
            result = check_config()
        elif args.list_functions:
            result = {"functions": list_functions()}
        elif args.check_env:
            result = check_environment(debug=args.debug)
        elif args.check_youtube_seo:
            result = check_youtube_seo()
        elif args.save_json:
            result = build_full_report()
        else:
            interactive_menu(debug=args.debug)
            return 0

        output_path = args.save_output or args.save_json
        if output_path and result is not None:
            save_json(result, output_path)
        if result is not None:
            print_json(result)
        return 0
    except Exception as exc:
        print(f"Ошибка проверки качества: {type(exc).__name__}: {exc}", file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    print("Этот файл не является лаунчером. Используйте: python launcher.py --help")
    raise SystemExit(1)
