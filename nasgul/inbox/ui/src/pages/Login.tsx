import { FormEvent, useState } from "react";
import { AuthError, login } from "../api";
import { Button } from "../components/Button";
import { label } from "../lib/brand";
import { useBrand } from "../lib/BrandContext";

export function LoginPage({ onOk }: { onOk: () => void }) {
  const brand = useBrand();
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(token);
      onOk();
    } catch (err) {
      setError(err instanceof AuthError ? "Jeton invalide." : err instanceof Error ? err.message : "Jeton invalide.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gate">
      <form className="gate-col" onSubmit={onSubmit}>
        <p className="gate-mark">{label(brand, "inbox", "title")}</p>
        <h1 className="gate-title">Ta boîte, triée. Rien n’est envoyé d’ici.</h1>
        <label className="gate-field">
          <span className="gate-label">Jeton</span>
          <input
            className="field"
            type="password"
            autoComplete="current-password"
            required
            placeholder="••••••••"
            value={token}
            onChange={(ev) => setToken(ev.target.value)}
          />
        </label>
        {error ? (
          <p className="gate-error" role="alert">
            {error}
          </p>
        ) : null}
        <Button variant="primary" busy={busy} className="gate-submit" onClick={(ev) => { ev.preventDefault(); void onSubmit(ev as unknown as FormEvent); }}>
          Entrer
        </Button>
        <p className="gate-hint">Le jeton est dans les secrets Nasgul (`inboxzero.json`, clé `ui_token`).</p>
      </form>
    </div>
  );
}
