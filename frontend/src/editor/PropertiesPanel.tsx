import type { DocumentState, Segment } from "../types/editor";
import { time } from "../api";
export function PropertiesPanel({
  document,
  selected,
  onChange,
  disabled,
}: {
  document: DocumentState;
  selected: Segment | undefined;
  onChange: (next: DocumentState, label?: string) => void;
  disabled: boolean;
}) {
  const settings = document.settings;
  const change = (values: Partial<DocumentState["settings"]>) =>
    onChange(
      { ...document, settings: { ...settings, ...values } },
      "Оформление и звук",
    );
  const segment = (values: Partial<Segment>) => {
    if (selected)
      onChange(
        {
          ...document,
          segments: document.segments.map((item) =>
            item.id === selected.id ? { ...item, ...values } : item,
          ),
        },
        "Изменение фрагмента",
      );
  };
  return (
    <fieldset className="properties-body" disabled={disabled}>
      <section className="property-section">
        <h3>Композиция</h3>
        <label className="field">
          Раскладка
          <select
            className="select"
            value={settings.layout}
            onChange={(event) =>
              change({ layout: event.target.value as typeof settings.layout })
            }
          >
            <option value="crop">Заполнить кадр</option>
            <option value="fit">Вписать целиком</option>
            <option value="split">Лицо сверху · игра снизу</option>
          </select>
        </label>
        <label className="field">
          Соотношение сторон
          <select
            className="select"
            value={settings.aspect_ratio}
            onChange={(event) =>
              change({
                aspect_ratio: event.target
                  .value as typeof settings.aspect_ratio,
              })
            }
          >
            <option>9:16</option>
            <option>16:9</option>
            <option>1:1</option>
            <option>4:5</option>
          </select>
        </label>
        <Range
          label="Центр X"
          value={settings.crop_x}
          min={0}
          max={1}
          step={0.01}
          onChange={(crop_x) => change({ crop_x })}
        />
        <Range
          label="Центр Y"
          value={settings.crop_y}
          min={0}
          max={1}
          step={0.01}
          onChange={(crop_y) => change({ crop_y })}
        />
        {settings.layout === "split" && (
          <div className="property-grid">
            {["Камера X", "Камера Y", "Ширина", "Высота"].map(
              (label, index) => (
                <label className="field" key={label}>
                  {label}
                  <input
                    className="input"
                    type="number"
                    step={0.01}
                    min={index < 2 ? 0 : 0.05}
                    max={1}
                    value={settings.face_box[index]}
                    onChange={(event) => {
                      const face_box = [...settings.face_box];
                      face_box[index] = Number(event.target.value);
                      change({ face_box });
                    }}
                  />
                </label>
              ),
            )}
          </div>
        )}
        <Toggle
          label="Найти лицо"
          checked={settings.face_tracking}
          onChange={(face_tracking) => change({ face_tracking })}
        />
        <p className="hint">
          Точное кадрирование по лицу проверяется в обработанном preview.
        </p>
      </section>
      <section className="property-section">
        <h3>Субтитры</h3>
        <Toggle
          label="Включены"
          checked={settings.subtitles}
          onChange={(subtitles) => change({ subtitles })}
        />
        <Toggle
          label="Подсветка слова"
          checked={settings.subtitle_highlight}
          onChange={(subtitle_highlight) => change({ subtitle_highlight })}
        />
        <div className="property-grid">
          <label className="field">
            Шрифт
            <select
              className="select"
              value={settings.subtitle_font}
              onChange={(event) =>
                change({ subtitle_font: event.target.value })
              }
            >
              <option>Arial</option>
              <option>DejaVu Sans</option>
              <option>Verdana</option>
            </select>
          </label>
          <label className="field">
            Цвет
            <input
              className="input"
              type="color"
              value={settings.subtitle_color}
              onChange={(event) =>
                change({ subtitle_color: event.target.value })
              }
            />
          </label>
        </div>
        <Range
          label="Размер"
          value={settings.subtitle_size}
          min={24}
          max={120}
          step={1}
          onChange={(subtitle_size) => change({ subtitle_size })}
        />
        <Range
          label="Положение"
          value={settings.subtitle_position}
          min={0.1}
          max={0.9}
          step={0.01}
          onChange={(subtitle_position) => change({ subtitle_position })}
        />
      </section>
      <section className="property-section">
        <h3>Звук и переходы</h3>
        <Toggle
          label="Нормализовать речь"
          checked={settings.normalize_audio}
          onChange={(normalize_audio) => change({ normalize_audio })}
        />
        <Range
          label="Громкость"
          value={settings.volume}
          min={0}
          max={3}
          step={0.05}
          onChange={(volume) => change({ volume })}
        />
        <Toggle
          label="Плавные переходы"
          checked={document.transition.type === "fade"}
          onChange={(enabled) =>
            onChange(
              {
                ...document,
                transition: { type: enabled ? "fade" : "none", duration: 0.2 },
              },
              "Переходы",
            )
          }
        />
      </section>
      {selected && (
        <section className="property-section">
          <h3>Выбранный фрагмент</h3>
          <p className="hint">
            Исходник {time(selected.start, true)} — {time(selected.end, true)}
          </p>
          <Range
            label="Скорость"
            value={selected.speed}
            min={0.25}
            max={4}
            step={0.05}
            onChange={(speed) => segment({ speed })}
          />
          <Range
            label="Приближение"
            value={selected.effects?.zoom || 1}
            min={1}
            max={3}
            step={0.05}
            onChange={(zoom) =>
              segment({ effects: { ...selected.effects, zoom } })
            }
          />
          <label className="field">
            Текст поверх видео
            <input
              className="input"
              maxLength={200}
              value={selected.effects?.text || ""}
              onChange={(event) =>
                segment({
                  effects: { ...selected.effects, text: event.target.value },
                })
              }
            />
          </label>
        </section>
      )}
    </fieldset>
  );
}
export function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="switch-row">
      <span>{label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
    </label>
  );
}
function Range({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="range-row">
      <span>
        {label}
        <output>{Number(value.toFixed(2))}</output>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}
