# Prompt Stages

Файлы в этой папке определяют 4 этапа генерации метаданных.

## Файлы
- `stage_title.prompt.txt` — только заголовок (`{"title":""}`).
- `stage_description.prompt.txt` — только core-описание (`{"description":""}`).
- `stage_hashtags_keys.prompt.txt` — хештеги и промежуточные keys (`{"hashtags":[], "keys":[]}`).
- `stage_keywords.prompt.txt` — финальные keywords (`{"keywords":[]}`).

## Плейсхолдеры
Разрешенные плейсхолдеры подставляются автоматически:
- Общие: `{title}`, `{keywords}`, `{max_tags_length}`
- Stage title: `{original_title}`, `{title_candidate}`, `{title_samples}`, `{max_title_length}`
- Stage description: `{target_description_min}`, `{max_description_length}`, `{referral_link_hint}`, `{description_seed}`
- Stage hashtags+keys: `{description_excerpt}`, `{hashtags_seed}`, `{max_hashtags}`
- Stage keywords: `{description_excerpt}`, `{hashtags}`, `{keys_seed}`, `{target_keywords_min}`

## Правила редактирования
- Сохраняйте требование `Верни строго JSON` для каждого этапа.
- Не меняйте ожидаемые ключи JSON.
- Изменяйте условия длины текста внутри промтов, если нужно ужесточить/ослабить поведение модели.
