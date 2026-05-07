import json
import logging
import random
import re
import sys
import ast
import base64
import os
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path
from time import perf_counter
from time import sleep
from typing import Dict, List, Tuple, Optional, Any, Set
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlsplit, urlunsplit
from googleapiclient.errors import HttpError
from generate_metadata import load_config, authenticate_youtube

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

try:
    import emoji
except Exception:
    emoji = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ACTIVE_HISTORY_RUN: Optional["HistoryRun"] = None
YOUTUBE_HARD_TITLE_LIMIT = 100
YOUTUBE_KEYWORDS_MIN_LEN = 300
YOUTUBE_KEYWORDS_MAX_LEN = 500
DEFAULT_TARGET_DESCRIPTION_MIN = 1700
DEFAULT_TARGET_KEYWORDS_MIN = 490
ROUTER_PROFILE_TARGET_DESCRIPTION_MIN = 1400
ROUTER_PROFILE_TARGET_KEYWORDS_MIN = 495
ROUTER_PROFILE_TERMS: List[str] = [
    "vpn для роутера keenetic",
    "впн для роутера keenetic",
    "vpn keenetic",
    "keenetic vpn",
    "впн keenetic",
    "vpn кинетик",
    "впн кинетик",
    "vpn для keenetic",
    "впн для keenetic",
    "vpn для кинетика",
    "впн для кинетика",
    "keenetic wireguard",
    "wireguard keenetic",
    "wireguard на keenetic",
    "wireguard на кинетик",
    "vpn wireguard keenetic",
    "wireguard vpn keenetic",
    "настройка wireguard keenetic",
    "keenetic wireguard настройка",
    "keenetic wg",
    "vpn keenetic wireguard",
    "wireguard роутер keenetic",
    "keenetic vpn client",
    "keenetic vpn server",
    "keenetic os vpn",
    "настройка vpn на keenetic",
    "как настроить vpn keenetic",
    "keenetic vpn настройка",
    "keenetic настройка vpn",
    "vpn клиент keenetic",
    "vpn сервер keenetic",
    "роутер keenetic vpn",
    "vpn роутер keenetic",
    "wireguard для роутера",
    "впн wireguard для роутера",
    "vpn для роутера wireguard",
    "впн для роутера wireguard",
]


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9А-Яа-я_\-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned.lower() or "run"


