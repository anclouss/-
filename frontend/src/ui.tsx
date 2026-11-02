import { AlertCircle, Film, LoaderCircle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
export function ErrorBanner({
  error,
  retry,
}: {
  error: string;
  retry?: () => void;
}) {
  return error ? (
    <div className="error-banner" role="alert">
      <AlertCircle size={16} />
      <span>{error}</span>
      {retry && (
        <button className="button ghost" onClick={retry}>
          <RefreshCw size={14} />
          Повторить
        </button>
      )}
    </div>
  ) : null;
}
export function Loading({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? "loading-inline" : "loading-state"} role="status">
      <LoaderCircle className="spinner" size={20} />
      <span>Загрузка…</span>
    </div>
  );
}
export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="skeleton-list" aria-label="Загрузка данных">
      {Array.from({ length: rows }, (_, index) => (
        <div className="skeleton" key={index} />
      ))}
    </div>
  );
}
export function PageHeader({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <header className="workspace-header">
      <div>
        <h1>{title}</h1>
        {description && <p className="muted">{description}</p>}
      </div>
      <div className="toolbar">{children}</div>
    </header>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <Film size={28} />
      <h3>{title}</h3>
      {children}
    </div>
  );
}
