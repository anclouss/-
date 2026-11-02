# Аудит и миграция текущего проекта

Аудит выполнен до изменения приложения. Обновление выполнено в существующем checkout; отдельный новый проект и Git worktree не создавались. Исходный baseline: 92 backend-теста и 3 subtests прошли; production build с TypeScript прошёл. Итоговые результаты и ограничения записаны в [VALIDATION.md](VALIDATION.md).

## Подтверждённые проблемы

| Область | Исходное поведение | Изменение архитектуры |
| --- | --- | --- |
| Монтаж | Save перезаписывал клип и сразу ставил полный render | Отдельный EDL, autosave, явные preview/export |
| История | Unsaved state жил только в React, Undo/Redo отсутствовали | Persistent snapshots, CAS revision, Undo/Redo/Reset |
| Готовые файлы | Старый MP4 становился недоступен во время повторного render | Отдельные export jobs и сохранение старых результатов |
| Retry | Анализ удалял прежние клипы и MP4 после нового отбора | Переиспользование подходящего отбора, сохранение ручных правок |
| Медиа | Только 1080×1920 / 30 FPS / H.264 | EDL renderer, proxy, качества и отдельный Export |
| Синхронизация | Source-span duration показывался после удаления пауз | Явные сегменты, output duration и remap слов |
| Субтитры | Дополнительные 0.15с могли наложить соседние фразы | Ограничение конца фразы началом следующей |
| Отмена | Blocking FFmpeg без cancel; upload продолжался после ухода | Проверки отмены, прекращение subprocess, abortable UI |
| Upload | MAX_BYTES проверялся после multipart-spooling | Ограничение body до parsing плюс проверка файла |
| API | Список проектов делал N+1 reads полных клипов | Агрегированные counts, компактные ответы, transcript pages |
| Frontend | Монолитный App, перекрывающиеся polling-запросы | Pages/editor/hooks, отменяемые неперекрывающиеся запросы |
| Транскрипция | Все слова клипа отображались и перерисовывались разом | Пагинация/поиск и ограниченное окно слов для preview |
| Конфигурация | Относительный DATA_DIR зависел от cwd | Разрешение относительно корня проекта |
| AI | Нет intent/tools, контекста playhead, cache и proposals | Restricted JSON actions, retrieval, сохранённый chat |
| Зависимости | PyAV19 ломал API установленного faster-whisper | Закреплённая PyAV18 и настоящий decode-regression сохраняются |

## Выполненный cleanup

До миграции подтверждены неиспользуемые импорты `re` в backend `main.py`, `signal` в `worker.py`, `ExternalLink` в старом frontend `App.tsx`. В CSS были неиспользуемые селекторы и несколько перекрывающих слоёв старой композиции.

Отдельных доказанно лишних исходных файлов не найдено. Windows-launcher, speech check/repair, standalone uninstaller, Bash/cloud scripts и их ZIP используются в документации и сохраняются. Удалять их ради меньшего количества файлов нельзя.

Старый inline-интерфейс и его CSS заменены в активных `App.tsx` / `style.css` и выделенных modules. Неиспользуемые импорты удалены. Нет `ComponentNew`, `Final2`, backup-копий страниц или второй параллельной точки входа. Самостоятельные исходные файлы не удалялись; launcher, диагностика речи, исправление и удаление приложения сохранены.

## Новые границы модулей

- Чистый `editing.py` отвечает за схему и разрешённые actions; не имеет shell/model/filesystem исполнения.
- `editor_store.py` отвечает за документы, ревизии и историю.
- `editor_ai.py` хранит сообщения/предложения, выбирает контекст и вызывает LLM.
- `editor_jobs.py` отвечает за snapshot/cache/cancel/retry.
- `editor_media.py` выполняет FFmpeg, proxy и waveform.
- `editor_api.py` связывает HTTP с этими сервисами, не превращает свободный текст в команды ОС.
- Существующие `ai.py`, `media.py`, проекты, настройки и STT сохраняют совместимые обязанности.

## Этапы и проверки

| Этап | Изменение | Обязательная проверка |
| --- | --- | --- |
| 1 Audit & Cleanup | Зависимости, API/worker/frontend/media, unused references | Исходные 92 tests + 3 subtests и production build прошли |
| 2 Architecture Refactor | EDL, lazy migration, CAS, snapshots | Unit/store tests на границы, stale revision, Undo/Redo/Reset |
| 3 New UI | Новый shell/pages, компактные панели и состояния | Typecheck, lint, build, mobile/desktop smoke |
| 4 Video Editor | Source/proxy playback, свойства, autosave | Изменение документа без запуска render, восстановление после reload |
| 5 AI Chat | Retrieval, schema, proposals, Apply/Cancel/cache | Мок внешней LLM при настоящей БД/actions; invalid tool и stale/cancel tests |
| 6 Timeline | Playhead, selection, cuts/segments, waveform/captions | Сопоставление source/output time, выбор/trim/delete и визуальный smoke |
| 7 Export & Quality | Snapshot jobs, presets/advanced, quality | Настоящий FFmpeg на коротких fixtures, probe размеров/FPS/звука |
| 8 Performance | Bounded responses, polling, cache, no render per change | Cache reuse, bounded transcript, отсутствие конкурирующего autosave |
| 9 Bug Fixing | Upload/cancel/retry/delete, subtitle remap | Интеграционные гонки, Range/ZIP и сохранение исходника |
| 10 Testing & Final Cleanup | Единый активный UI и актуальные docs | Полный набор, lint/typecheck/build, browser и финальный Git diff |

Все десять этапов реализованы; таблица сохраняет критерии их проверки. Результаты полного набора, настоящего FFmpeg, браузера и фонового экспорта приведены в [VALIDATION.md](VALIDATION.md). Качество настоящих Whisper/LLM-моделей, выполнение на Windows и длительная нагрузка отдельно отмечены как непроверенные.

## План миграции установки

1. Остановить API и worker; сохранить `.env` и весь `data`.
2. Заменить исходные файлы новой версией в той же папке, сохранив окружение и данные. Не выполнять reset/удаление source-файлов.
3. Запустить обычный launcher: он обновит зависимости/сборку по изменившимся файлам. Backend добавит таблицы/колонки, старые записи не сбрасываются.
4. При первом открытии старого клипа создать EDL из его source-range/settings и сохранить существующий MP4.
5. Проверить один старый проект: download старого файла, autosave, Undo/Redo, новый отдельный Export.

Особый старый случай: `remove_silence=true` не сохранял карту вырезанных пауз. Такой старый MP4 остаётся целым, а новый редактируемый документ начинается с исходного диапазона; точную прежнюю карту cuts нельзя честно восстановить из одного флага. Новая команда удаления пауз создаёт реальные сегменты.
