import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { requestOtp, verifyOtp, type Me } from "../api/auth";
import { strings } from "../strings";

type ErrorKey = keyof typeof strings.login.errors;

export function LoginPage({ onLoggedIn }: { onLoggedIn: (me: Me) => void }) {
  const t = strings.login;
  const navigate = useNavigate();
  const [step, setStep] = useState<"phone" | "code">("phone");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorKey | null>(null);

  async function sendCode(event?: FormEvent) {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    const result = await requestOtp(phone);
    setBusy(false);
    if (result.ok) {
      setCode("");
      setStep("code");
    } else {
      setError(result.reason);
    }
  }

  async function submitCode(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const result = await verifyOtp(phone, code);
    setBusy(false);
    if (result.ok) {
      onLoggedIn(result.me);
      navigate("/");
    } else {
      setError(result.reason);
    }
  }

  return (
    <main className="page">
      <h1>{t.title}</h1>

      {step === "phone" ? (
        <form className="card form" onSubmit={sendCode}>
          <label htmlFor="phone">{t.phoneLabel}</label>
          <input
            id="phone"
            type="tel"
            inputMode="tel"
            autoComplete="tel-national"
            placeholder="98765 43210"
            aria-describedby="phone-hint"
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
            required
          />
          <small id="phone-hint">{t.phoneHint}</small>
          <button type="submit" disabled={busy}>
            {busy ? t.sending : t.sendCode}
          </button>
        </form>
      ) : (
        <form className="card form" onSubmit={submitCode}>
          <p>
            {t.codeSentTo} {phone}
          </p>
          <label htmlFor="code">{t.codeLabel}</label>
          <input
            id="code"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="\d{6}"
            maxLength={6}
            value={code}
            onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
            required
          />
          <button type="submit" disabled={busy}>
            {busy ? t.verifying : t.verify}
          </button>
          <div className="form-links">
            <button type="button" className="link" onClick={() => sendCode()} disabled={busy}>
              {t.resend}
            </button>
            <button
              type="button"
              className="link"
              onClick={() => {
                setStep("phone");
                setError(null);
              }}
            >
              {t.changeNumber}
            </button>
          </div>
        </form>
      )}

      {error && (
        <p className="status-error" role="alert">
          {t.errors[error]}
        </p>
      )}
    </main>
  );
}
