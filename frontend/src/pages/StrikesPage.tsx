import { useEffect, useState } from "react";
import { Link } from "react-router";

import { challengeStrike, fetchStrikes, type Strike } from "../api/orders";
import { formatINR } from "../format/money";
import { formatIST } from "../format/time";
import { strings } from "../strings";

const t = strings.strikes;
type ErrorKey = keyof typeof t.errors;

/** The user's missed pickups, with a way to challenge each within 48 hours. */
export function StrikesPage() {
  const [strikes, setStrikes] = useState<Strike[] | null>(null);
  const [error, setError] = useState<ErrorKey | null>(null);

  useEffect(() => {
    fetchStrikes().then(setStrikes);
  }, []);

  async function send(strike: Strike, reason: string) {
    setError(null);
    const result = await challengeStrike(strike.id, reason);
    if (!result.ok) setError(result.reason in t.errors ? (result.reason as ErrorKey) : "error");
    setStrikes(await fetchStrikes());
  }

  return (
    <main className="page">
      <h1>{t.title}</h1>
      {strikes?.length === 0 && <p>{t.none}</p>}
      {strikes?.map((strike) => (
        <section key={strike.id} className="card">
          <p>
            <strong>
              {t.youMissed} {strings.pickup.roles[strike.party]}
            </strong>{" "}
            · {formatIST(strike.created_at)}
          </p>
          <p>
            {strike.penalty &&
              `${t.penalty} ${formatINR(strike.penalty.amount_paise)} (${t.penaltyStatuses[strike.penalty.status]}) · `}
            {t.statuses[strike.status]}
          </p>
          {strike.penalty?.status === "unpaid" && <Link to="/dues">{t.payNow}</Link>}
          {strike.challenged_at && (
            <p>
              {t.challenged} {formatIST(strike.challenged_at)}: “{strike.challenge_reason}”
            </p>
          )}
          {strike.can_challenge && <ChallengeForm onSend={(reason) => send(strike, reason)} />}
        </section>
      ))}
      {error && (
        <p className="status-error" role="alert">
          {t.errors[error]}
        </p>
      )}
    </main>
  );
}

function ChallengeForm({ onSend }: { onSend: (reason: string) => void }) {
  const [reason, setReason] = useState("");

  return (
    <form
      className="form"
      onSubmit={(event) => {
        event.preventDefault();
        if (reason.trim()) onSend(reason.trim());
      }}
    >
      <label htmlFor="challenge-reason">{t.challengeReason}</label>
      <textarea
        id="challenge-reason"
        maxLength={500}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
      />
      <small>{t.challengeHint}</small>
      <button type="submit" disabled={!reason.trim()}>
        {t.sendChallenge}
      </button>
    </form>
  );
}
