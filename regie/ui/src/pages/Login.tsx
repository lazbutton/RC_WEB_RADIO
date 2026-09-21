import { FormEvent, useState } from "react";
import { AuthError, login } from "../api";
import { Button } from "../components/Button";

export function LoginPage({ onOk }: { onOk: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      onOk();
    } catch (err) {
      setError(err instanceof AuthError ? "Identifiants invalides." : err instanceof Error ? err.message : "Connexion impossible.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gate">
      <form className="gate-col" onSubmit={onSubmit}>
        <p className="gate-mark">Régie</p>
        <h1 className="gate-title">Le hub de Radio Campus Orléans. Mails, contacts, agenda, émissions : reliés.</h1>
        <label className="gate-field">
          <span className="gate-label">Adresse</span>
          <input className="field" type="email" autoComplete="username" required value={email} onChange={(ev) => setEmail(ev.target.value)} placeholder="prenom@orleans.radiocampus.org" />
        </label>
        <label className="gate-field">
          <span className="gate-label">Mot de passe</span>
          <input className="field" type="password" autoComplete="current-password" required value={password} onChange={(ev) => setPassword(ev.target.value)} placeholder="••••••••••" />
        </label>
        {error ? (
          <p className="gate-error" role="alert">
            {error}
          </p>
        ) : null}
        <Button variant="primary" busy={busy} className="gate-submit" onClick={(ev) => { ev.preventDefault(); void onSubmit(ev as unknown as FormEvent); }}>
          Entrer
        </Button>
        <p className="gate-hint">Un compte par personne. Rien n’est envoyé d’ici : Régie lit, classe, relie.</p>
      </form>
    </div>
  );
}
