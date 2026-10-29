import { useState, type FormEvent } from "react";
import { api, errorMessage } from "../api";
import { IconMoon, IconSun } from "../components/Icons";
import { ErrorBox } from "../components/ui";
import type { AuthConfig, User } from "../types";

export default function LoginPage({
  config,
  onLogin,
  theme,
}: {
  config: AuthConfig;
  onLogin: (u: User) => void;
  theme: { effective: "light" | "dark"; toggle: () => void };
}) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const u = mode === "login" ? await api.login(username, password) : await api.signup(username, password, displayName);
      onLogin(u);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-page">
      <button
        className="icon-btn corner-btn"
        onClick={theme.toggle}
        aria-label={theme.effective === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      >
        {theme.effective === "dark" ? <IconSun /> : <IconMoon />}
      </button>
      <form className="card narrow login-card" onSubmit={submit} aria-labelledby="login-title">
        <div className="brand-row">
          <span className="brand-mark" aria-hidden="true" />
          <h1 id="login-title">Prism</h1>
        </div>
        <p className="muted">{mode === "login" ? "Sign in to your research workspace." : "Create an account."}</p>
        <label className="field">
          <span>Username</span>
          <input autoComplete="username" required value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        {mode === "signup" && (
          <label className="field">
            <span>Display name (optional)</span>
            <input autoComplete="name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </label>
        )}
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required
            minLength={mode === "signup" ? 8 : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {mode === "signup" && <small className="muted">At least 8 characters.</small>}
        </label>
        <ErrorBox error={error} />
        <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
        {config.allow_signup && (
          <p className="muted small center">
            {mode === "login" ? "No account? " : "Already have an account? "}
            <button
              type="button"
              className="link-btn"
              onClick={() => {
                setMode(mode === "login" ? "signup" : "login");
                setError(null);
              }}
            >
              {mode === "login" ? "Sign up" : "Sign in"}
            </button>
          </p>
        )}
      </form>
    </div>
  );
}