class HistoryRun:
    def __init__(self, base_dir: Path, action_name: str):
        self.started_at = datetime.now()
        self.action_name = action_name
        date_dir = self.started_at.strftime("%Y-%m-%d")
        run_dir = f"{self.started_at.strftime('%H-%M-%S')}_{_slugify(action_name)}"
        self.path = base_dir / date_dir / run_dir
        self.path.mkdir(parents=True, exist_ok=True)
        self.events: List[Dict[str, Any]] = []
        self.events_jsonl = self.path / "events.jsonl"
        self.summary_json = self.path / "summary.json"
        self.report_html = self.path / "report.html"
        self.video_reports_dir = self.path / "videos"
        self.video_reports_dir.mkdir(parents=True, exist_ok=True)
        self.video_drafts_dir = self.path / "drafts"
        self.video_drafts_dir.mkdir(parents=True, exist_ok=True)
        self.video_reports: List[Dict[str, str]] = []

    def log(self, event_type: str, message: str = "", data: Optional[Dict[str, Any]] = None) -> None:
        event = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "type": event_type,
            "message": message,
            "data": data or {},
        }
        self.events.append(event)
        with self.events_jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def finalize(self, status: str = "completed") -> None:
        finished_at = datetime.now()
        summary = {
            "action": self.action_name,
            "status": status,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "duration_sec": int((finished_at - self.started_at).total_seconds()),
            "events_count": len(self.events),
            "events_by_type": dict(Counter(evt["type"] for evt in self.events)),
        }
        self.summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.report_html.write_text(self._build_html(summary), encoding="utf-8")

    def save_video_report(
        self,
        video_id: str,
        source_title: str,
        source_description: str,
        generated_title: str,
        generated_description: str,
        generated_tags_csv: str,
        generated_keywords_csv: str,
    ) -> Path:
        file_path = self.video_reports_dir / f"{_slugify(video_id)}.html"
        html = self._build_video_html(
            video_id=video_id,
            source_title=source_title,
            source_description=source_description,
            generated_title=generated_title,
            generated_description=generated_description,
            generated_tags_csv=generated_tags_csv,
            generated_keywords_csv=generated_keywords_csv,
        )
        file_path.write_text(html, encoding="utf-8")
        rel_file = file_path.relative_to(self.path).as_posix()
        self.video_reports = [v for v in self.video_reports if v.get("video_id") != video_id]
        self.video_reports.append({"video_id": video_id, "file": rel_file})
        return file_path

    def save_video_draft(
        self,
        video_id: str,
        title: str,
        description: str,
        keywords_csv: str,
        hashtags: List[str],
        data_folder: str = "",
    ) -> Path:
        file_path = self.video_drafts_dir / f"{_slugify(video_id)}.json"
        payload = {
            "video_id": video_id,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "data_folder": data_folder,
            "title": title,
            "description": description,
            "keywords_csv": keywords_csv,
            "keywords_len": len(keywords_csv or ""),
            "hashtags": hashtags,
            "hashtags_count": len(hashtags or []),
        }
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return file_path

    def _build_html(self, summary: Dict[str, Any]) -> str:
        summary_rows = "\n".join(
            f"<tr><th>{escape(str(k))}</th><td>{escape(str(v))}</td></tr>" for k, v in summary.items()
        )
        event_rows = "\n".join(
            (
                "<tr>"
                f"<td>{escape(evt.get('ts', ''))}</td>"
                f"<td><span class='badge text-bg-secondary'>{escape(evt.get('type', ''))}</span></td>"
                f"<td>{escape(evt.get('message', ''))}</td>"
                f"<td><pre class='mb-0 small'><code>{escape(json.dumps(evt.get('data', {}), ensure_ascii=False, indent=2))}</code></pre></td>"
                "</tr>"
            )
            for evt in self.events
        ) or "<tr><td colspan='4' class='text-muted'>События отсутствуют</td></tr>"
        video_rows = "\n".join(
            (
                "<tr>"
                f"<td>{escape(v.get('video_id', ''))}</td>"
                f"<td><a href=\"{escape(v.get('file', ''))}\" target=\"_blank\" rel=\"noopener noreferrer\">{escape(v.get('file', ''))}</a></td>"
                "</tr>"
            )
            for v in self.video_reports
        ) or "<tr><td colspan='2' class='text-muted'>Нет отчётов по видео</td></tr>"
        return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>History Report - {escape(self.action_name)}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {{ background: linear-gradient(120deg, #f8fafc, #eef2ff); }}
    .card {{ box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08); border: 0; }}
    pre {{ white-space: pre-wrap; word-break: break-word; }}
  </style>
</head>
<body>
  <div class="container py-4">
    <div class="card mb-4">
      <div class="card-body">
        <h1 class="h4 mb-3">История запуска: {escape(self.action_name)}</h1>
        <p class="text-muted mb-0">Папка запуска: <code>{escape(str(self.path))}</code></p>
      </div>
    </div>
    <div class="row g-4">
      <div class="col-12 col-lg-4">
        <div class="card h-100">
          <div class="card-body">
            <h2 class="h6">Сводка</h2>
            <div class="table-responsive">
              <table class="table table-sm align-middle mb-0">
                <tbody>{summary_rows}</tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
      <div class="col-12 col-lg-8">
        <div class="card h-100">
          <div class="card-body">
            <h2 class="h6">События</h2>
            <div class="table-responsive">
              <table class="table table-striped table-hover align-middle">
                <thead><tr><th>Время</th><th>Тип</th><th>Сообщение</th><th>Данные</th></tr></thead>
                <tbody>{event_rows}</tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
      <div class="col-12">
        <div class="card">
          <div class="card-body">
            <h2 class="h6">Отчёты По Видео</h2>
            <div class="table-responsive">
              <table class="table table-sm table-hover align-middle">
                <thead><tr><th>Video ID</th><th>Файл</th></tr></thead>
                <tbody>{video_rows}</tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</body>
</html>"""

    def _build_video_html(
        self,
        video_id: str,
        source_title: str,
        source_description: str,
        generated_title: str,
        generated_description: str,
        generated_tags_csv: str,
        generated_keywords_csv: str,
    ) -> str:
        tags_items = [x.strip() for x in generated_tags_csv.split(",") if x.strip()]
        keywords_items = [x.strip() for x in generated_keywords_csv.split(",") if x.strip()]
        tags_table = "\n".join(f"<tr><td>{i+1}</td><td>{escape(v)}</td></tr>" for i, v in enumerate(tags_items))
        keywords_table = "\n".join(f"<tr><td>{i+1}</td><td>{escape(v)}</td></tr>" for i, v in enumerate(keywords_items))
        tags_table = tags_table or "<tr><td colspan='2' class='text-muted'>Нет</td></tr>"
        keywords_table = keywords_table or "<tr><td colspan='2' class='text-muted'>Нет</td></tr>"
        return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Video Report - {escape(video_id)}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {{ background: linear-gradient(120deg, #f8fafc, #eef2ff); }}
    .card {{ box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08); border: 0; }}
    pre {{ white-space: pre-wrap; word-break: break-word; }}
  </style>
</head>
<body>
  <div class="container py-4">
    <div class="card mb-4">
      <div class="card-body">
        <h1 class="h4 mb-1">Отчёт По Видео</h1>
        <div class="text-muted">Video ID: <code>{escape(video_id)}</code></div>
        <div class="text-muted">Сформировано: {escape(datetime.now().isoformat(timespec="seconds"))}</div>
      </div>
    </div>

    <div class="row g-4">
      <div class="col-12 col-lg-6">
        <div class="card h-100">
          <div class="card-body">
            <h2 class="h6">Исходные Данные</h2>
            <div class="mb-2"><strong>Title</strong></div>
            <pre class="p-2 bg-light rounded border"><code>{escape(source_title or "")}</code></pre>
            <div class="mb-2"><strong>Description</strong></div>
            <pre class="p-2 bg-light rounded border"><code>{escape(source_description or "")}</code></pre>
          </div>
        </div>
      </div>

      <div class="col-12 col-lg-6">
        <div class="card h-100">
          <div class="card-body">
            <h2 class="h6">Сгенерированные Данные</h2>
            <div class="mb-2"><strong>Title ({len(generated_title or "")} chars)</strong></div>
            <pre class="p-2 bg-light rounded border"><code>{escape(generated_title or "")}</code></pre>
            <div class="mb-2"><strong>Description ({len(generated_description or "")} chars)</strong></div>
            <pre class="p-2 bg-light rounded border"><code>{escape(generated_description or "")}</code></pre>
          </div>
        </div>
      </div>

      <div class="col-12 col-lg-6">
        <div class="card h-100">
          <div class="card-body">
            <h2 class="h6">Теги ({len(generated_tags_csv or "")} chars)</h2>
            <div class="table-responsive">
              <table class="table table-sm table-striped align-middle">
                <thead><tr><th>#</th><th>Tag</th></tr></thead>
                <tbody>{tags_table}</tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      <div class="col-12 col-lg-6">
        <div class="card h-100">
          <div class="card-body">
            <h2 class="h6">Keywords ({len(generated_keywords_csv or "")} chars)</h2>
            <div class="table-responsive">
              <table class="table table-sm table-striped align-middle">
                <thead><tr><th>#</th><th>Keyword</th></tr></thead>
                <tbody>{keywords_table}</tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</body>
</html>"""

MENU_OPTIONS = {
    1: "Проверить конфигурацию и файлы данных",
    2: "Обработать видео в плейлисте",
    3: "Очистить данные авторизации",
    4: "Протестировать генерацию метаданных",
    5: "Сгенерировать превью через OpenAI",
    6: "Проверить окружение и импорты",
    7: "Найти и показать все проекты",
    8: "Проверить все промты",
    9: "Проверить YouTube SEO-качество",
    10: "Запустить все smoke-tests",
    11: "Запустить тест VPN RAKETA",
    12: "Запустить тест @vpnzm_bot / Keenetic",
    13: "Запустить mixed-тест",
    14: "Показать найденные функции",
    15: "Выход"
}

DATA_FILES = {
    'titles_file': 'titles.txt',
    'keywords_file': 'keywords.txt',
    'hashtags_file': 'hashtags.txt',
    'description_file': 'description.txt',
}


def _history_event(event_type: str, message: str = "", **data: Any) -> None:
    if ACTIVE_HISTORY_RUN:
        ACTIVE_HISTORY_RUN.log(event_type, message=message, data=data)


def _llm_log(message: str) -> None:
    print(f"[LLM] {message}")
    _history_event("llm_console", message=message)


def _llm_progress(stage: str, current: int, total: int, suffix: str = "") -> None:
    total = max(1, total)
    current = max(0, min(current, total))
    width = 24
    filled = int(width * current / total)
    bar = "#" * filled + "-" * (width - filled)
    pct = int(100 * current / total)
    msg = f"[LLM][{stage}] [{bar}] {pct}% ({current}/{total})"
    if suffix:
        msg += f" {suffix}"
    print(msg)
    _history_event("llm_progress", stage=stage, current=current, total=total, percent=pct, suffix=suffix)


class LLMFatalError(RuntimeError):
    """Non-retryable LLM configuration/runtime error."""


class OllamaHTTPError(RuntimeError):
    def __init__(self, code: int, reason: str, details: str = "") -> None:
        self.code = code
        self.reason = reason or "Unknown"
        self.details = (details or "").strip()
        message = f"Ollama HTTP error {self.code}: {self.reason}"
        if self.details:
            message = f"{message} | {self.details}"
        super().__init__(message)


class OpenAIHTTPError(RuntimeError):
    def __init__(self, code: int, reason: str, details: str = "") -> None:
        self.code = code
        self.reason = reason or "Unknown"
        self.details = (details or "").strip()
        message = f"OpenAI HTTP error {self.code}: {self.reason}"
        if self.details:
            message = f"{message} | {self.details}"
        super().__init__(message)


def _start_history_run(action_name: str, config: Optional[Dict] = None) -> Optional[HistoryRun]:
    global ACTIVE_HISTORY_RUN
    try:
        base_dir = Path("history_reports")
        if config:
            base_dir = Path(config.get("paths", {}).get("history_folder", str(base_dir)))
        ACTIVE_HISTORY_RUN = HistoryRun(base_dir=base_dir, action_name=action_name)
        ACTIVE_HISTORY_RUN.log("run_start", f"Запуск действия '{action_name}'", {"cwd": str(Path.cwd())})
        print(f"🗂️ История запуска: {ACTIVE_HISTORY_RUN.path}")
        return ACTIVE_HISTORY_RUN
    except Exception as e:
        ACTIVE_HISTORY_RUN = None
        print(f"⚠️ Не удалось инициализировать историю: {e}")
        return None


def _finish_history_run(status: str = "completed") -> None:
    global ACTIVE_HISTORY_RUN
    if not ACTIVE_HISTORY_RUN:
        return
    try:
        ACTIVE_HISTORY_RUN.log("run_finish", f"Завершение запуска со статусом: {status}")
        ACTIVE_HISTORY_RUN.finalize(status=status)
        print(f"📄 HTML-отчёт: {ACTIVE_HISTORY_RUN.report_html}")
    finally:
        ACTIVE_HISTORY_RUN = None


def _read_lines(file_path: Path) -> List[str]:
    if not file_path.exists():
        logger.warning(f"Missing file: {file_path}")
        return []
    return [line.strip() for line in file_path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def _payload_from_line(line: str) -> str:
    parts = line.split('|')
    if len(parts) >= 2:
        return parts[-1].strip()
    return line.strip()


def _prepare_title_seed_for_model(seed: str, max_len: int = 180) -> str:
    src = (seed or "").strip()
    if not src:
        return ""
    # If the data row has technical separators, keep the semantic tail.
    if "|" in src:
        parts = [p.strip() for p in src.split("|") if p.strip()]
        src = parts[-1] if parts else src
    # Remove leading emoji/noise to keep prompt focus on semantic core.
    src = re.sub(r"^\s*(?:[\U0001F300-\U0001FAFF\u2600-\u27BF]\s*)+", "", src).strip()
    src = src.replace("\r", " ").replace("\n", " ")
    src = re.sub(r"https?://\S+", " ", src, flags=re.IGNORECASE)
    src = re.sub(r"\s{2,}", " ", src).strip(" -_.,;:|")
    if len(src) > max_len:
        src = _fit_title_without_word_cut(src, max_len=max_len)
    return src.strip()


def _split_csv(values: List[str]) -> List[str]:
    items: List[str] = []
    for v in values:
        for piece in v.split(','):
            if piece.strip():
                items.append(piece.strip())
    return items


def _fit_title_without_word_cut(title: str, max_len: int = 100) -> str:
    """Fit title to max_len without cutting words in the middle."""
    cleaned = re.sub(r"\s{2,}", " ", title).strip()
    if len(cleaned) <= max_len:
        return cleaned

    words = cleaned.split()
    fitted: List[str] = []
    for word in words:
        candidate = " ".join(fitted + [word]) if fitted else word
        if len(candidate) <= max_len:
            fitted.append(word)
        else:
            break

    # If the first token is too long, skip it entirely to avoid word cutting.
    if not fitted:
        return " ".join(words[1:]).strip() if len(words) > 1 else ""
    return " ".join(fitted).strip()


def _ensure_sentence_ending(text: str, default_mark: str = "!") -> str:
    src = (text or "").strip()
    if not src:
        return src
    if src[-1] in ".!?":
        return src
    return f"{src}{default_mark}"


def _extract_keywords_csv_from_description(description: str) -> str:
    if not description:
        return ""
    for line in description.splitlines():
        if line.lower().startswith("keywords:"):
            return line.split(":", 1)[1].strip()
    return ""


def _trim_text_nicely(text: str, max_len: int) -> str:
    src = re.sub(r"\s{2,}", " ", (text or "").strip())
    if max_len <= 0:
        return ""
    if len(src) <= max_len:
        return src

    sentences = re.split(r"(?<=[.!?])\s+", src)
    kept: List[str] = []
    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue
        candidate = f"{' '.join(kept)} {s}".strip() if kept else s
        if len(candidate) <= max_len:
            kept.append(s)
            continue
        break
    if kept:
        return " ".join(kept).strip()
    return _fit_title_without_word_cut(src, max_len=max_len).strip(" ,;:-")


def _fit_hashtags_block(hashtags_generated: List[str], max_len: int) -> str:
    if max_len <= 0:
        return ""
    items = _normalize_hashtags_list(hashtags_generated or [], max_count=len(hashtags_generated or []))
    if not items:
        return ""
    picked: List[str] = []
    total_len = 0
    for tag in items:
        add_len = len(tag) + (2 if picked else 0)
        if total_len + add_len > max_len:
            break
        picked.append(tag)
        total_len += add_len
    return ", ".join(picked)


def _ensure_title_keywords_and_emoji_end(
    title: str,
    keyword_pool: List[str],
    emoji_token: str,
    max_len: int = 100,
    min_keywords: int = 3,
) -> str:
    txt = re.sub(r"^\s*(?:[\U0001F300-\U0001FAFF\u2600-\u27BF]\s*)+", "", (title or "").strip())
    txt = re.sub(r"\s{2,}", " ", txt).strip()
    txt_low = txt.lower()

    pool: List[str] = []
    seen_pool: Set[str] = set()
    for raw in keyword_pool:
        term = re.sub(r"\s{2,}", " ", (raw or "").strip().lower())
        if not term:
            continue
        if term in seen_pool:
            continue
        seen_pool.add(term)
        pool.append(term)

    present = [k for k in pool if k in txt_low]
    needed = max(0, min_keywords - len(present))
    if needed:
        for k in pool:
            if k in present:
                continue
            candidate = f"{txt} {k}".strip() if txt else k
            if len(candidate) <= max_len:
                txt = candidate
                txt_low = txt.lower()
                present.append(k)
            if len(present) >= min_keywords:
                break

    txt = _fit_title_without_word_cut(txt, max_len=max_len)
    txt = _ensure_sentence_ending(txt, default_mark="!")
    if emoji_token:
        # Remove emoji token if already present and append it to the end.
        txt = txt.replace(emoji_token, "").strip()
        with_emoji = f"{txt} {emoji_token}".strip()
        txt = with_emoji if len(with_emoji) <= max_len else txt
    return txt


def _compose_final_description(
    title: str,
    description_core: str,
    keywords_csv: str,
    hashtags_generated: List[str],
    referral_link: str,
    include_referral: bool,
    max_desc: int,
) -> str:
    head_blocks: List[str] = []
    tail_blocks: List[str] = []

    title_block = (title or "").strip()
    if title_block:
        head_blocks.append(title_block)
    if include_referral and referral_link:
        head_blocks.append(referral_link.strip())

    core = re.sub(r"\n{3,}", "\n\n", (description_core or "").strip())
    link_lines: List[str] = []
    core_blocks: List[str] = []
    for raw_block in re.split(r"\n\s*\n", core):
        block = raw_block.strip()
        if not block:
            continue
        if re.search(r"(https?://|@[\w.]+|t\.me/)", block, flags=re.IGNORECASE):
            if block not in link_lines:
                link_lines.append(block)
            continue
        core_blocks.append(block)
    head_blocks.extend(link_lines[:3])

    hashtags_block = ", ".join(_normalize_hashtags_list(hashtags_generated or [], max_count=len(hashtags_generated or [])))
    if hashtags_block:
        tail_blocks.append(hashtags_block)

    head_text = "\n\n".join([b for b in head_blocks if b]).strip()
    tail_text = "\n\n".join([b for b in tail_blocks if b]).strip()

    # Keep technical tail (keywords + hashtags) whenever possible by trimming core first.
    separators_len = 0
    core_text = "\n\n".join(core_blocks).strip()
    if head_text and core_text:
        separators_len += 2
    if (head_text or core_text) and tail_text:
        separators_len += 2
    available_core = max_desc - len(head_text) - len(tail_text) - separators_len
    if available_core < 0:
        available_core = 0
    fitted_blocks: List[str] = []
    remaining = available_core
    for block in core_blocks:
        if remaining <= 0:
            break
        trimmed = _trim_text_nicely(block, remaining)
        if not trimmed:
            continue
        add_len = len(trimmed) + (2 if fitted_blocks else 0)
        if add_len > remaining + (2 if fitted_blocks else 0):
            break
        fitted_blocks.append(trimmed)
        remaining -= add_len
        if len(trimmed) < len(block.strip()):
            break
    core_text = "\n\n".join(fitted_blocks).strip()

    merged: List[str] = []
    if head_text:
        merged.append(head_text)
    if core_text:
        merged.append(core_text)
    if tail_text:
        merged.append(tail_text)

    assembled = "\n\n".join(merged).strip()
    if len(assembled) <= max_desc:
        return assembled

    # Fallback guard: if still too long, trim core completely, then trim tail as the last resort.
    merged = [x for x in [head_text, tail_text] if x]
    assembled = "\n\n".join(merged).strip()
    if len(assembled) <= max_desc:
        return assembled
    tail_budget = max(0, max_desc - len(head_text) - (2 if head_text else 0))
    safe_tail = _fit_hashtags_block(hashtags_generated, tail_budget)
    merged = [x for x in [head_text, safe_tail] if x]
    assembled = "\n\n".join(merged).strip()
    if len(assembled) <= max_desc:
        return assembled
    return _trim_text_nicely(assembled, max_desc).strip()


def _sanitize_title_for_youtube(title: str, max_len: int = 120, fallback_title: str = "") -> str:
    def _clean(src: str) -> str:
        txt = (src or "").replace("\r", " ").replace("\n", " ")
        txt = re.sub(r"[\x00-\x1F\x7F]", " ", txt)
        txt = re.sub(r"[<>]", " ", txt)
        txt = re.sub(r"\s{2,}", " ", txt).strip()
        txt = _fit_title_without_word_cut(txt, max_len=max_len)
        return txt.strip()

    cleaned = _clean(title)
    if len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]", cleaned)) >= 3:
        return cleaned

    fallback = _clean(fallback_title)
    if len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]", fallback)) >= 3:
        return fallback

    return "Актуальный обзор и полезные рекомендации"


def _extract_hashtags_from_description(description: str, max_count: int = 20) -> List[str]:
    if not description:
        return []
    tags = re.findall(r"#([A-Za-zА-Яа-яЁё0-9_]+)", description)
    normalized = [f"#{t}" for t in tags]
    return _normalize_hashtags_list(normalized, max_count=max_count)


def _hashtags_from_description_terms(description: str, max_count: int = 20) -> List[str]:
    terms = _collect_terms_from_text(description, limit=500)
    raw: List[str] = []
    for term in terms:
        token = re.sub(r"[^\w\s\-]", "", term, flags=re.UNICODE).strip()
        token = token.replace(" ", "")
        if len(token) < 4:
            continue
        raw.append(f"#{token}")
    return _normalize_hashtags_list(raw, max_count=max_count)


def _ensure_hashtags_from_description(
    description: str,
    current_hashtags: List[str],
    max_count: int = 20,
) -> List[str]:
    desc_low = (description or "").lower()
    kept = [
        h for h in (current_hashtags or [])
        if h and h.lstrip("#").lower() in desc_low
    ]
    desc_generated = _hashtags_from_description_terms(description, max_count=max_count)
    merged = kept + desc_generated
    return _normalize_hashtags_list(merged, max_count=max_count)


def _enforce_description_content_policy(text: str) -> str:
    src = (text or "").strip()
    if not src:
        return src
    # User policy: avoid game mentions in generated core text.
    src = re.sub(
        r"\b(игра|игры|игровой|игровые|гейм|геймплей|gaming|gameplay|videogame|steam)\b",
        "",
        src,
        flags=re.IGNORECASE,
    )
    src = re.sub(r"\s{2,}", " ", src).strip()
    focus_terms = ["инстаграм", "тик ток", "ютюб", "грок", "джпт"]
    low = src.lower()
    missing = [term for term in focus_terms if term not in low]
    if missing:
        addon = (
            "Отдельно разбираем продвижение и контент в инстаграм, тик ток и ютюб, "
            "а также работу с грок и джпт для идей, сценариев и роста охватов."
        )
        src = f"{src}\n\n{addon}".strip()
    return src


def _extract_emojis(text: str) -> List[str]:
    if not text:
        return []
    if emoji:
        return [item["emoji"] for item in emoji.emoji_list(text)]
    # Extended fallback matcher: flags + modern emoji blocks + ZWJ sequences.
    emoji_pattern = (
        r"(?:[\U0001F1E6-\U0001F1FF]{2}"  # flags
        r"|[\U0001F300-\U0001F5FF"
        r"\U0001F600-\U0001F64F"
        r"\U0001F680-\U0001F6FF"
        r"\U0001F700-\U0001F77F"
        r"\U0001F780-\U0001F7FF"
        r"\U0001F800-\U0001F8FF"
        r"\U0001F900-\U0001F9FF"
        r"\U0001FA70-\U0001FAFF"
        r"\u2600-\u26FF"
        r"\u2700-\u27BF])"
        r"(?:\uFE0F|\uFE0E)?"
        r"(?:\u200D(?:[\U0001F300-\U0001FAFF\u2600-\u27BF])(?:\uFE0F|\uFE0E)?)*"
    )
    return re.findall(emoji_pattern, text)


def _override_data_folder(config: Dict, data_folder: Path) -> Dict:
    """Return a copy of config with data files pointed to the chosen folder."""
    cfg = {**config, 'paths': dict(config.get('paths', {}))}
    cfg['paths']['data_folder'] = str(data_folder)
    for key, filename in DATA_FILES.items():
        cfg['paths'][key] = str(data_folder / filename)
    return cfg


def _is_router_vpn_profile(data_folder: Path, titles: List[str], keywords: List[str], description_seed: str) -> bool:
    folder_name = (data_folder.name or "").lower()
    if "router" in folder_name or "роут" in folder_name:
        return True

    blob = " ".join(titles[:12] + keywords[:80] + [description_seed or ""]).lower()
    signals = ("keenetic", "кинет", "wireguard", "роутер", "router vpn")
    return sum(1 for s in signals if s in blob) >= 2


def _merge_priority_terms(base_terms: List[str], priority_terms: List[str], limit: int) -> List[str]:
    merged: List[str] = []
    seen: Set[str] = set()
    for term in list(priority_terms) + list(base_terms):
        token = re.sub(r"\s{2,}", " ", (term or "").strip())
        if not token:
            continue
        low = token.lower()
        if low in seen:
            continue
        seen.add(low)
        merged.append(token)
        if len(merged) >= limit:
            break
    return merged


def _augment_prompt_for_router_profile(stage_name: str, prompt: str) -> str:
    additions = {
        "stage_title": (
            "\n\nДОПОЛНИТЕЛЬНЫЙ ФОКУС ДЛЯ ROUTER PROFILE:\n"
            "— Это публикация только про VPN для роутера.\n"
            "— В title делай основной упор на Keenetic/Кинетик и WireGuard.\n"
            "— Предпочитай формулировки: vpn для роутера keenetic, keenetic vpn, keenetic wireguard, настройка vpn keenetic, wireguard на keenetic.\n"
            "— Нужны разные вариации написания: Keenetic, keenetic, Кинетик, кинетик, кинетика.\n"
            "— Не уводи фокус в общие VPN для телефона или ПК, если это не связано с роутером.\n"
        ),
        "stage_description": (
            "\n\nДОПОЛНИТЕЛЬНЫЙ ФОКУС ДЛЯ ROUTER PROFILE:\n"
            "— Описание должно быть только про VPN для роутера.\n"
            "— Обязательно раскрывай Keenetic/Кинетик и WireGuard как основные сценарии.\n"
            "— Упоминай настройку Keenetic, KeeneticOS, VPN client/server, WireGuard на роутере, домашнюю сеть, Wi-Fi, Smart TV, YouTube через роутер.\n"
            "— Дай несколько разных формулировок: Keenetic, кинетик, wireguard, vpn для роутера keenetic, настройка vpn на keenetic.\n"
        ),
        "stage_hashtags_keys": (
            "\n\nДОПОЛНИТЕЛЬНЫЙ ФОКУС ДЛЯ ROUTER PROFILE:\n"
            "— hashtags и keys строй в первую очередь вокруг Keenetic/Кинетик, WireGuard и настройки VPN на роутере.\n"
            "— Добавляй разные релевантные вариации написания и интента настройки.\n"
        ),
        "stage_keywords": (
            "\n\nДОПОЛНИТЕЛЬНЫЙ ФОКУС ДЛЯ ROUTER PROFILE:\n"
            "— Keywords должны быть максимально заточены под VPN для роутера.\n"
            "— Главный кластер: Keenetic/Кинетик/кинетика, WireGuard, настройка VPN на роутере.\n"
            "— Нужны многие разные вариации: vpn для роутера keenetic, keenetic vpn, vpn keenetic, wireguard keenetic, настройка wireguard keenetic, vpn кинетик, впн кинетик, vpn для кинетика.\n"
            "— Старайся занять почти весь лимит длины именно роутерными вариациями, без ухода в общие мобильные VPN.\n"
        ),
    }
    return f"{prompt}{additions.get(stage_name, '')}"


def prompt_data_folder(config: Dict) -> Dict:
    """Scan for data packs and let user pick by number or custom path."""
    current = Path(config['paths']['data_folder']).resolve()

    def collect_packs(root: Path):
        found = []
        if _has_all_data_files(root):
            found.append((root, root.name or "data"))
        for child in sorted(root.iterdir()):
            if child.is_dir() and _has_all_data_files(child):
                found.append((child, child.name))
        return found

    packs = collect_packs(current)
    if not packs and current.parent != current:
        packs.extend(collect_packs(current.parent))

    print("\n📂 Доступные папки данных:")
    for idx, (p, label) in enumerate(packs, 1):
        mark = " (текущая)" if p == current else ""
        print(f"[{idx}] {label} -> {p}{mark}")

    prompt = "Выберите номер или введите путь (Enter оставить текущую): "
    user_input = input(prompt).strip()
    if not user_input:
        # If current folder doesn't contain required files but packs exist,
        # auto-pick the first valid pack to avoid runtime missing-file errors.
        if not _has_all_data_files(current) and packs:
            chosen_auto = packs[0][0]
            print(f"ℹ️ Текущая папка не содержит нужных файлов. Автовыбор: {chosen_auto}")
            return _override_data_folder(config, chosen_auto)
        return config

    if user_input.isdigit() and 1 <= int(user_input) <= len(packs):
        chosen = packs[int(user_input) - 1][0]
    else:
        chosen = Path(user_input).expanduser()
        if not chosen.is_absolute():
            chosen = Path.cwd() / chosen

    if not chosen.exists() or not chosen.is_dir():
        print(f"✖ Папка не найдена: {chosen}. Оставляю текущую.")
        return config

    missing = [name for name in DATA_FILES.values() if not (chosen / name).exists()]
    if missing:
        print(f"⚠️ В выбранной папке нет файлов: {', '.join(missing)}")

    updated = _override_data_folder(config, chosen)
    print(f"✓ Используем данные из: {chosen}")
    return updated


def _has_all_data_files(folder: Path) -> bool:
    return all((folder / name).exists() for name in DATA_FILES.values())


def prompt_include_referral(config: Dict) -> bool:
    """Ask user whether to include referral URL from config in description."""
    referral = config.get('metadata', {}).get('referral_link')
    if not referral:
        return False
    resp = input("Добавлять ссылку из конфига в описание? (Y/n): ").strip().lower()
    return resp in ["", "y", "yes", "д", "да"]


def prompt_category(config: Dict) -> str:
    """Let user pick YouTube category; default to config or Gaming (20)."""
    categories = {
        "1": "Film & Animation",
        "2": "Autos & Vehicles",
        "10": "Music",
        "15": "Pets & Animals",
        "17": "Sports",
        "19": "Travel & Events",
        "20": "Gaming",
        "22": "People & Blogs",
        "23": "Comedy",
        "24": "Entertainment",
        "25": "News & Politics",
        "26": "Howto & Style",
        "27": "Education",
        "28": "Science & Technology",
    }
    default_id = str(config.get("youtube", {}).get("category_id", "20"))
    print("\nКатегория YouTube (Enter оставить по умолчанию):")
    for cid, title in categories.items():
        mark = " (по умолчанию)" if cid == default_id else ""
        print(f"[{cid}] {title}{mark}")
    choice = input("Введите ID категории: ").strip()
    if choice in categories:
        return choice
    print(f"Использую категорию {default_id} ({categories.get(default_id, 'Unknown')})")
    return default_id


def prompt_language(config: Dict) -> Optional[str]:
    """Always publish videos with Russian language."""
    print("Язык публикации зафиксирован: ru")
    return "ru"


def _build_llm_prompt(
    original_title: str,
    title_candidate: str,
    data_title_examples: List[str],
    keywords: List[str],
    hashtags: List[str],
    data_description_seed: str,
    data_profile_terms: List[str],
    priority_keywords: List[str],
    max_title_length: int,
    max_description_length: int,
    max_tags_length: int,
    max_hashtags: int,
    referral_link: str = "",
    include_referral: bool = True,
    prompt_template: Optional[str] = None,
    retry_feedback: str = "",
) -> str:
    keywords_text = ", ".join(keywords)
    hashtags_text = " ".join(hashtags)
    titles_sample_text = " | ".join([t.strip() for t in data_title_examples if t.strip()][:8])
    profile_terms_text = ", ".join(data_profile_terms[:40])
    priority_keywords_text = ", ".join(priority_keywords[:20])
    referral_text = referral_link if include_referral and referral_link else "нет"
    template = prompt_template or """Ты профессиональный YouTube SEO-специалист.

На основе данных:
Оригинальный заголовок:
{original_title}

Кандидат заголовка:
{title_candidate}

Ключевые слова:
{keywords}

Хештеги:
{hashtags}

Семплы заголовков из data:
{data_title_examples}

Базовый текст описания из data:
{data_description_seed}

Термины релевантности (из анализа data):
{data_profile_terms}

Приоритетные ключевые слова:
{priority_keywords}

Referral link:
{referral_link}

Сгенерируй строго по правилам:
1. Заголовок до {max_title_length} символов.
2. Описание до {max_description_length} символов ОБЩЕЙ длиной.
3. Во 2-й строке описания должен быть referral link без изменений.
4. В заголовке и описании должны быть эмодзи.
5. Теги (tags) до {max_tags_length} символов.
6. Ключевые слова (keywords) до {max_tags_length} символов.
7. И теги, и ключевые слова подбирай релевантно на основе заголовка и описания (50%/50%), без повторов.
8. Хештеги (hashtags) до {max_hashtags} штук, релевантные теме.
9. Используй слова из входных data-файлов (titles/keywords/hashtags/description).
10. Не изменяй и не обрабатывай ссылки/упоминания вида http:// https:// @t.me и @.
11. Если это повторная попытка, исправь ошибки из блока FEEDBACK.

FEEDBACK:
{retry_feedback}

Верни строго JSON:
{{
  "title": "",
  "description": "",
  "tags": [],
  "keywords": [],
  "hashtags": []
}}"""
    replacements = {
        "{original_title}": original_title,
        "{title_candidate}": title_candidate,
        "{data_title_examples}": titles_sample_text,
        "{keywords}": keywords_text,
        "{hashtags}": hashtags_text,
        "{data_description_seed}": data_description_seed,
        "{data_profile_terms}": profile_terms_text,
        "{priority_keywords}": priority_keywords_text,
        "{max_title_length}": str(max_title_length),
        "{referral_link}": referral_text,
        "{max_description_length}": str(max_description_length),
        "{max_tags_length}": str(max_tags_length),
        "{max_hashtags}": str(max_hashtags),
        "{retry_feedback}": retry_feedback.strip() or "none",
    }
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    return rendered


def _extract_json_from_text(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty LLM response")

    def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                nested = json.loads(value)
                if isinstance(nested, dict):
                    return nested
            except Exception:
                try:
                    nested = ast.literal_eval(value)
                    if isinstance(nested, dict):
                        return nested
                except Exception:
                    return None
        return None

    def _balanced_object_candidates(src: str) -> List[str]:
        out: List[str] = []
        n = len(src)
        for i, ch in enumerate(src):
            if ch != "{":
                continue
            depth = 0
            in_str = False
            quote = ""
            esc = False
            for j in range(i, n):
                c = src[j]
                if in_str:
                    if esc:
                        esc = False
                        continue
                    if c == "\\":
                        esc = True
                        continue
                    if c == quote:
                        in_str = False
                    continue
                if c in ("'", '"'):
                    in_str = True
                    quote = c
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        out.append(src[i:j + 1])
                        break
        return out

    try:
        data = json.loads(text)
        as_dict = _as_dict(data)
        if as_dict:
            return as_dict
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        chunk = fenced.group(1).strip()
        try:
            data = json.loads(chunk)
            as_dict = _as_dict(data)
            if as_dict:
                return as_dict
        except Exception:
            pass
        for candidate in _balanced_object_candidates(chunk):
            try:
                data = json.loads(candidate)
                as_dict = _as_dict(data)
                if as_dict:
                    return as_dict
            except Exception:
                try:
                    data = ast.literal_eval(candidate)
                    if isinstance(data, dict):
                        return data
                except Exception:
                    continue

    for candidate in _balanced_object_candidates(text):
        try:
            data = json.loads(candidate)
            as_dict = _as_dict(data)
            if as_dict:
                return as_dict
        except Exception:
            try:
                data = ast.literal_eval(candidate)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue

    for match in re.finditer(r"\{[\s\S]*?\}", text):
        candidate = match.group(0)
        try:
            data = json.loads(candidate)
            as_dict = _as_dict(data)
            if as_dict:
                return as_dict
        except Exception:
            try:
                data = ast.literal_eval(candidate)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
            continue
    raise ValueError("JSON object not found in LLM response")


def _extract_keywords_from_free_text(text: str) -> List[str]:
    src = (text or "").strip()
    if not src:
        return []
    # Remove fenced wrappers and obvious labels.
    src = re.sub(r"```(?:json)?", "", src, flags=re.IGNORECASE).strip()
    src = re.sub(r"(?i)\bkeywords?\b\s*:\s*", "", src)
    src = src.replace("\r", "\n")
    # Split by comma/newline/semicolon and clean.
    parts = re.split(r"[,;\n]+", src)
    out: List[str] = []
    seen: Set[str] = set()
    for p in parts:
        token = re.sub(r"\s{2,}", " ", p).strip().strip("\"'`[]{}")
        token = token.lstrip("#")
        if not token:
            continue
        low = token.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(token)
    return out


def _coerce_stage_data(response_text: str, required_keys: List[str], stage_name: str) -> Optional[Dict[str, Any]]:
    """
    Best-effort conversion for non-JSON model replies on specific stages.
    """
    if stage_name == "stage_keywords" and "keywords" in required_keys:
        kws = _extract_keywords_from_free_text(response_text)
        if kws:
            return {"keywords": kws}
    if stage_name == "stage_hashtags_keys" and ("hashtags" in required_keys or "keys" in required_keys):
        hashtags = re.findall(r"#\w+", response_text or "")
        keys = _extract_keywords_from_free_text(response_text or "")
        if hashtags or keys:
            return {"hashtags": hashtags, "keys": keys}
    return None


def _normalize_tags_to_csv(tags_raw: Any, max_tags_length: int) -> str:
    if isinstance(tags_raw, list):
        pieces = [str(x).strip() for x in tags_raw if str(x).strip()]
    elif isinstance(tags_raw, str):
        pieces = [x.strip() for x in tags_raw.split(",") if x.strip()]
    else:
        pieces = []

    seen: Set[str] = set()
    cleaned: List[str] = []
    total_len = 0
    for tag in pieces:
        tag = re.sub(r"\s{2,}", " ", tag).strip()
        tag = re.sub(r"[#@\"']", "", tag)
        if not tag:
            continue
        low = tag.lower()
        if low in seen:
            continue
        add_len = len(tag) + (2 if cleaned else 0)
        if total_len + add_len > max_tags_length:
            continue
        cleaned.append(tag)
        seen.add(low)
        total_len += add_len
    return ", ".join(cleaned)


def _expand_csv_to_min_length(
    base_csv: str,
    min_len: int,
    max_len: int,
    candidate_terms: List[str],
) -> str:
    items = [x.strip() for x in base_csv.split(",") if x.strip()]
    seen = {x.lower() for x in items}
    cur = ", ".join(items)
    if len(cur) >= min_len:
        return cur[:max_len] if len(cur) > max_len else cur

    for term in candidate_terms:
        token = re.sub(r"\s{2,}", " ", (term or "").strip())
        token = re.sub(r"[\"']", "", token)
        if not token:
            continue
        low = token.lower()
        if low in seen:
            continue
        candidate = f"{cur}, {token}" if cur else token
        if len(candidate) > max_len:
            continue
        cur = candidate
        seen.add(low)
        if len(cur) >= min_len:
            break
    return cur


def _expand_description_to_min_length(
    description: str,
    min_len: int,
    max_len: int,
    seed_blocks: List[str],
) -> str:
    cur = (description or "").strip()
    if len(cur) >= min_len:
        return cur[:max_len]

    for block in seed_blocks:
        b = (block or "").strip()
        if not b:
            continue
        candidate = f"{cur}\n\n{b}" if cur else b
        if len(candidate) > max_len:
            continue
        cur = candidate
        if len(cur) >= min_len:
            break
    return cur[:max_len]


def _collect_terms_from_text(text: str, limit: int = 120) -> List[str]:
    src = re.sub(r"https?://\S+|@\S+", " ", (text or ""), flags=re.IGNORECASE)
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9+\-]{2,}", src)
    out: List[str] = []
    seen: Set[str] = set()
    for t in tokens:
        token = t.strip().lower()
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= limit:
            break
    return out


def _collect_keyword_candidates(text: str, limit: int = 120, max_words: int = 3) -> List[str]:
    src = re.sub(r"https?://\S+|@\S+", " ", (text or ""), flags=re.IGNORECASE)
    src = src.replace("\r", " ").replace("\n", " ")
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9+\-]{1,}", src)
    stopwords = {
        "для", "это", "как", "или", "что", "если", "только", "после", "перед", "через",
        "чтобы", "когда", "можно", "очень", "video", "with", "from", "this", "that",
        "your", "about", "into", "the", "and",
    }

    out: List[str] = []
    seen: Set[str] = set()

    def add(term: str) -> None:
        cleaned = re.sub(r"\s{2,}", " ", term).strip(" -_.,;:|").lower()
        if not cleaned or len(cleaned) < 3:
            return
        if cleaned in seen:
            return
        seen.add(cleaned)
        out.append(cleaned)

    for word in words:
        low = word.lower()
        if low in stopwords or len(low) < 3:
            continue
        add(low)
        if len(out) >= limit:
            return out

    for size in range(2, max_words + 1):
        for idx in range(0, len(words) - size + 1):
            chunk_words = [w.lower() for w in words[idx:idx + size]]
            if any(len(w) < 2 for w in chunk_words):
                continue
            if chunk_words[0] in stopwords or chunk_words[-1] in stopwords:
                continue
            add(" ".join(chunk_words))
            if len(out) >= limit:
                return out
    return out


