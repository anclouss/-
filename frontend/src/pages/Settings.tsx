import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Check, RefreshCw, Save, Trash2 } from "lucide-react";
import { api, message } from "../api";
import type { Settings as SettingsValue } from "../api";
import { useResource } from "../hooks/useResource";
import { ErrorBanner, PageHeader, Skeleton } from "../ui";
export function SettingsPage() {
  const { data, setData, loading, error, refresh } =
    useResource<SettingsValue>("/settings");
  const [form, setForm] = useState<SettingsValue | null>(null),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [actionError, setActionError] = useState("");
  useEffect(() => {
    if (data) setForm(data);
  }, [data]);
  const update = (values: Partial<SettingsValue>) => {
    setForm((current) => (current ? { ...current, ...values } : current));
    setNotice("");
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!form) return;
    setBusy(true);
    setActionError("");
    try {
      const values = {
        transcription_provider: form.transcription_provider,
        whisper_model: form.whisper_model,
        analysis_provider: form.analysis_provider,
        openai_model: form.openai_model,
        ollama_model: form.ollama_model,
        max_clips: form.max_clips,
        retention_days: form.retention_days,
        language: form.language,
      };
      const value = await api<Partial<SettingsValue>>("/settings", {
        method: "PATCH",
        body: JSON.stringify(values),
      });
      setData({ ...form, ...value });
      setNotice("Настройки сохранены.");
    } catch (failure) {
      setActionError(message(failure));
    } finally {
      setBusy(false);
    }
  };
  const cleanup = async () => {
    if (
      !window.confirm(
        "Удалить старые исходники? Готовые экспорты сохранятся, повторный монтаж без исходников недоступен.",
      )
    )
      return;
    setBusy(true);
    try {
      const value = await api<{ deleted: number }>("/storage/sources", {
        method: "DELETE",
      });
      setNotice(`Удалено исходников: ${value.deleted}.`);
    } catch (failure) {
      setActionError(message(failure));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHeader
        title="Настройки"
        description="Модели, хранение и состояние локального сервера."
      >
        <button className="button secondary" onClick={() => void refresh()}>
          <RefreshCw size={15} />
          Проверить
        </button>
      </PageHeader>
      <ErrorBanner error={error || actionError} />
      {notice && (
        <div className="notice" role="status">
          <Check size={15} />
          {notice}
        </div>
      )}
      {loading ? (
        <Skeleton />
      ) : (
        form && (
          <form onSubmit={save}>
            <fieldset disabled={busy} className="settings-fieldset">
              <div className="settings-grid">
                <section className="panel settings-section">
                  <div className="panel-heading">
                    <h2>Распознавание речи</h2>
                  </div>
                  <div className="panel-body">
                    <label className="field">
                      Провайдер
                      <select
                        className="select"
                        value={form.transcription_provider}
                        onChange={(event) =>
                          update({
                            transcription_provider: event.target
                              .value as SettingsValue["transcription_provider"],
                          })
                        }
                      >
                        <option value="local">Локально · faster-whisper</option>
                        <option value="openai">OpenAI · платный API</option>
                      </select>
                    </label>
                    {form.transcription_provider === "local" ? (
                      <>
                        <label className="field">
                          Модель
                          <select
                            className="select"
                            value={form.whisper_model}
                            onChange={(event) =>
                              update({ whisper_model: event.target.value })
                            }
                          >
                            {[
                              "tiny",
                              "base",
                              "small",
                              "medium",
                              "large-v3",
                              "turbo",
                            ].map((value) => (
                              <option key={value}>{value}</option>
                            ))}
                          </select>
                        </label>
                        <p className="hint">
                          Для CPU начните с base или small. Модели скачиваются
                          при первом использовании.
                        </p>
                      </>
                    ) : (
                      <p className="hint">
                        {form.providers.openai
                          ? "Ключ задан на сервере."
                          : "Добавьте OPENAI_API_KEY в серверный .env и перезапустите приложение."}
                      </p>
                    )}
                    <label className="field">
                      Язык
                      <select
                        className="select"
                        value={form.language}
                        onChange={(event) =>
                          update({ language: event.target.value })
                        }
                      >
                        <option value="auto">Автоматически</option>
                        <option value="ru">Русский</option>
                        <option value="en">Английский</option>
                        <option value="uk">Украинский</option>
                      </select>
                    </label>
                  </div>
                </section>
                <section className="panel settings-section">
                  <div className="panel-heading">
                    <h2>AI-чат и поиск моментов</h2>
                  </div>
                  <div className="panel-body">
                    <label className="field">
                      Провайдер
                      <select
                        className="select"
                        value={form.analysis_provider}
                        onChange={(event) =>
                          update({
                            analysis_provider: event.target
                              .value as SettingsValue["analysis_provider"],
                          })
                        }
                      >
                        <option value="ollama">Ollama · локально</option>
                        <option value="openai">OpenAI · платный API</option>
                        <option value="heuristic">Эвристики · без LLM</option>
                      </select>
                    </label>
                    {form.analysis_provider === "ollama" ? (
                      <>
                        <label className="field">
                          Модель Ollama
                          <input
                            className="input"
                            value={form.ollama_model}
                            required
                            onChange={(event) =>
                              update({ ollama_model: event.target.value })
                            }
                          />
                        </label>
                        <p className="hint">
                          {form.providers.ollama
                            ? "Сервер Ollama доступен."
                            : "Запустите Ollama с выбранной моделью."}{" "}
                          Модель: <code>ollama pull {form.ollama_model}</code>
                        </p>
                      </>
                    ) : form.analysis_provider === "openai" ? (
                      <label className="field">
                        Модель OpenAI
                        <input
                          className="input"
                          value={form.openai_model}
                          required
                          onChange={(event) =>
                            update({ openai_model: event.target.value })
                          }
                        />
                      </label>
                    ) : (
                      <p className="hint">
                        Эвристики не заменяют понимание команды LLM. Для
                        естественного языка выберите Ollama или OpenAI.
                      </p>
                    )}
                    <label className="field">
                      Моментов на проект
                      <input
                        className="input"
                        type="number"
                        min={1}
                        max={20}
                        required
                        value={form.max_clips}
                        onChange={(event) =>
                          update({ max_clips: Number(event.target.value) })
                        }
                      />
                    </label>
                  </div>
                </section>
                <section className="panel settings-section">
                  <div className="panel-heading">
                    <h2>Хранение</h2>
                  </div>
                  <div className="panel-body">
                    <label className="field">
                      Хранить исходники, дней
                      <input
                        className="input"
                        type="number"
                        min={1}
                        max={365}
                        required
                        value={form.retention_days}
                        onChange={(event) =>
                          update({ retention_days: Number(event.target.value) })
                        }
                      />
                    </label>
                    <p className="hint">
                      После очистки исходника экспортированные MP4 сохраняются.
                      Повторный монтаж требует исходный файл.
                    </p>
                    <button
                      type="button"
                      className="button danger"
                      onClick={() => void cleanup()}
                    >
                      <Trash2 size={14} />
                      Очистить старые исходники
                    </button>
                  </div>
                </section>
                <section className="panel settings-section">
                  <div className="panel-heading">
                    <h2>Окружение</h2>
                  </div>
                  <div className="panel-body">
                    <div className="settings-status">
                      <span>FFmpeg</span>
                      <span className="badge">
                        {form.providers.ffmpeg ? "Доступен" : "Не найден"}
                      </span>
                    </div>
                    <div className="settings-status">
                      <span>Ollama</span>
                      <span className="badge">
                        {form.providers.ollama ? "Доступна" : "Не запущена"}
                      </span>
                    </div>
                    <div className="settings-status">
                      <span>Лимит файла</span>
                      <span>{form.max_upload_gb} ГБ</span>
                    </div>
                    <p className="hint">
                      Ключи хранятся в .env сервера. Не добавляйте их в браузер
                      и клиентскую сборку.
                    </p>
                  </div>
                </section>
              </div>
              <div className="settings-footer">
                <button className="button primary" type="submit">
                  <Save size={15} />
                  Сохранить настройки
                </button>
              </div>
            </fieldset>
          </form>
        )
      )}
    </>
  );
}
