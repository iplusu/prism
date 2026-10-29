import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../appContext";
import { IconMoon, IconSun } from "./Icons";
import { Menu } from "./ui";

export function ThemeToggle() {
  const { theme } = useApp();
  return (
    <button
      className="icon-btn"
      onClick={theme.toggle}
      aria-label={theme.effective === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      title={theme.effective === "dark" ? "Light theme" : "Dark theme"}
    >
      {theme.effective === "dark" ? <IconSun /> : <IconMoon />}
    </button>
  );
}

export function UserMenu() {
  const { user, logout, theme } = useApp();
  return (
    <Menu
      label={`Account: ${user.display_name}`}
      align="right"
      buttonClass="btn btn-ghost"
      buttonContent={<span className="avatar-sm" aria-hidden="true">{user.display_name.slice(0, 1).toUpperCase()}</span>}
    >
      {(close) => (
        <div className="menu-list">
          <div className="menu-info">
            <strong>{user.display_name}</strong>
            <span className="muted small">@{user.username}{user.is_admin ? " · administrator" : ""}</span>
          </div>
          <div className="menu-sep" />
          <div className="menu-info small">
            Theme:
            <span className="seg">
              {(["light", "dark", "system"] as const).map((t) => (
                <button key={t} className={`seg-btn ${theme.pref === t ? "active" : ""}`} aria-pressed={theme.pref === t} onClick={() => theme.choose(t)}>
                  {t}
                </button>
              ))}
            </span>
          </div>
          <div className="menu-sep" />
          <button
            className="menu-item"
            onClick={() => {
              close();
              void logout();
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </Menu>
  );
}

export function AppHeader({ title, children }: { title?: ReactNode; children?: ReactNode }) {
  return (
    <header className="app-header">
      <Link to="/" className="brand-link" aria-label="Prism — all projects">
        <span className="brand-mark" aria-hidden="true" />
        <span className="brand-name">Prism</span>
      </Link>
      {title && <div className="app-header-title">{title}</div>}
      <div className="spacer" />
      {children}
      <ThemeToggle />
      <UserMenu />
    </header>
  );
}