def _build_keywords_csv_by_ratio(
    title: str,
    description_core: str,
    hashtags: List[str],
    extra_terms: List[str],
    max_len: int,
    min_len: int,
) -> str:
    title_pool = _collect_keyword_candidates(title, limit=120, max_words=3)
    desc_pool = _collect_keyword_candidates(description_core, limit=220, max_words=3)
    hash_pool: List[str] = []
    for h in hashtags or []:
        token = re.sub(r"[^\w\s\-]", "", h.lstrip("#"), flags=re.UNICODE).strip()
        token = re.sub(r"\s{2,}", " ", token)
        if token:
            hash_pool.append(token.lower())
    hash_pool = list(dict.fromkeys(hash_pool))
    extra_pool = _collect_keyword_candidates(", ".join(extra_terms), limit=260, max_words=4)

    budget = 140
    need_title = max(1, int(budget * 0.5))
    need_desc = max(1, int(budget * 0.4))
    need_hash = max(1, budget - need_title - need_desc)

    picked: List[str] = []
    used: Set[str] = set()

    def take_from_pool(pool: List[str], amount: int) -> None:
        for token in pool:
            t = re.sub(r"\s{2,}", " ", (token or "").strip())
            t = re.sub(r"[#@\"']", "", t)
            if not t:
                continue
            low = t.lower()
            if low in used:
                continue
            used.add(low)
            picked.append(t)
            if len([x for x in picked if x.lower() in {p.lower() for p in pool}]) >= amount:
                break

    take_from_pool(title_pool, need_title)
    take_from_pool(desc_pool, need_desc)
    take_from_pool(hash_pool, need_hash)

    # Fill remaining space with seeds while preserving relevance.
    fill_pool = title_pool + desc_pool + hash_pool + extra_pool
    for token in fill_pool:
        t = re.sub(r"\s{2,}", " ", (token or "").strip())
        t = re.sub(r"[#@\"']", "", t)
        if not t:
            continue
        low = t.lower()
        if low in used:
            continue
        used.add(low)
        picked.append(t)
        if len(picked) >= 220:
            break

    csv = _normalize_tags_to_csv(picked, max_tags_length=max_len)
    if len(csv) < min_len:
        csv = _expand_csv_to_min_length(
            base_csv=csv,
            min_len=min_len,
            max_len=max_len,
            candidate_terms=fill_pool,
        )
    return csv


