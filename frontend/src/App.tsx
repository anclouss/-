import type { MouseEvent } from "react";
import { Clapperboard, FolderOpen, Plus, Settings2 } from "lucide-react";
import {
  Link,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { Upload } from "./pages/Upload";
import { Processing, Results } from "./pages/Projects";
import { SettingsPage } from "./pages/Settings";
import { EditorPage } from "./editor/EditorPage";
import { Empty } from "./ui";
export default function App() {
  const location = useLocation(),
    navigate = useNavigate(),
    editor = /^\/clips\/[^/]+\/edit/.test(location.pathname);
  const onNavigate = (event: MouseEvent<HTMLDivElement>) => {
    if (
      !editor ||
      event.defaultPrevented ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey ||
      event.altKey ||
      event.button !== 0
    )
      return;
    const target =
      event.target instanceof Element ? event.target.closest("a") : null;
    const href = target?.getAttribute("href");
    if (
      !target ||
      !href?.startsWith("/") ||
      target.hasAttribute("download") ||
      target.target === "_blank"
    )
      return;
    event.preventDefault();
    const promises: Promise<void>[] = [];
    window.dispatchEvent(
      new CustomEvent("clipforge:navigate", {
        detail: {
          waitUntil: (promise: Promise<void>) => promises.push(promise),
        },
      }),
    );
    void Promise.all(promises)
      .then(() => navigate(href))
      .catch(() => undefined);
  };
  return (
    <div
      className={`app-shell ${editor ? "editor-shell" : ""}`}
      onClickCapture={onNavigate}
    >
      <aside className="app-rail">
        <Link className="rail-brand" to="/" aria-label="ClipForge">
          <Clapperboard size={23} />
        </Link>
        <nav aria-label="Навигация">
          <NavLink
            className={({ isActive }) =>
              `rail-link ${isActive ? "rail-active" : ""}`
            }
            to="/"
            end
            title="Проекты"
            aria-label="Проекты"
          >
            <FolderOpen size={19} />
          </NavLink>
          <NavLink
            className={({ isActive }) =>
              `rail-link ${isActive ? "rail-active" : ""}`
            }
            to="/upload"
            title="Новый проект"
            aria-label="Новый проект"
          >
            <Plus size={21} />
          </NavLink>
        </nav>
        <NavLink
          className={({ isActive }) =>
            `rail-link rail-settings ${isActive ? "rail-active" : ""}`
          }
          to="/settings"
          title="Настройки"
          aria-label="Настройки"
        >
          <Settings2 size={19} />
        </NavLink>
      </aside>
      <main className={editor ? "editor-main" : "main-content"}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/projects/:id/processing" element={<Processing />} />
          <Route path="/projects/:id/results" element={<Results />} />
          <Route path="/clips/:id/edit" element={<EditorPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route
            path="*"
            element={
              <Empty title="Страница не найдена">
                <Link className="button primary" to="/">
                  Открыть проекты
                </Link>
              </Empty>
            }
          />
        </Routes>
      </main>
    </div>
  );
}