def _request_keywords_topup_from_title(
    llm_cfg: Dict[str, Any],
    title: str,
    current_keywords_csv: str,
    max_len: int,
    min_len: int,
    max_attempts: int = 2,
) -> str:
    current = _normalize_tags_to_csv(current_keywords_csv, max_tags_length=max_len)
    if len(current) >= min_len:
        return current

    needed = max(0, min_len - len(current))
    prompt = (
        "Сформируй дополнительные ключевые слова для YouTube строго на основе title.\n"
        "Требования:\n"
        "1. Только релевантные поисковые фразы.\n"
        "2. Формат: через запятую и пробел.\n"
        "3. Без #, URL, @, дубликатов.\n"
        "4. Нужны короткие, средние и long-tail варианты, чтобы максимально заполнить лимит.\n"
        "5. Верни минимум 20 новых вариантов.\n"
        "6. Верни строго JSON: {\"keywords\":[]}\n\n"
        f"title: {title}\n"
        f"current_keywords: {current}\n"
        f"need_at_least_chars: {needed}\n"
        f"max_total_chars: {max_len}\n"
    )
    try:
        data = _run_llm_stage_json(
            llm_cfg=llm_cfg,
            prompt=prompt,
            required_keys=["keywords"],
            stage_name="stage_keywords_topup",
            stage_options={"num_predict": 480},
            max_attempts=max_attempts,
        )
        extra_csv = _normalize_tags_to_csv(data.get("keywords", []), max_tags_length=max_len)
        merged_terms = [x.strip() for x in current.split(",") if x.strip()] + [x.strip() for x in extra_csv.split(",") if x.strip()]
        merged = _normalize_tags_to_csv(merged_terms, max_tags_length=max_len)
        return merged
    except Exception as e:
        _llm_log(f"Keywords topup from title failed: {e}")
        return current


def _normalize_hashtags_list(hashtags_raw: Any, max_count: int) -> List[str]:
    if isinstance(hashtags_raw, list):
        pieces = [str(x).strip() for x in hashtags_raw if str(x).strip()]
    elif isinstance(hashtags_raw, str):
        pieces = [x.strip() for x in hashtags_raw.replace(",", " ").split() if x.strip()]
    else:
        pieces = []
    out: List[str] = []
    seen: Set[str] = set()
    for item in pieces:
        tag = item if item.startswith("#") else f"#{item.lstrip('#')}"
        tag = re.sub(r"\s+", "", tag)
        if len(tag) < 2:
            continue
        low = tag.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(tag)
        if len(out) >= max_count:
            break
    return out


def _fallback_hashtags(
    selected_hashtags: List[str],
    tags_csv: str,
    keywords_csv: str,
    max_count: int,
) -> List[str]:
    base: List[str] = []
    base.extend(selected_hashtags)
    for token in [x.strip() for x in (tags_csv + "," + keywords_csv).split(",") if x.strip()]:
        normalized = re.sub(r"\s{2,}", " ", token).strip()
        if not normalized:
            continue
        # Use first word for hashtag if phrase is too long
        first = normalized.split()[0]
        base.append(first)
    return _normalize_hashtags_list(base, max_count=max_count)


def _validate_generation_quality(
    title: str,
    description: str,
    tags_csv: str,
    keywords_csv: str,
    hashtags: List[str],
    referral_link: str,
    max_title_len: int,
    max_desc_len: int,
    max_tags_len: int,
    target_tags_len: int,
    target_keywords_len: int,
    target_desc_len: int,
) -> List[str]:
    errors: List[str] = []
    if not title:
        errors.append("title пустой")
    if len(title) > max_title_len:
        errors.append(f"title длиннее {max_title_len}")
    if len(title) < 20:
        errors.append("title слишком короткий")
    title_base = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", title).strip()
    if title_base and title_base[-1:] not in [".", "!", "?"]:
        errors.append("title должен заканчиваться знаком препинания перед эмодзи")

    if not description:
        errors.append("description пустой")
    if len(description) > max_desc_len:
        errors.append(f"description длиннее {max_desc_len}")
    if len(description) < target_desc_len:
        errors.append(f"description короче целевого минимума {target_desc_len}")

    lines = [ln.strip() for ln in description.splitlines() if ln.strip()]
    if referral_link:
        if not lines:
            errors.append("description пустой, нет строки с referral")
        elif len(lines) < 2 or lines[1] != referral_link:
            errors.append("вторая строка description должна быть referral_link без изменений")

    if not tags_csv:
        errors.append("tags пустые")
    if len(tags_csv) > max_tags_len:
        errors.append(f"tags длиннее {max_tags_len}")
    if len(tags_csv) < target_tags_len:
        errors.append(f"tags короче целевого минимума {target_tags_len}")

    if not keywords_csv:
        errors.append("keywords пустые")
    if len(keywords_csv) > max_tags_len:
        errors.append(f"keywords длиннее {max_tags_len}")
    if len(keywords_csv) < target_keywords_len:
        errors.append(f"keywords короче целевого минимума {target_keywords_len}")
    if "#" in keywords_csv:
        errors.append("keywords не должны содержать символ #")
    if "," not in keywords_csv:
        errors.append("keywords должны быть перечислены через запятую")

    if not hashtags:
        errors.append("hashtags пустые")
    elif description:
        desc_low = description.lower()
        hashtag_hits = sum(1 for h in hashtags if h.lstrip("#").lower() in desc_low)
        if hashtag_hits < min(3, len(hashtags)):
            errors.append("hashtags должны быть сформированы по тексту description")
    return errors


def _ensure_multiline_description(text: str) -> str:
    """
    Normalize generated text into clear sections with paragraph blocks.
    """
    src = (text or "").strip()
    if not src:
        return src

    # Flatten noisy line breaks first.
    src = re.sub(r"[ \t]+\n", "\n", src)
    src = re.sub(r"\n{3,}", "\n\n", src)
    src = re.sub(r"\s{2,}", " ", src).strip()

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", src) if s.strip()]
    if len(sentences) < 4:
        # At least split short text into readable paragraphs.
        return re.sub(r"(?<=[.!?])\s+", "\n\n", src)

    section_titles = [
        "О чем видео",
        "Что важно знать",
        "Практика и польза",
        "Итог и действие",
    ]

    sections: List[str] = []
    idx = 0
    title_idx = 0
    while idx < len(sentences):
        block_size = 2 if idx == 0 else 3
        chunk = " ".join(sentences[idx:idx + block_size]).strip()
        if not chunk:
            break
        section_name = section_titles[min(title_idx, len(section_titles) - 1)]
        sections.append(f"{section_name}:\n{chunk}")
        idx += block_size
        title_idx += 1

    return "\n\n".join(sections).strip()


def _to_int(value: object | None, default: int) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            src = value.strip()
            if not src:
                return default
            return int(src)
        return default
    except (TypeError, ValueError):
        return default


def _to_float(value: object | None, default: float) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            src = value.strip()
            if not src:
                return default
            return float(src)
        return default
    except (TypeError, ValueError):
        return default


DEFAULT_STAGE_PROMPT_FILENAMES: Dict[str, str] = {
    "stage_title": "stage_title.prompt.txt",
    "stage_description": "stage_description.prompt.txt",
    "stage_hashtags_keys": "stage_hashtags_keys.prompt.txt",
    "stage_keywords": "stage_keywords.prompt.txt",
}

DEFAULT_STAGE_PROMPTS: Dict[str, str] = {
    "stage_title": (
        "Ты генерируешь только заголовок для YouTube.\n"
        "Ограничения:\n"
        "1. Длина title: от 20 до {max_title_length} символов.\n"
        "2. Язык: русский.\n"
        "3. Используй минимум 3 слова из keywords.\n"
        "4. Верни строго JSON: {\"title\":\"...\"}\n"
        "5. Никакого markdown и пояснений.\n\n"
        "Контекст:\n"
        "original_title: {original_title}\n"
        "title_candidate: {title_candidate}\n"
        "title_samples: {title_samples}\n"
        "keywords: {keywords}\n"
    ),
    "stage_description": (
        "Ты генерируешь только core-описание YouTube без title/keywords/hashtags в начале.\n"
        "Ограничения:\n"
        "1. Длина description: от {target_description_min} до {max_description_length} символов.\n"
        "2. Структура: несколько абзацев и переносов строк.\n"
        "3. Не вставляй referral_link в текст: он добавляется системой отдельно.\n"
        "4. Верни строго JSON: {\"description\":\"...\"}\n"
        "5. Никакого markdown и пояснений.\n\n"
        "Контекст:\n"
        "title: {title}\n"
        "referral_link: {referral_link_hint}\n"
        "description_seed: {description_seed}\n"
        "keywords_seed: {keywords}\n"
    ),
    "stage_hashtags_keys": (
        "Ты генерируешь хештеги и базовые keys для YouTube.\n"
        "Ограничения:\n"
        "1. hashtags: от 8 до {max_hashtags} штук, каждый начинается с #.\n"
        "2. keys: список релевантных фраз без #.\n"
        "3. Никаких URL, @ и markdown.\n"
        "4. Верни строго JSON: {\"hashtags\":[], \"keys\":[]}\n\n"
        "Контекст:\n"
        "title: {title}\n"
        "description: {description_excerpt}\n"
        "keywords_seed: {keywords}\n"
        "hashtags_seed: {hashtags_seed}\n"
    ),
    "stage_keywords": (
        "Ты генерируешь финальный список keywords для поля tags в YouTube.\n"
        "Ограничения:\n"
        "1. Верни список keywords (без #) в JSON.\n"
        "2. Итоговая длина после join(', ') должна быть от {target_keywords_min} до {max_tags_length} символов.\n"
        "3. Только релевантные запросы, без дублей, без URL и @.\n"
        "4. Верни строго JSON: {\"keywords\":[]}\n"
        "5. Никакого markdown и пояснений.\n\n"
        "Контекст:\n"
        "title: {title}\n"
        "description: {description_excerpt}\n"
        "hashtags: {hashtags}\n"
        "keys_seed: {keys_seed}\n"
        "keywords_data: {keywords}\n"
    ),
}


def _resolve_llm_prompts_dir(config: Dict[str, Any], llm_cfg: Dict[str, Any]) -> Path:
    configured = str(llm_cfg.get("prompts_dir", "config/prompts")).strip() or "config/prompts"
    p = Path(configured)
    if p.is_absolute():
        return p
    cfg_dir = Path(str(config.get("_config_dir", Path.cwd())))
    return (cfg_dir.parent / p).resolve()


def _load_stage_prompt_template(
    config: Dict[str, Any],
    llm_cfg: Dict[str, Any],
    stage_name: str,
) -> str:
    fallback = DEFAULT_STAGE_PROMPTS[stage_name]
    prompts_dir = _resolve_llm_prompts_dir(config, llm_cfg)
    cfg_key = f"{stage_name}_prompt_file"
    configured = str(llm_cfg.get(cfg_key, "")).strip()
    if configured:
        prompt_path = Path(configured)
        if not prompt_path.is_absolute():
            prompt_path = (prompts_dir / prompt_path).resolve()
    else:
        prompt_path = prompts_dir / DEFAULT_STAGE_PROMPT_FILENAMES[stage_name]
    try:
        text = prompt_path.read_text(encoding="utf-8").strip()
        if text:
            return text
    except Exception as e:
        _llm_log(f"Prompt file for {stage_name} unavailable ({prompt_path}): {e}; using fallback")
    return fallback


def _render_prompt_template(template: str, values: Dict[str, Any]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def _normalize_ollama_generate_url(value: str) -> str:
    src = (value or "").strip() or "http://localhost:11434/api/generate"
    if "://" not in src:
        src = f"http://{src}"
    split = urlsplit(src)
    path = split.path or ""
    if path in {"", "/"}:
        path = "/api/generate"
    elif path.rstrip("/") == "/api":
        path = "/api/generate"
    elif not path.endswith("/api/generate") and not path.endswith("/api/chat"):
        path = path.rstrip("/") + "/api/generate"
    return urlunsplit((split.scheme, split.netloc, path, split.query, split.fragment))


def _normalize_openai_chat_url(value: str) -> str:
    src = (value or "").strip() or "https://api.openai.com/v1/chat/completions"
    if "://" not in src:
        src = f"https://{src}"
    split = urlsplit(src)
    path = split.path or ""
    if path in {"", "/"}:
        path = "/v1/chat/completions"
    elif path.rstrip("/") == "/v1":
        path = "/v1/chat/completions"
    elif not path.endswith("/chat/completions"):
        path = path.rstrip("/") + "/chat/completions"
    return urlunsplit((split.scheme, split.netloc, path, split.query, split.fragment))


def _normalize_openai_images_url(value: str) -> str:
    src = (value or "").strip() or "https://api.openai.com/v1/images/generations"
    if "://" not in src:
        src = f"https://{src}"
    split = urlsplit(src)
    path = split.path or ""
    if path in {"", "/"}:
        path = "/v1/images/generations"
    elif path.rstrip("/") == "/v1":
        path = "/v1/images/generations"
    elif not path.endswith("/images/generations"):
        path = path.rstrip("/") + "/images/generations"
    return urlunsplit((split.scheme, split.netloc, path, split.query, split.fragment))


def _get_openai_api_key(llm_cfg: Dict[str, Any]) -> str:
    return (os.environ.get("OPENAI_API_KEY") or str(llm_cfg.get("openai_api_key", ""))).strip()


def _call_openai_generate(
    prompt: str,
    llm_cfg: Dict[str, Any],
    stage_options: Optional[Dict[str, Any]] = None,
) -> Tuple[str, float]:
    url = _normalize_openai_chat_url(str(llm_cfg.get("openai_base_url", "https://api.openai.com/v1")))
    timeout = _to_int(llm_cfg.get("timeout"), 120)
    api_key = _get_openai_api_key(llm_cfg)
    if not api_key:
        raise LLMFatalError("llm.openai_api_key is required when llm.provider='openai'")

    model_name = str(
        llm_cfg.get("openai_model_name", llm_cfg.get("model_name", "gpt-4o-mini"))
    ).strip() or "gpt-4o-mini"
    temperature = _to_float(llm_cfg.get("temperature"), 0.7)
    top_p = _to_float(llm_cfg.get("top_p"), 0.9)
    num_predict = _to_int(llm_cfg.get("num_predict"), 1200)
    if stage_options:
        if stage_options.get("temperature") is not None:
            temperature = _to_float(stage_options.get("temperature"), temperature)
        if stage_options.get("top_p") is not None:
            top_p = _to_float(stage_options.get("top_p"), top_p)
        if stage_options.get("num_predict") is not None:
            num_predict = _to_int(stage_options.get("num_predict"), num_predict)

    payload: Dict[str, Any] = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": num_predict,
        "response_format": {"type": "json_object"},
    }
    req = urlrequest.Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    _llm_log(f"HTTP POST {url} (provider=openai, model={model_name}, max_tokens={num_predict})")
    started = perf_counter()
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        elapsed = perf_counter() - started
        parsed = json.loads(raw)
        choices = parsed.get("choices", []) if isinstance(parsed, dict) else []
        if not choices:
            raise RuntimeError("OpenAI response has no choices")
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = str(message.get("content", "") or "").strip()
        if not content:
            raise RuntimeError("OpenAI response content is empty")
        _llm_log(f"OpenAI response received in {elapsed:.2f}s")
        return content, elapsed
    except urlerror.HTTPError as err:
        raw = ""
        try:
            raw = err.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        details = raw.strip()
        if details.startswith("{") and details.endswith("}"):
            try:
                parsed = json.loads(details)
                if isinstance(parsed, dict):
                    data = parsed.get("error")
                    if isinstance(data, dict):
                        details = str(data.get("message") or details)
            except Exception:
                pass
        if err.code in {401, 403}:
            raise LLMFatalError(f"OpenAI auth failed: {details or err.reason}") from err
        raise OpenAIHTTPError(err.code, str(err.reason), details) from err
    except urlerror.URLError as e:
        raise RuntimeError(f"OpenAI request failed: {e}") from e


def _call_openai_image_generate(
    prompt: str,
    config: Dict[str, Any],
) -> Tuple[bytes, Dict[str, Any], float]:
    llm_cfg = config.get("llm", {})
    image_cfg = config.get("image_generation", {})
    api_key = _get_openai_api_key(llm_cfg)
    if not api_key:
        raise LLMFatalError("OPENAI_API_KEY or llm.openai_api_key is required for preview generation")

    base_url = str(image_cfg.get("openai_base_url") or llm_cfg.get("openai_base_url", "https://api.openai.com/v1"))
    url = _normalize_openai_images_url(base_url)
    timeout = _to_int(image_cfg.get("timeout", llm_cfg.get("timeout")), 180)
    payload: Dict[str, Any] = {
        "model": str(image_cfg.get("model", "gpt-image-1")).strip() or "gpt-image-1",
        "prompt": prompt,
        "size": str(image_cfg.get("size", "1536x1024")).strip() or "1536x1024",
        "quality": str(image_cfg.get("quality", "medium")).strip() or "medium",
        "n": 1,
    }
    if image_cfg.get("background"):
        payload["background"] = str(image_cfg.get("background"))

    req = urlrequest.Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    _llm_log(
        f"HTTP POST {url} (provider=openai, model={payload['model']}, size={payload['size']}, quality={payload['quality']})"
    )
    started = perf_counter()
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        elapsed = perf_counter() - started
        parsed = json.loads(raw)
        data = parsed.get("data", []) if isinstance(parsed, dict) else []
        if not data or not isinstance(data[0], dict):
            raise RuntimeError("OpenAI image response has no data")
        first = data[0]
        if first.get("b64_json"):
            image_bytes = base64.b64decode(first["b64_json"])
        elif first.get("url"):
            with urlrequest.urlopen(str(first["url"]), timeout=timeout) as img_resp:
                image_bytes = img_resp.read()
        else:
            raise RuntimeError("OpenAI image response has neither b64_json nor url")
        _llm_log(f"OpenAI preview image received in {elapsed:.2f}s")
        safe_meta = {
            "model": payload["model"],
            "size": payload["size"],
            "quality": payload["quality"],
            "usage": parsed.get("usage") if isinstance(parsed, dict) else None,
        }
        return image_bytes, safe_meta, elapsed
    except urlerror.HTTPError as err:
        raw = ""
        try:
            raw = err.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        details = raw.strip()
        if details.startswith("{") and details.endswith("}"):
            try:
                parsed = json.loads(details)
                if isinstance(parsed, dict):
                    data = parsed.get("error")
                    if isinstance(data, dict):
                        details = str(data.get("message") or details)
            except Exception:
                pass
        if err.code in {401, 403}:
            raise LLMFatalError(f"OpenAI image auth failed: {details or err.reason}") from err
        raise OpenAIHTTPError(err.code, str(err.reason), details) from err
    except urlerror.URLError as e:
        raise RuntimeError(f"OpenAI image request failed: {e}") from e


def _call_ollama_generate(
    prompt: str,
    llm_cfg: Dict[str, Any],
    stage_options: Optional[Dict[str, Any]] = None,
) -> Tuple[str, float]:
    provider = str(llm_cfg.get("provider", "ollama")).strip().lower() or "ollama"
    if provider == "openai":
        return _call_openai_generate(prompt, llm_cfg, stage_options=stage_options)

    url = _normalize_ollama_generate_url(str(llm_cfg.get("ollama_url", "http://localhost:11434/api/generate")))
    model_name = str(llm_cfg.get("model_name", "qwen3:8b")).strip()
    timeout = _to_int(llm_cfg.get("timeout"), 120)
    exec_mode = str(llm_cfg.get("execution_mode", "hybrid")).strip().lower()
    options: Dict[str, Any] = {
        "temperature": _to_float(llm_cfg.get("temperature"), 0.8),
        "top_p": _to_float(llm_cfg.get("top_p"), 0.9),
        "num_predict": _to_int(llm_cfg.get("num_predict"), 4000),
        "num_ctx": _to_int(llm_cfg.get("num_ctx"), 8192),
        "repeat_penalty": _to_float(llm_cfg.get("repeat_penalty"), 1.1),
    }
    # Ollama runtime mode:
    # cpu    -> force CPU (num_gpu=0)
    # gpu    -> force GPU layers (num_gpu=-1 by default, or explicit value)
    # hybrid -> split load between GPU and CPU using configured num_gpu + num_thread
    if exec_mode == "cpu":
        options["num_gpu"] = 0
    elif exec_mode == "gpu":
        options["num_gpu"] = _to_int(llm_cfg.get("num_gpu"), -1)
    else:
        exec_mode = "hybrid"
        if llm_cfg.get("num_gpu") is not None:
            options["num_gpu"] = _to_int(llm_cfg.get("num_gpu"), -1)
    if llm_cfg.get("num_thread") is not None:
        options["num_thread"] = _to_int(llm_cfg.get("num_thread"), 8)
    if stage_options:
        for k, v in stage_options.items():
            if v is None:
                continue
            if k in {"num_predict", "num_ctx", "num_gpu", "num_thread"}:
                options[k] = _to_int(v, _to_int(options.get(k), 0))
            elif k in {"temperature", "top_p", "repeat_penalty"}:
                options[k] = _to_float(v, _to_float(options.get(k), 0.0))
            else:
                options[k] = v

    think_cfg = llm_cfg.get("think")
    model_name_lower = model_name.lower()
    think_value: Optional[bool]
    if think_cfg is None:
        # Qwen3 models may return only "thinking" with empty response/content unless think is disabled.
        think_value = False if model_name_lower.startswith("qwen3") else None
    else:
        think_value = bool(think_cfg)

    payload = {
        "model": model_name,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": options,
    }
    if think_value is not None:
        payload["think"] = think_value
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    def _parse_http_error(err: urlerror.HTTPError) -> OllamaHTTPError:
        raw = ""
        try:
            raw = err.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        details = raw.strip()
        if details.startswith("{") and details.endswith("}"):
            try:
                parsed = json.loads(details)
                if isinstance(parsed, dict):
                    details = str(parsed.get("error") or parsed.get("message") or details)
            except Exception:
                pass
        return OllamaHTTPError(err.code, str(err.reason), details)

    def do_post(target_url: str, body_payload: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
        req = urlrequest.Request(
            url=target_url,
            data=json.dumps(body_payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        started = perf_counter()
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw), perf_counter() - started
        except urlerror.HTTPError as err:
            raise _parse_http_error(err) from err

    def do_inference(model_for_call: str) -> Tuple[str, float]:
        call_payload = dict(payload)
        call_payload["model"] = model_for_call

        def _extract_text(parsed: Dict[str, Any]) -> str:
            response_text = ""
            if isinstance(parsed, dict):
                response_text = str(parsed.get("response", "") or "")
                if not response_text:
                    message = parsed.get("message", {})
                    if isinstance(message, dict):
                        response_text = str(message.get("content", "") or "")
                if response_text:
                    return response_text
                thinking_text = str(
                    parsed.get("thinking")
                    or (parsed.get("message", {}) or {}).get("thinking")
                    or ""
                ).strip()
                if thinking_text:
                    # Some models return JSON-like payload in "thinking"; unwrap common {role, content} envelope.
                    try:
                        jt = json.loads(thinking_text)
                        if isinstance(jt, dict):
                            content = jt.get("content")
                            if isinstance(content, str) and content.strip():
                                return content.strip()
                    except Exception:
                        pass
                    return thinking_text
            return ""

        try:
            _llm_log(
                f"HTTP POST {url} (model={model_for_call}, mode={exec_mode}, "
                f"num_gpu={options.get('num_gpu', 'auto')}, num_thread={options.get('num_thread', 'auto')}, "
                f"num_predict={options.get('num_predict')}, num_ctx={options.get('num_ctx')})"
            )
            parsed, elapsed = do_post(url, call_payload)
            response_text = _extract_text(parsed)
            if not response_text:
                raise RuntimeError("Ollama response field is empty")
            _llm_log(f"Ollama response received via /api/generate in {elapsed:.2f}s")
            return response_text, elapsed
        except OllamaHTTPError as e:
            # Some local Ollama builds expose /api/chat while /api/generate may be unavailable.
            if e.code != 404:
                raise
            details = (e.details or "").lower()
            if "model" in details and "not found" in details:
                raise LLMFatalError(
                    f"Configured model '{model_for_call}' is unavailable in Ollama: {e.details}"
                ) from e
            split = urlsplit(url)
            chat_url = urlunsplit((split.scheme, split.netloc, "/api/chat", split.query, split.fragment))
            chat_payload = {
                "model": model_for_call,
                "messages": [{"role": "user", "content": prompt}],
                "format": "json",
                "stream": False,
                "options": call_payload["options"],
            }
            if think_value is not None:
                chat_payload["think"] = think_value
            _llm_log(f"/api/generate unavailable (404), fallback to {chat_url}")
            try:
                parsed, elapsed = do_post(chat_url, chat_payload)
            except OllamaHTTPError as chat_err:
                chat_details = (chat_err.details or "").lower()
                if chat_err.code == 404:
                    if "model" in chat_details and "not found" in chat_details:
                        raise LLMFatalError(
                            f"Configured model '{model_for_call}' is unavailable in Ollama: {chat_err.details}"
                        ) from chat_err
                    raise LLMFatalError(
                        f"Ollama endpoint mismatch: both /api/generate and /api/chat returned 404 at {split.scheme}://{split.netloc}"
                    ) from chat_err
                raise
            response_text = _extract_text(parsed if isinstance(parsed, dict) else {})
            if not response_text:
                raise RuntimeError("Ollama chat response content is empty")
            _llm_log(f"Ollama response received via /api/chat in {elapsed:.2f}s")
            return response_text, elapsed
        except urlerror.URLError as e:
            raise RuntimeError(f"Ollama request failed: {e}") from e

    fallback_model = str(llm_cfg.get("fallback_model_name", "")).strip()
    disable_primary_on_first_failure = bool(llm_cfg.get("disable_primary_on_first_failure", True))
    primary_disabled = bool(llm_cfg.get("_primary_disabled", False))
    active_primary = model_name
    if primary_disabled and fallback_model and fallback_model != model_name:
        active_primary = fallback_model
        if not bool(llm_cfg.get("_primary_disabled_logged", False)):
            _llm_log(f"Primary model {model_name} disabled for this run. Using {fallback_model}")
            llm_cfg["_primary_disabled_logged"] = True
    try:
        return do_inference(active_primary)
    except Exception as first_err:
        failed_primary = active_primary == model_name
        if failed_primary and fallback_model and fallback_model != model_name:
            if disable_primary_on_first_failure:
                llm_cfg["_primary_disabled"] = True
            _llm_log(f"Primary model failed ({model_name}): {first_err}. Fallback -> {fallback_model}")
            return do_inference(fallback_model)
        raise


def _run_llm_stage_json(
    llm_cfg: Dict[str, Any],
    prompt: str,
    required_keys: List[str],
    stage_name: str,
    stage_options: Optional[Dict[str, Any]] = None,
    max_attempts: int = 6,
) -> Dict[str, Any]:
    last_error = ""
    feedback = ""
    _llm_log(f"Stage '{stage_name}' started (max_attempts={max_attempts})")
    _llm_progress(stage_name, 0, max_attempts, "start")
    for i in range(1, max_attempts + 1):
        stage_prompt = prompt
        if feedback:
            stage_prompt = f"{prompt}\n\nFEEDBACK:\n{feedback}"
        try:
            _llm_log(f"Stage '{stage_name}' attempt {i}/{max_attempts}")
            response_text, _elapsed = _call_ollama_generate(stage_prompt, llm_cfg, stage_options=stage_options)
            try:
                data = _extract_json_from_text(response_text)
            except Exception:
                coerced = _coerce_stage_data(response_text, required_keys, stage_name)
                if not coerced:
                    raise
                _llm_log(f"Stage '{stage_name}' non-JSON response repaired by fallback parser")
                data = coerced
            missing = [k for k in required_keys if k not in data]
            if missing:
                raise ValueError(f"{stage_name}: missing keys {', '.join(missing)}")
            _llm_log(f"Stage '{stage_name}' completed on attempt {i}")
            _llm_progress(stage_name, i, max_attempts, "ok")
            return data
        except Exception as e:
            last_error = str(e)
            if isinstance(e, LLMFatalError):
                _llm_log(f"Stage '{stage_name}' fatal error: {e}")
                raise
            feedback = f"{stage_name}: исправь формат JSON и верни ключи {required_keys}. Ошибка: {e}"
            _llm_log(f"Stage '{stage_name}' attempt {i} failed: {e}")
            _llm_progress(stage_name, i, max_attempts, "retry")
            if i < max_attempts:
                sleep(0.5 * i)
    raise RuntimeError(f"{stage_name} failed after retries: {last_error}")


def _generate_with_ollama(
    config: Dict,
    original_title: str = "",
    include_referral: bool = True,
    used_title_core_terms: Optional[Set[str]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    paths = config["paths"]
    data_folder = Path(paths["data_folder"])
    llm_cfg = config.get("llm", {})
    provider = str(llm_cfg.get("provider", "ollama")).strip().lower() or "ollama"
    if provider != "openai":
        raise LLMFatalError(
            "Title/description generation is restricted to GPT. Set llm.provider='openai'."
        )

    def _autofix_data_pack_if_needed() -> bool:
        """Auto-select first valid data pack if current paths point to missing files."""
        roots: List[Path] = []
        configured_root = Path(paths.get("data_folder", "")).expanduser()
        roots.append(configured_root)
        roots.append(configured_root.parent)
        roots.append(Path.cwd() / "data")
        roots.append(Path(__file__).resolve().parent / "data")
        seen: Set[str] = set()
        uniq_roots: List[Path] = []
        for r in roots:
            key = str(r.resolve()) if r.exists() else str(r)
            if key in seen:
                continue
            seen.add(key)
            uniq_roots.append(r)

        for root in uniq_roots:
            if not root.exists() or not root.is_dir():
                continue
            candidates: List[Path] = []
            if _has_all_data_files(root):
                candidates.append(root)
            for child in sorted(root.iterdir()):
                if child.is_dir() and _has_all_data_files(child):
                    candidates.append(child)
            if not candidates:
                continue
            chosen = candidates[0]
            for key, filename in DATA_FILES.items():
                paths[key] = str(chosen / filename)
            paths["data_folder"] = str(chosen)
            logger.info("Auto-selected data pack: %s", chosen)
            print(f"ℹ️ Автовыбор data-пакета: {chosen}")
            _history_event("autofix_data_folder", chosen=str(chosen))
            return True
        return False

    titles_raw = _read_lines(Path(paths["titles_file"]))
    keywords_raw = _read_lines(Path(paths["keywords_file"]))
    hashtags_raw = _read_lines(Path(paths["hashtags_file"]))
    if (not titles_raw or not keywords_raw or not hashtags_raw) and _autofix_data_pack_if_needed():
        titles_raw = _read_lines(Path(paths["titles_file"]))
        keywords_raw = _read_lines(Path(paths["keywords_file"]))
        hashtags_raw = _read_lines(Path(paths["hashtags_file"]))

    titles = [_payload_from_line(l) for l in titles_raw]
    keywords_list = _split_csv([_payload_from_line(l) for l in keywords_raw])
    hashtags_values = _split_csv([_payload_from_line(h) for h in hashtags_raw])
    hashtags_list: List[str] = []
    for h in hashtags_values:
        tag = h.strip()
        if not tag:
            continue
        hashtags_list.append(tag if tag.startswith("#") else f"#{tag}")
    hashtags_list = list(dict.fromkeys(hashtags_list))

    if not titles or not keywords_list:
        logger.warning("Not enough data for LLM generation: titles/keywords missing")
        return None, None, None

    # Hard business rules requested by user.
    requested_title_len = _to_int(config.get("youtube", {}).get("max_title_length"), 120)
    max_title_len = min(YOUTUBE_HARD_TITLE_LIMIT, requested_title_len)
    if requested_title_len > YOUTUBE_HARD_TITLE_LIMIT:
        _llm_log(
            f"Config max_title_length={requested_title_len} exceeds YouTube limit {YOUTUBE_HARD_TITLE_LIMIT}; clamped"
        )
    max_desc = 3000
    max_tags_length = min(YOUTUBE_KEYWORDS_MAX_LEN, _to_int(config.get("youtube", {}).get("max_tags_length"), 500))
    max_hashtags = 20
    referral_link = str(config.get("metadata", {}).get("referral_link", "")).strip()
    emoji_list = config.get("metadata", {}).get("emojis", ["🔥"])
    emoji_token = random.choice(emoji_list) if emoji_list else "🔥"
    max_attempts = _to_int(llm_cfg.get("max_attempts"), 20)
    stage_title_attempts = _to_int(llm_cfg.get("stage_title_max_attempts"), 3)
    stage_desc_attempts = _to_int(llm_cfg.get("stage_description_max_attempts"), 3)
    stage_hk_attempts = _to_int(llm_cfg.get("stage_hashtags_keys_max_attempts"), 2)
    stage_kw_attempts = _to_int(llm_cfg.get("stage_keywords_max_attempts"), 2)
    use_model_for_keywords = bool(llm_cfg.get("stage_keywords_use_model", False))
    target_desc_len = min(max_desc, _to_int(llm_cfg.get("target_description_min"), DEFAULT_TARGET_DESCRIPTION_MIN))
    target_tags_len = _to_int(llm_cfg.get("target_tags_min"), 450)
    target_keywords_len = min(max_tags_length, max(YOUTUBE_KEYWORDS_MIN_LEN, _to_int(llm_cfg.get("target_keywords_min"), DEFAULT_TARGET_KEYWORDS_MIN)))
    desired_keywords_len = min(max_tags_length, YOUTUBE_KEYWORDS_MAX_LEN)
    stage_title_predict = _to_int(llm_cfg.get("stage_title_num_predict"), 220)
    stage_desc_predict = _to_int(llm_cfg.get("stage_description_num_predict"), 1800)
    stage_hk_predict = _to_int(llm_cfg.get("stage_hashtags_keys_num_predict"), 520)
    stage_kw_predict = _to_int(llm_cfg.get("stage_keywords_num_predict"), 900)

    selected_title_raw = original_title.strip() if (original_title or "").strip() else random.choice(titles)
    selected_title = _prepare_title_seed_for_model(selected_title_raw, max_len=180) or selected_title_raw
    selected_title_samples = random.sample(titles, min(8, len(titles))) if titles else []
    selected_keywords = random.sample(keywords_list, min(25, len(keywords_list))) if keywords_list else []
    selected_hashtags = random.sample(hashtags_list, min(max_hashtags, len(hashtags_list))) if hashtags_list else []
    description_seed = ""
    try:
        description_seed = Path(paths.get("description_file", "")).read_text(encoding="utf-8-sig").strip()
    except Exception:
        description_seed = ""

    router_profile = _is_router_vpn_profile(data_folder, titles, keywords_list, description_seed)
    if router_profile:
        target_desc_len = min(max_desc, _to_int(llm_cfg.get("router_target_description_min"), ROUTER_PROFILE_TARGET_DESCRIPTION_MIN))
        target_keywords_len = min(max_tags_length, _to_int(llm_cfg.get("router_target_keywords_min"), ROUTER_PROFILE_TARGET_KEYWORDS_MIN))
        stage_kw_predict = max(stage_kw_predict, _to_int(llm_cfg.get("router_stage_keywords_num_predict"), 1000))
        stage_kw_attempts = max(stage_kw_attempts, _to_int(llm_cfg.get("router_stage_keywords_max_attempts"), 3))
        stage_desc_predict = max(stage_desc_predict, _to_int(llm_cfg.get("router_stage_description_num_predict"), 1900))
        selected_keywords = _merge_priority_terms(selected_keywords, ROUTER_PROFILE_TERMS, limit=40)
        keywords_list = _merge_priority_terms(keywords_list, ROUTER_PROFILE_TERMS, limit=max(120, len(keywords_list) + len(ROUTER_PROFILE_TERMS)))
        hashtags_list = _merge_priority_terms(hashtags_list, ["#keenetic", "#wireguard", "#vpnrouter", "#vpnkeenetic", "#кинетик"], limit=max_hashtags * 3)

    stage_templates = {
        "stage_title": _load_stage_prompt_template(config, llm_cfg, "stage_title"),
        "stage_description": _load_stage_prompt_template(config, llm_cfg, "stage_description"),
        "stage_hashtags_keys": _load_stage_prompt_template(config, llm_cfg, "stage_hashtags_keys"),
        "stage_keywords": _load_stage_prompt_template(config, llm_cfg, "stage_keywords"),
    }
    if router_profile:
        stage_templates = {
            name: _augment_prompt_for_router_profile(name, template)
            for name, template in stage_templates.items()
        }
    model_name = llm_cfg.get("model_name", "qwen3:8b")
    used_titles = used_title_core_terms if used_title_core_terms is not None else set()

    def _title_key(value: str) -> str:
        return re.sub(r"\s{2,}", " ", (value or "").strip().lower())

    last_error = ""
    exec_mode = str(llm_cfg.get("execution_mode", "hybrid")).strip().lower() or "hybrid"
    _llm_log(
        f"Generation started | model={model_name} | mode={exec_mode} | "
        f"video_title_seed='{selected_title[:60]}'"
    )
    _llm_log(f"Data pack in use: {paths.get('data_folder', '')} | titles={len(titles)} keywords={len(keywords_list)} hashtags={len(hashtags_list)}")
    if router_profile:
        _llm_log(
            f"Router profile active | target_desc_len={target_desc_len} | "
            f"target_keywords_len={target_keywords_len} | stage_kw_predict={stage_kw_predict}"
        )
    _llm_progress("global", 0, max_attempts, "start")
    for attempt in range(1, max_attempts + 1):
        try:
            _llm_log(f"Global attempt {attempt}/{max_attempts}")
            _llm_log("Pipeline order: title -> description -> hashtags+keys -> keywords")
            _llm_progress("global", attempt - 1, max_attempts, "running")
            attempt_started = perf_counter()
            # Stage 1: title
            _llm_progress("pipeline", 1, 4, "title")
            stage1_prompt = _render_prompt_template(
                stage_templates["stage_title"],
                {
                    "original_title": original_title,
                    "title_candidate": selected_title,
                    "title_samples": " | ".join(selected_title_samples),
                    "keywords": ", ".join(selected_keywords),
                    "max_title_length": max_title_len,
                },
            )
            stage1 = _run_llm_stage_json(
                llm_cfg,
                stage1_prompt,
                ["title"],
                "stage_title",
                stage_options={"num_predict": stage_title_predict},
                max_attempts=stage_title_attempts,
            )
            title = str(stage1.get("title", "")).strip()
            title = _ensure_title_keywords_and_emoji_end(
                title=title,
                keyword_pool=selected_keywords,
                emoji_token=emoji_token,
                max_len=max_title_len,
                min_keywords=3,
            )
            if not title:
                title = _ensure_title_keywords_and_emoji_end(
                    title=selected_title,
                    keyword_pool=selected_keywords,
                    emoji_token=emoji_token,
                    max_len=max_title_len,
                    min_keywords=3,
                )
            title_norm = _title_key(title)
            if title_norm in used_titles:
                raise ValueError("title already used in this run; regenerate unique title")

            # Stage 2: description
            _llm_progress("pipeline", 2, 4, "description")
            stage2_prompt = _render_prompt_template(
                stage_templates["stage_description"],
                {
                    "title": title,
                    "referral_link_hint": referral_link if include_referral else "нет",
                    "description_seed": description_seed[:1500],
                    "keywords": ", ".join(selected_keywords),
                    "target_description_min": target_desc_len,
                    "max_description_length": max_desc,
                },
            )
            stage2 = _run_llm_stage_json(
                llm_cfg,
                stage2_prompt,
                ["description"],
                "stage_description",
                stage_options={"num_predict": stage_desc_predict},
                max_attempts=stage_desc_attempts,
            )
            description_core = str(stage2.get("description", "")).strip()
            description_core = _enforce_description_content_policy(description_core)

            # Stage 3: hashtags + keys
            _llm_progress("pipeline", 3, 4, "hashtags+keys")
            stage3_prompt = _render_prompt_template(
                stage_templates["stage_hashtags_keys"],
                {
                    "title": title,
                    "description_excerpt": description_core[:1500],
                    "keywords": ", ".join(selected_keywords),
                    "hashtags_seed": ", ".join(selected_hashtags),
                    "max_hashtags": max_hashtags,
                },
            )
            stage3 = _run_llm_stage_json(
                llm_cfg,
                stage3_prompt,
                ["hashtags", "keys"],
                "stage_hashtags_keys",
                stage_options={"num_predict": stage_hk_predict},
                max_attempts=stage_hk_attempts,
            )
            hashtags_generated = _normalize_hashtags_list(stage3.get("hashtags", []), max_count=max_hashtags)
            hashtags_generated = _ensure_hashtags_from_description(
                description=description_core,
                current_hashtags=hashtags_generated,
                max_count=max_hashtags,
            )
            keys_seed_csv = _normalize_tags_to_csv(stage3.get("keys", []), max_tags_length=max_tags_length)

            # Stage 4: final keywords 500
            _llm_progress("pipeline", 4, 4, "keywords")
            ratio_seed_terms = (
                _collect_terms_from_text(title, limit=80)
                + _collect_terms_from_text(description_core, limit=180)
                + [h.lstrip("#") for h in hashtags_generated]
                + [x.strip() for x in keys_seed_csv.split(",") if x.strip()]
                + selected_keywords
            )
            if router_profile:
                ratio_seed_terms = _merge_priority_terms(ratio_seed_terms, ROUTER_PROFILE_TERMS, limit=220)
            if use_model_for_keywords:
                stage4_prompt = _render_prompt_template(
                    stage_templates["stage_keywords"],
                    {
                        "title": title,
                        "description_excerpt": description_core[:1800],
                        "hashtags": ", ".join(hashtags_generated),
                        "keys_seed": ", ".join(ratio_seed_terms[:120]),
                        "keywords": ", ".join((selected_keywords + ratio_seed_terms)[:160]),
                        "target_keywords_min": target_keywords_len,
                        "max_tags_length": max_tags_length,
                    },
                )
                stage4 = _run_llm_stage_json(
                    llm_cfg,
                    stage4_prompt,
                    ["keywords"],
                    "stage_keywords",
                    stage_options={"num_predict": stage_kw_predict},
                    max_attempts=stage_kw_attempts,
                )
                llm_keywords_csv = _normalize_tags_to_csv(
                    stage4.get("keywords", stage3.get("keys", [])),
                    max_tags_length=max_tags_length,
                )
                keywords_csv = _build_keywords_csv_by_ratio(
                    title=title,
                    description_core=description_core,
                    hashtags=hashtags_generated,
                    extra_terms=[x.strip() for x in llm_keywords_csv.split(",") if x.strip()] + ratio_seed_terms,
                    max_len=max_tags_length,
                    min_len=target_keywords_len,
                )
            else:
                local_terms = []
                local_terms.extend([x.strip() for x in keys_seed_csv.split(",") if x.strip()])
                local_terms.extend(selected_keywords)
                local_terms.extend([h.lstrip("#") for h in hashtags_generated])
                local_terms.extend(_collect_terms_from_text(title, limit=40))
                local_terms.extend(_collect_terms_from_text(description_core, limit=120))
                if router_profile:
                    local_terms = _merge_priority_terms(local_terms, ROUTER_PROFILE_TERMS, limit=240)
                keywords_csv = _build_keywords_csv_by_ratio(
                    title=title,
                    description_core=description_core,
                    hashtags=hashtags_generated,
                    extra_terms=local_terms,
                    max_len=max_tags_length,
                    min_len=target_keywords_len,
                )
                _llm_log("Stage 'stage_keywords' completed locally (model disabled for this stage)")
            tags_csv = keywords_csv
            if not keywords_csv:
                keywords_csv = _normalize_tags_to_csv(selected_keywords, max_tags_length=max_tags_length)
                tags_csv = keywords_csv
            if not hashtags_generated:
                hashtags_generated = _fallback_hashtags(
                    selected_hashtags=selected_hashtags,
                    tags_csv=tags_csv,
                    keywords_csv=keywords_csv,
                    max_count=max_hashtags,
                )

            # Final description: line 1 title, line 2 referral, keywords/hashtags only at the end.
            description_core = _ensure_multiline_description(description_core)

            # Auto-expand fields to reach quality targets with relevant source data.
            description_core = _expand_description_to_min_length(
                description=description_core,
                min_len=target_desc_len,
                max_len=max_desc,
                seed_blocks=[
                    " ".join(selected_keywords),
                    " ".join([h.lstrip('#') for h in selected_hashtags]),
                    " ".join(keywords_list),
                ],
            )
            description = _compose_final_description(
                title=title,
                description_core=description_core,
                keywords_csv=keywords_csv,
                hashtags_generated=hashtags_generated,
                referral_link=referral_link,
                include_referral=include_referral,
                max_desc=max_desc,
            )
            tags_csv = _expand_csv_to_min_length(
                base_csv=keywords_csv,
                min_len=target_tags_len,
                max_len=max_tags_length,
                candidate_terms=(
                    keywords_list
                    + selected_keywords
                    + [h.lstrip("#") for h in selected_hashtags]
                    + _collect_terms_from_text(title, limit=40)
                    + _collect_terms_from_text(description_core, limit=160)
                ),
            )
            keywords_csv = _expand_csv_to_min_length(
                base_csv=keywords_csv,
                min_len=max(target_keywords_len, min(desired_keywords_len, YOUTUBE_KEYWORDS_MAX_LEN) - 10),
                max_len=max_tags_length,
                candidate_terms=(
                    keywords_list
                    + selected_keywords
                    + [h.lstrip("#") for h in selected_hashtags]
                    + _collect_terms_from_text(title, limit=60)
                    + _collect_terms_from_text(description_core, limit=220)
                ),
            )
            if len(keywords_csv) < YOUTUBE_KEYWORDS_MIN_LEN:
                keywords_csv = _request_keywords_topup_from_title(
                    llm_cfg=llm_cfg,
                    title=title,
                    current_keywords_csv=keywords_csv,
                    max_len=max_tags_length,
                    min_len=YOUTUBE_KEYWORDS_MIN_LEN,
                    max_attempts=2,
                )
            description = _compose_final_description(
                title=title,
                description_core=description_core,
                keywords_csv=keywords_csv,
                hashtags_generated=hashtags_generated,
                referral_link=referral_link,
                include_referral=include_referral,
                max_desc=max_desc,
            )

            tags_items = [t.strip() for t in tags_csv.split(",") if t.strip()]
            tags_phrases = sum(1 for t in tags_items if " " in t)

            quality_errors = _validate_generation_quality(
                title=title,
                description=description,
                tags_csv=tags_csv,
                keywords_csv=keywords_csv,
                hashtags=hashtags_generated,
                referral_link=referral_link if include_referral else "",
                max_title_len=max_title_len,
                max_desc_len=max_desc,
                max_tags_len=max_tags_length,
                target_tags_len=target_tags_len,
                target_keywords_len=target_keywords_len,
                target_desc_len=target_desc_len,
            )
            if quality_errors:
                raise ValueError(f"Quality check failed: {'; '.join(quality_errors)}")
            _llm_log("Quality check passed")
            _llm_progress("pipeline", 4, 4, "quality ok")
            used_titles.add(title_norm)

            elapsed_total = perf_counter() - attempt_started
            _llm_log(
                f"Generation done in {elapsed_total:.2f}s | title_len={len(title)} | "
                f"description_len={len(description)} | tags_len={len(tags_csv)} | keywords_len={len(keywords_csv)} | hashtags={len(hashtags_generated)}"
            )
            _llm_progress("global", attempt, max_attempts, "done")

            logger.info(
                "LLM metadata generated | model=%s | attempt=%s | sec=%.2f | desc_len=%s | tags_len=%s | keywords_len=%s | hashtags=%s",
                model_name,
                attempt,
                elapsed_total,
                len(description),
                len(tags_csv),
                len(keywords_csv),
                len(hashtags_generated),
            )
            _history_event(
                "llm_generation",
                model=model_name,
                attempt=attempt,
                elapsed_sec=round(elapsed_total, 2),
                description_len=len(description),
                tags_len=len(tags_csv),
                keywords_len=len(keywords_csv),
                hashtags_count=len(hashtags_generated),
                tags_count=len(tags_items),
                tags_phrases=tags_phrases,
            )
            return title, description, tags_csv
        except LLMFatalError as e:
            last_error = str(e)
            logger.warning("LLM fatal error: %s", e)
            _history_event("llm_generation_fatal", attempt=attempt, error=str(e))
            _llm_log(f"Fatal error: {e}")
            _llm_progress("global", attempt, max_attempts, "fatal")
            break
        except Exception as e:
            last_error = str(e)
            logger.warning("LLM generation attempt %s failed: %s", attempt, e)
            _history_event("llm_generation_error", attempt=attempt, error=str(e))
            _llm_log(f"Global attempt {attempt} failed: {e}")
            _llm_progress("global", attempt, max_attempts, "failed")
            if attempt < max_attempts:
                sleep(0.8 * attempt)
            continue

    logger.warning("LLM generation failed after retries. Last error: %s", last_error)
    _history_event("llm_generation_failed", error=last_error)
    _llm_log(f"Generation failed after {max_attempts} attempts: {last_error}")
    return None, None, None


def generate_random_metadata(
    config: Dict,
    original_title: str = "",
    include_referral: bool = True,
    used_title_core_terms: Optional[Set[str]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    return _generate_with_ollama(
        config=config,
        original_title=original_title,
        include_referral=include_referral,
        used_title_core_terms=used_title_core_terms,
    )


def _generate_random_metadata_legacy(
    config: Dict,
    original_title: str = "",
    include_referral: bool = True,
    used_title_core_terms: Optional[Set[str]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Legacy alias kept for compatibility. Uses LLM-only generation."""
    return _generate_with_ollama(
        config=config,
        original_title=original_title,
        include_referral=include_referral,
        used_title_core_terms=used_title_core_terms,
    )


def _detect_brand_from_data_folder(data_folder: str) -> str:
    low = str(data_folder or "").replace("\\", "/").lower()
    if "router-vpn-zm" in low or "keenetic" in low or "router" in low:
        return "@vpnzm_bot"
    if "vpn-raketa" in low or "raketa" in low:
        return "VPN RAKETA"
    if "vpn-groza" in low or "groza" in low:
        return "VPN GROZA"
    if "roblox" in low:
        return "Roblox"
    if "bf6" in low:
        return "Battlefield 6 / BF6"
    if "cs2" in low:
        return "Counter-Strike 2 / CS2"
    return "project"


def _thumbnail_brand_rules(brand: str) -> str:
    brand_low = brand.lower()
    if "@vpnzm_bot" in brand_low:
        return (
            "Бренд/проект: @vpnzm_bot. Тема: VPN на роутере Keenetic через WireGuard. "
            "Визуальные элементы: роутер Keenetic, Wi-Fi, WireGuard, схема подключения, галочка 'Интернет через VPN'. "
            "Не добавляй VPN RAKETA, телефоны как главный объект или мобильный VPN."
        )
    if "raketa" in brand_low:
        return (
            "Бренд/проект: VPN RAKETA. Тема: VPN для телефона, ПК, Android, iPhone, Windows, HAPP/VLESS. "
            "Визуальные элементы: смартфон, ноутбук/ПК, QR-код, замок, молния, приложение. "
            "Можно аккуратно показать бонус +2 дня, если это уместно. Не добавляй Keenetic, WireGuard-роутер или @vpnzm_bot."
        )
    if "groza" in brand_low:
        return (
            "Бренд/проект: VPN GROZA. Тема: VLESS/VPN для Android и iPhone. "
            "Визуальные элементы: смартфон, VLESS-ключ, замок, динамичный контраст. "
            "Не добавляй VPN RAKETA, ZankinMaster, Keenetic или @vpnzm_bot."
        )
    if "roblox" in brand_low:
        return (
            "Проект: Roblox. Тема: подключение и сетевые ошибки Roblox. "
            "Визуальные элементы: игровой интерфейс в стиле Roblox, смартфон/ПК, индикатор подключения. "
            "Не обещай идеальный пинг или гарантированное исправление."
        )
    if "battlefield" in brand_low or "bf6" in brand_low:
        return (
            "Проект: Battlefield 6 / BF6. Тема: гайд, настройки, оружие, FPS или мультиплеер. "
            "Визуальные элементы: динамичный шутерный кадр, солдат, оружие, HUD, FPS."
        )
    if "counter-strike" in brand_low or "cs2" in brand_low:
        return (
            "Проект: Counter-Strike 2 / CS2. Тема: смоки, флешки, прицел, FPS, тактика. "
            "Визуальные элементы: карта, траектория гранаты, прицел, игровая сцена."
        )
    return "Сохраняй соответствие теме, бренду и платформе YouTube. Не смешивай разные бренды."


def _load_thumbnail_prompt_template(config: Dict[str, Any]) -> str:
    image_cfg = config.get("image_generation", {})
    llm_cfg = config.get("llm", {})
    prompts_dir = _resolve_llm_prompts_dir(config, llm_cfg)
    filename = str(image_cfg.get("prompt_file", "stage_thumbnail_image.prompt.txt")).strip()
    path = Path(filename)
    if not path.is_absolute():
        path = (prompts_dir / path).resolve()
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    except Exception as e:
        _llm_log(f"Thumbnail prompt file unavailable ({path}): {e}; using fallback")
    return (
        "Создай промт для генерации YouTube thumbnail 16:9.\n"
        "Верни только финальный промт для изображения, без JSON и пояснений.\n"
        "Тема: {topic}\nБренд: {brand}\nTitle: {title}\nThumbnail text: {thumbnail_text}\n"
        "Brand rules: {brand_rules}\n"
    )


def _build_thumbnail_image_prompt(
    config: Dict[str, Any],
    topic: str,
    brand: str,
    title: str = "",
    thumbnail_text: str = "",
) -> str:
    paths = config.get("paths", {})
    keywords = ""
    description_seed = ""
    try:
        keywords = Path(paths.get("keywords_file", "")).read_text(encoding="utf-8-sig").strip()[:1200]
    except Exception:
        keywords = ""
    try:
        description_seed = Path(paths.get("description_file", "")).read_text(encoding="utf-8-sig").strip()[:1200]
    except Exception:
        description_seed = ""
    template = _load_thumbnail_prompt_template(config)
    prompt = _render_prompt_template(
        template,
        {
            "topic": topic,
            "brand": brand,
            "title": title,
            "thumbnail_text": thumbnail_text,
            "brand_rules": _thumbnail_brand_rules(brand),
            "keywords": keywords,
            "description_seed": description_seed,
        },
    )
    return prompt.strip()


def generate_openai_preview_image(
    config: Dict[str, Any],
    topic: str,
    brand: str = "",
    title: str = "",
    thumbnail_text: str = "",
    dry_run: bool = False,
) -> Dict[str, Any]:
    brand = (brand or _detect_brand_from_data_folder(config.get("paths", {}).get("data_folder", ""))).strip()
    topic = (topic or title or brand or "YouTube preview").strip()
    image_cfg = config.get("image_generation", {})
    out_dir = Path(str(image_cfg.get("output_dir", "generated_previews"))).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(f"{brand}_{topic}")[:80]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prompt = _build_thumbnail_image_prompt(config, topic=topic, brand=brand, title=title, thumbnail_text=thumbnail_text)
    prompt_path = out_dir / f"{stamp}_{slug}.prompt.txt"
    meta_path = out_dir / f"{stamp}_{slug}.json"
    image_path = out_dir / f"{stamp}_{slug}.png"
    prompt_path.write_text(prompt, encoding="utf-8")
    result: Dict[str, Any] = {
        "brand": brand,
        "topic": topic,
        "title": title,
        "thumbnail_text": thumbnail_text,
        "prompt_path": str(prompt_path),
        "image_path": "" if dry_run else str(image_path),
        "dry_run": dry_run,
    }
    if not dry_run:
        image_bytes, api_meta, elapsed = _call_openai_image_generate(prompt, config)
        image_path.write_bytes(image_bytes)
        result.update({"image_path": str(image_path), "api": api_meta, "elapsed_sec": round(elapsed, 2)})
    meta_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["meta_path"] = str(meta_path)
    return result


def prompt_generate_openai_preview(config: Dict[str, Any]) -> None:
    config = prompt_data_folder(config)
    default_brand = _detect_brand_from_data_folder(config.get("paths", {}).get("data_folder", ""))
    topic = input("Тема превью: ").strip()
    title = input("Title/смысл ролика (можно Enter): ").strip()
    thumbnail_text = input("Крупный текст на превью 2-5 слов (можно Enter): ").strip()
    brand = input(f"Бренд [{default_brand}]: ").strip() or default_brand
    dry = input("Dry-run без запроса к OpenAI? (y/N): ").strip().lower() in {"y", "yes", "д", "да"}
    result = generate_openai_preview_image(
        config=config,
        topic=topic,
        brand=brand,
        title=title,
        thumbnail_text=thumbnail_text,
        dry_run=dry,
    )
    print("\n🖼️ Preview generation result:")
    print(f"Бренд: {result['brand']}")
    print(f"Промт: {result['prompt_path']}")
    if result.get("image_path"):
        print(f"Изображение: {result['image_path']}")
    print(f"Метаданные: {result['meta_path']}")

def check_configuration(config_path: str, config_override: Optional[Dict] = None) -> bool:
    print("\n🔍 Checking configuration and required files...")
    try:
        if config_override:
            config = config_override
            print("✓ Using in-memory config (with chosen data folder)")
        else:
            cfg_file = Path(config_path)
            if not cfg_file.exists():
                print(f"✖ Config file not found: {config_path}")
                return False
            config = load_config(str(cfg_file))
            print("✓ Config loaded")

        paths_ok = check_all_paths(config)
        structure_ok = check_config_structure(config)
        if paths_ok and structure_ok:
            print("\n🎉 All checks passed!")
            return True
        print("\n⚠️ Fix the issues above before continuing.")
        return False
    except Exception as e:
        logger.error(f"Config check failed: {e}")
        print(f"✖ Config check failed: {e}")
        return False


def check_all_paths(config: Dict) -> bool:
    print("\n📁 Checking files and folders:")
    ok = True
    folders = [
        (config['paths']['data_folder'], "Data folder"),
        (config['paths']['credentials_folder'], "Credentials folder")
    ]
    for path_str, label in folders:
        p = Path(path_str)
        if p.exists():
            print(f"✓ {label}: {p}")
        else:
            print(f"✖ {label} missing: {p}")
            ok = False

    data_files = [
        (config['paths']['titles_file'], "titles.txt"),
        (config['paths']['keywords_file'], "keywords.txt"),
        (config['paths']['hashtags_file'], "hashtags.txt"),
        (config['paths'].get('description_file', Path(config['paths']['data_folder']) / 'description.txt'), "description.txt"),
    ]
    for path_str, label in data_files:
        p = Path(path_str)
        if p.exists():
            print(f"✓ {label}: {p} ({p.stat().st_size} bytes)")
        else:
            print(f"✖ {label} missing: {p}")
            ok = False

    auth_files = [
        (config['paths']['client_secret_file'], "client_secret.json"),
        (config['paths']['credentials_file'], "credentials.json (created after auth)")
    ]
    for path_str, label in auth_files:
        p = Path(path_str)
        if p.exists():
            print(f"✓ {label}: {p}")
        else:
            if 'credentials.json' in p.name:
                print(f"ℹ️ {label}: will be created on first auth")
            else:
                print(f"✖ {label} missing: {p}")
                ok = False
    return ok


def check_config_structure(config: Dict) -> bool:
    print("\n🧩 Checking config sections:")
    required = ['paths', 'youtube', 'metadata', 'llm', 'image_generation']
    ok = True
    for section in required:
        if section in config:
            print(f"✓ Section '{section}' found")
            if section == 'metadata':
                print(f"   Emojis available: {len(config[section].get('emojis', []))}")
        else:
            print(f"✖ Section '{section}' missing")
            ok = False
    return ok


def test_metadata_generation(config: Dict):
    print("\n🔍 Test metadata generation (config + data folder only)")
    include_referral = prompt_include_referral(config)
    title, description, tags = generate_random_metadata(
        config,
        include_referral=include_referral,
        used_title_core_terms=set(),
    )
    if title and description and tags:
        print(f"📌 Title: {title}")
        print(f"📝 Description (first 200 chars): {description[:200]}...")
        print(f"🏷️ Tags: {tags}")
        print(f"📊 Tags length: {len(tags)}")
    else:
        print("✖ Failed to generate metadata")


def process_playlist_videos(youtube: Any, config: Dict):
    try:
        _history_event("playlist_start")
        playlists_request = youtube.playlists().list(part="snippet", mine=True, maxResults=25)
        playlists_response = playlists_request.execute()
        playlists = playlists_response.get("items", [])
        if not playlists:
            print("✖ No playlists found.")
            _history_event("playlist_empty")
            return

        print("\n📋 Your playlists:")
        for i, playlist in enumerate(playlists, start=1):
            print(f"[{i}] {playlist['snippet']['title']}")

        choice = input("\nEnter playlist number to process: ").strip()
        if not choice.isdigit() or not (1 <= int(choice) <= len(playlists)):
            print("✖ Invalid selection.")
            return

        playlist_id = playlists[int(choice) - 1]['id']
        print(f"Selected playlist: {playlists[int(choice) - 1]['snippet']['title']}")
        _history_event(
            "playlist_selected",
            playlist_id=playlist_id,
            playlist_title=playlists[int(choice) - 1]['snippet']['title'],
        )

        videos = get_videos_from_playlist(youtube, playlist_id)
        total = len(videos)
        processed = 0
        print(f"\n📊 Found {total} videos")
        print("🛠️ Processing from the end of the playlist...")
        _history_event("playlist_videos_loaded", total=total)

        include_referral = prompt_include_referral(config)
        category_id = prompt_category(config)
        language = prompt_language(config)
        used_title_core_terms: Set[str] = set()

        for i, video in enumerate(reversed(videos), 1):
            video_id = video["videoId"]
            status = check_video_status(youtube, video_id)
            if status["status"] != "found":
                print(f"⚠️ Video {video_id} not found or unavailable")
                _history_event("video_skipped_not_found", video_id=video_id)
                continue

            privacy = status["privacyStatus"]
            print(f"ℹ️ Video {i}/{total} ({video_id}) status: {privacy}")

            if privacy == "public":
                print(f"ℹ️ Video {video_id} already public, skipping")
                _history_event("video_skipped_public", video_id=video_id)
                continue

            title, description, tags = generate_random_metadata(
                config,
                video.get("title", ""),
                include_referral=include_referral,
                used_title_core_terms=used_title_core_terms,
            )
            if title and description and tags:
                generated_keywords_csv = tags
                generated_hashtags = _extract_hashtags_from_description(description, max_count=20)
                if ACTIVE_HISTORY_RUN:
                    draft_path = ACTIVE_HISTORY_RUN.save_video_draft(
                        video_id=video_id,
                        title=title,
                        description=description,
                        keywords_csv=generated_keywords_csv,
                        hashtags=generated_hashtags,
                        data_folder=str(config.get("paths", {}).get("data_folder", "")),
                    )
                    _history_event(
                        "video_draft_created",
                        video_id=video_id,
                        draft_file=str(draft_path.relative_to(ACTIVE_HISTORY_RUN.path)),
                        keywords_len=len(generated_keywords_csv),
                        hashtags_count=len(generated_hashtags),
                    )
                    report_path = ACTIVE_HISTORY_RUN.save_video_report(
                        video_id=video_id,
                        source_title=video.get("title", ""),
                        source_description=video.get("description", ""),
                        generated_title=title,
                        generated_description=description,
                        generated_tags_csv=tags,
                        generated_keywords_csv=generated_keywords_csv,
                    )
                    _history_event(
                        "video_report_created",
                        video_id=video_id,
                        report_file=str(report_path.relative_to(ACTIVE_HISTORY_RUN.path)),
                    )

                updated = update_video_metadata(
                    youtube,
                    video_id,
                    title,
                    description,
                    tags,
                    category_id=category_id,
                    language=language,
                    max_tags_length=config['youtube'].get('max_tags_length', 500),
                    max_title_length=config['youtube'].get('max_title_length', 120),
                    fallback_title=video.get("title", ""),
                )
                if updated:
                    print(f"✅ Updated and published: {video_id}")
                    _history_event("video_updated", video_id=video_id, processed=processed + 1)
                    processed += 1
                else:
                    print(f"✖ Update failed: {video_id}")
                    _history_event("video_update_failed", video_id=video_id)
                if processed > 0 and processed % 20 == 0:
                    print(f"\n🏅 Processed {processed} videos")
                    cont = input("Continue? (y/n): ").strip().lower()
                    if cont not in ['y', 'yes', 'д', 'да', '']:
                        print(f"⏹️ Stopped by user. Processed {processed} videos")
                        _history_event("playlist_stopped_by_user", processed=processed)
                        return
            else:
                print(f"⚠️ Could not generate metadata for {video_id}")
                _history_event("video_metadata_failed", video_id=video_id)

        print(f"\n🎉 Done! Updated {processed} of {total} videos")
        _history_event("playlist_done", processed=processed, total=total)

    except HttpError as e:
        logger.error(f"Playlist processing error: {e}")
        print(f"✖ Playlist processing error: {e}")
        _history_event("playlist_error", error=str(e))


def get_videos_from_playlist(youtube: Any, playlist_id: str, max_results: int = 50) -> List[Dict]:
    try:
        videos: List[Dict] = []
        next_token = None
        while True:
            request = youtube.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=max_results,
                pageToken=next_token
            )
            response = request.execute()
            for item in response.get("items", []):
                videos.append({
                    "videoId": item["snippet"]["resourceId"]["videoId"],
                    "title": item["snippet"]["title"],
                    "description": item["snippet"].get("description", "")
                })
            next_token = response.get("nextPageToken")
            if not next_token:
                break
        logger.info(f"Fetched {len(videos)} videos from playlist {playlist_id}")
        return videos
    except HttpError as e:
        logger.error(f"Error getting videos: {e}")
        return []


def check_video_status(youtube: Any, video_id: str) -> Dict[str, str]:
    try:
        response = youtube.videos().list(part="status", id=video_id).execute()
        if not response["items"]:
            logger.warning(f"Video {video_id} not found")
            return {"status": "not_found", "privacyStatus": "N/A"}
        status = response["items"][0]["status"]["privacyStatus"]
        return {"status": "found", "privacyStatus": status}
    except HttpError as e:
        logger.error(f"Status check error for {video_id}: {e}")
        return {"status": "error", "privacyStatus": "N/A"}


def update_video_metadata(
    youtube: Any,
    video_id: str,
    title: str,
    description: str,
    tags: str,
    category_id: Optional[str] = None,
    language: Optional[str] = None,
    max_tags_length: int = 500,
    max_title_length: int = 120,
    fallback_title: str = "",
) -> bool:
    def _sanitize_tag_list(
        raw_tags: List[str],
        max_total: int = 500,
        max_each: int = 60,
        max_count: int = 50,
    ) -> List[str]:
        cleaned: List[str] = []
        seen = set()
        total_len = 0
        for raw_tag in raw_tags:
            tag = raw_tag
            tag = tag.strip()
            tag = re.sub(r"[\"']", "", tag)
            tag = re.sub(r"[^\w\s\-]", "", tag, flags=re.UNICODE)
            tag = re.sub(r"\s{2,}", " ", tag).strip()
            if not tag or len(tag) < 2:
                continue
            tag = tag.strip("-_ ")
            if not tag:
                continue
            if len(tag) > max_each:
                tag = tag[:max_each]
            if tag in seen:
                continue
            if len(cleaned) >= max_count:
                break
            add_len = len(tag) + (2 if cleaned else 0)
            if total_len + add_len > max_total:
                continue
            cleaned.append(tag)
            seen.add(tag)
            total_len += add_len
        return cleaned

    try:
        safe_title = _sanitize_title_for_youtube(
            title=title,
            max_len=min(max_title_length, YOUTUBE_HARD_TITLE_LIMIT),
            fallback_title=fallback_title,
        )
        if safe_title != (title or "").strip():
            _history_event(
                "title_sanitized",
                video_id=video_id,
                original_len=len(title or ""),
                sanitized_len=len(safe_title),
            )
        body = {
            "id": video_id,
            "snippet": {
                "title": safe_title,
                "description": description,
                "categoryId": category_id or "20",
            },
            "status": {"privacyStatus": "public"}
        }
        if language:
            # Keep both metadata and audio language aligned to avoid YouTube
            # showing a different language in UI.
            body["snippet"]["defaultLanguage"] = language
            body["snippet"]["defaultAudioLanguage"] = language
        if tags:
            tags_list = _sanitize_tag_list(
                [t.strip() for t in tags.split(',') if t.strip()],
                max_total=min(max_tags_length, YOUTUBE_KEYWORDS_MAX_LEN),
            )
            body["snippet"]["tags"] = tags_list
        youtube.videos().update(part="snippet,status", body=body).execute()
        _history_event(
            "youtube_update_success",
            video_id=video_id,
            title_len=len(safe_title),
            description_len=len(description),
            tags_count=len(body["snippet"].get("tags", [])),
        )
        return True
    except HttpError as e:
        logger.error(
            "Update error for %s: %s | tags=%s",
            video_id,
            e,
            body["snippet"].get("tags", [])
        )
        _history_event("youtube_update_error", video_id=video_id, error=str(e))
        return False


def clear_data(credentials_folder: str):
    try:
        for file_path in Path(credentials_folder).glob("*.json"):
            file_path.unlink()
        print("✓ Auth data cleared")
    except Exception as e:
        logger.error(f"Clear data error: {e}")
        print(f"✖ Clear data error: {e}")


def exit_launcher():
    print("👋 Exit")
    return


def _resolve_config_path() -> Path:
    cwd_config = Path.cwd() / 'config' / 'config.json'
    script_config = Path(__file__).resolve().parent / 'config' / 'config.json'
    return cwd_config if cwd_config.exists() else script_config


def _run_quality_menu_action(choice: int) -> None:
    from quality_checks import (
        check_environment,
        check_prompts,
        check_youtube_seo,
        list_functions,
        print_json,
        run_smoke,
        run_test,
        scan_projects,
    )

    actions = {
        6: lambda: check_environment(),
        7: lambda: scan_projects(),
        8: lambda: check_prompts(),
        9: lambda: check_youtube_seo(),
        10: lambda: run_smoke(),
        11: lambda: run_test("vpn_raketa"),
        12: lambda: run_test("router"),
        13: lambda: run_test("mixed"),
        14: lambda: {"functions": list_functions()},
    }
    print_json(actions[choice]())


def main() -> int:
    config_path = _resolve_config_path()

    while True:
        print("\n🛰️ YouTube API Launcher")
        for key, value in MENU_OPTIONS.items():
            print(f"[{key}] {value}")

        choice = input("\nEnter action number: ").strip()
        if not choice.isdigit() or int(choice) not in MENU_OPTIONS:
            print("✖ Invalid choice. Try again.")
            continue

        choice = int(choice)
        config = load_config(str(config_path)) if config_path.exists() else None

        if choice == 1:
            if not config:
                print("✖ Config not found")
                continue
            _start_history_run("check_configuration", config)
            try:
                config_for_check = prompt_data_folder(config)
                _history_event("config_check_selected_folder", folder=config_for_check.get("paths", {}).get("data_folder", ""))
                check_configuration(str(config_path), config_override=config_for_check)
                _finish_history_run("completed")
            except Exception as e:
                _history_event("run_exception", error=str(e))
                _finish_history_run("failed")
                print(f"✖ Ошибка запуска: {e}")
        elif choice == 2:
            if not config:
                print("✖ Config not found")
                continue
            _start_history_run("process_playlist_videos", config)
            try:
                config = prompt_data_folder(config)
                _history_event("playlist_selected_folder", folder=config.get("paths", {}).get("data_folder", ""))
                youtube = authenticate_youtube(config)
                if youtube:
                    _history_event("youtube_auth", status="ok")
                    process_playlist_videos(youtube, config)
                    _finish_history_run("completed")
                else:
                    _history_event("youtube_auth", status="failed")
                    _finish_history_run("failed")
            except Exception as e:
                _history_event("run_exception", error=str(e))
                _finish_history_run("failed")
                print(f"✖ Ошибка запуска: {e}")
        elif choice == 3:
            if not config:
                print("✖ Config not found")
                continue
            _start_history_run("clear_data", config)
            try:
                _history_event("clear_data_start", folder=config['paths']['credentials_folder'])
                clear_data(config['paths']['credentials_folder'])
                _history_event("clear_data_done")
                _finish_history_run("completed")
            except Exception as e:
                _history_event("run_exception", error=str(e))
                _finish_history_run("failed")
                print(f"✖ Ошибка запуска: {e}")
        elif choice == 4:
            if not config:
                print("✖ Config not found")
                continue
            _start_history_run("test_metadata_generation", config)
            try:
                config = prompt_data_folder(config)
                _history_event("test_selected_folder", folder=config.get("paths", {}).get("data_folder", ""))
                test_metadata_generation(config)
                _finish_history_run("completed")
            except Exception as e:
                _history_event("run_exception", error=str(e))
                _finish_history_run("failed")
                print(f"✖ Ошибка запуска: {e}")
        elif choice == 5:
            if not config:
                print("✖ Config not found")
                continue
            _start_history_run("generate_openai_preview", config)
            try:
                prompt_generate_openai_preview(config)
                _finish_history_run("completed")
            except Exception as e:
                _history_event("run_exception", error=str(e))
                _finish_history_run("failed")
                print(f"✖ Ошибка генерации превью: {e}")
        elif choice == 6:
            _run_quality_menu_action(choice)
        elif choice == 7:
            _run_quality_menu_action(choice)
        elif choice == 8:
            _run_quality_menu_action(choice)
        elif choice == 9:
            _run_quality_menu_action(choice)
        elif choice == 10:
            _run_quality_menu_action(choice)
        elif choice == 11:
            _run_quality_menu_action(choice)
        elif choice == 12:
            _run_quality_menu_action(choice)
        elif choice == 13:
            _run_quality_menu_action(choice)
        elif choice == 14:
            _run_quality_menu_action(choice)
        elif choice == 15:
            exit_launcher()
            return 0


def _cli_value(args: List[str], name: str, default: str = "") -> str:
    if name not in args:
        return default
    idx = args.index(name)
    if idx + 1 >= len(args):
        return default
    return args[idx + 1]


def _run_preview_cli(args: List[str]) -> int:
    config_path = _resolve_config_path()
    if not config_path.exists():
        print("✖ Config not found")
        return 1
    config = load_config(str(config_path))
    data_folder = _cli_value(args, "--data-folder")
    if data_folder:
        folder = Path(data_folder)
        if not folder.is_absolute():
            folder = (Path.cwd() / folder).resolve()
        config["paths"]["data_folder"] = str(folder)
        config["paths"]["titles_file"] = str(folder / "titles.txt")
        config["paths"]["keywords_file"] = str(folder / "keywords.txt")
        config["paths"]["hashtags_file"] = str(folder / "hashtags.txt")
        config["paths"]["description_file"] = str(folder / "description.txt")
    topic = _cli_value(args, "--preview-topic") or _cli_value(args, "--topic")
    title = _cli_value(args, "--preview-title") or _cli_value(args, "--title")
    thumbnail_text = _cli_value(args, "--thumbnail-text")
    brand = _cli_value(args, "--preview-brand") or _cli_value(args, "--brand")
    dry_run = "--dry-run" in args
    if not topic and not title:
        print("✖ Укажите --preview-topic или --preview-title")
        return 1
    result = generate_openai_preview_image(
        config=config,
        topic=topic,
        brand=brand,
        title=title,
        thumbnail_text=thumbnail_text,
        dry_run=dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _print_launcher_help() -> None:
    print(
        "YouTube API Launcher\n\n"
        "Основной запуск:\n"
        "  python launcher.py\n\n"
        "Генерация превью через OpenAI Image API:\n"
        "  python launcher.py --generate-preview --data-folder data/router-vpn-zm --preview-topic \"VPN для Keenetic\" --thumbnail-text \"Keenetic + VPN\"\n"
        "  python launcher.py --generate-preview --data-folder data/vpn-raketa --preview-topic \"VPN для Android и iPhone\" --thumbnail-text \"VPN за 5 минут\"\n"
        "  python launcher.py --generate-preview --preview-topic \"CS2 смоки\" --thumbnail-text \"CS2 гайд\" --dry-run\n\n"
        "Параметры превью:\n"
        "  --generate-preview        запустить генерацию превью\n"
        "  --data-folder PATH        data-pack бренда/проекта\n"
        "  --preview-topic TEXT      тема превью\n"
        "  --preview-title TEXT      title или смысл ролика\n"
        "  --thumbnail-text TEXT     крупный текст на превью 2-5 слов\n"
        "  --preview-brand TEXT      бренд, если нужно переопределить автовыбор\n"
        "  --dry-run                 создать только prompt/json без запроса к OpenAI\n\n"
        "QA-команды:\n"
        "  --check-config, --check-env, --check-prompts, --scan-projects, --check-youtube-seo,\n"
        "  --test vpn_raketa|router|mixed|vpn_groza|roblox|bf6|cs2, --smoke\n\n"
        "В корне проекта оставлен один launcher-файл: launcher.py.\n"
    )


if __name__ == "__main__":
    qa_args = {
        "--scan-projects",
        "--check-prompts",
        "--test-all-projects",
        "--test",
        "--check-youtube-seo",
        "--report-md",
        "--report-json",
        "--smoke",
        "--check-config",
        "--list-functions",
        "--check-env",
        "--save-output",
        "--save-json",
        "--debug",
    }
    preview_args = {
        "--generate-preview",
        "--preview-topic",
        "--topic",
        "--preview-title",
        "--title",
        "--thumbnail-text",
        "--preview-brand",
        "--brand",
        "--data-folder",
        "--dry-run",
    }
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        _print_launcher_help()
        raise SystemExit(0)
    if "--generate-preview" in sys.argv[1:] or any(arg in preview_args for arg in sys.argv[1:]):
        raise SystemExit(_run_preview_cli(sys.argv[1:]))
    if any(arg in qa_args for arg in sys.argv[1:]):
        from quality_checks import main as qa_main

        raise SystemExit(qa_main(sys.argv[1:]))
    raise SystemExit(main())
