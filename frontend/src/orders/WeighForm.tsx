import { useState, type FormEvent } from "react";

import type { Pickup, Weighing } from "../api/orders";
import { formatINR } from "../format/money";
import { formatIST } from "../format/time";
import { formatWeight } from "../format/weight";
import { strings } from "../strings";
import { ScaleCamera } from "./ScaleCamera";
import { CHOICES, compare, isValidMeasured, previewLinePaise, type WeighChoice } from "./weighing";

const t = strings.pickup.weigh;
type Item = Pickup["items"][number];

function amount(item: Item, value: string): string {
  return item.declared_weight_kg !== null
    ? formatWeight(value)
    : `${Number(value)} ${strings.listings.pieces}`;
}

function declared(item: Item): string {
  return item.declared_weight_kg ?? String(item.declared_quantity);
}

/** The dealer's weighing form: a measured amount per item, choices, and the scale photo. */
export function WeighForm({
  pickup,
  busy,
  onWeigh,
}: {
  pickup: Pickup;
  busy: boolean;
  onWeigh: (photo: Blob, lines: Weighing["lines"]) => void;
}) {
  const [measured, setMeasured] = useState<Record<number, string>>({});
  const [chosen, setChosen] = useState<Record<number, WeighChoice>>({});
  const [photo, setPhoto] = useState<Blob | null>(null);

  const lines = pickup.items.map((item) => {
    const perKg = item.declared_weight_kg !== null;
    const value = (measured[item.category] ?? "").trim();
    const valid = isValidMeasured(value, perKg);
    const comparison = valid
      ? compare(declared(item), value, perKg, pickup.weight_tolerance_percent)
      : null;
    const options = comparison ? CHOICES[comparison] : [];
    const choice = options.includes(chosen[item.category]) ? chosen[item.category] : null;
    const ready = valid && (options.length === 0 || choice !== null);
    const paise =
      ready && choice !== "deny"
        ? previewLinePaise(declared(item), value, perKg, item.rate_paise, choice)
        : null;
    return { item, perKg, value, valid, comparison, options, choice, ready, paise };
  });
  const allReady = lines.every((line) => line.ready);
  const denied = lines.some((line) => line.choice === "deny");
  const total = lines.reduce((sum, line) => sum + (line.paise ?? 0), 0);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!allReady || !photo) return;
    onWeigh(
      photo,
      lines.map((line) => ({
        category: line.item.category,
        verified: line.value,
        choice: line.choice,
      })),
    );
  }

  return (
    <form className="card form" onSubmit={submit}>
      <h2>{t.title}</h2>
      <small>{t.hint}</small>
      {lines.map((line) => (
        <fieldset key={line.item.category} className="weigh-line">
          <legend>
            {line.item.category_name}: {t.declared} {amount(line.item, declared(line.item))}
          </legend>
          <label htmlFor={`measured-${line.item.category}`}>
            {line.perKg ? t.measuredKg : t.measuredCount}
          </label>
          <input
            id={`measured-${line.item.category}`}
            inputMode={line.perKg ? "decimal" : "numeric"}
            value={measured[line.item.category] ?? ""}
            onChange={(event) =>
              setMeasured({ ...measured, [line.item.category]: event.target.value })
            }
            required
          />
          {line.comparison && <small>{t.comparisons[line.comparison]}</small>}
          {line.value && !line.valid && <small className="status-error">{t.invalid}</small>}
          {line.options.map((option) => (
            <label key={option} className="choice">
              <input
                type="radio"
                name={`choice-${line.item.category}`}
                checked={line.choice === option}
                onChange={() => setChosen({ ...chosen, [line.item.category]: option })}
              />
              {t.choices[option]}
            </label>
          ))}
          {line.paise !== null && (
            <small>
              {t.linePays} {formatINR(line.paise)}
            </small>
          )}
        </fieldset>
      ))}

      <ScaleCamera onChange={setPhoto} />

      {allReady && (
        <p className="notice">
          {denied ? (
            t.denyNotice
          ) : (
            <>
              {t.youPay} <strong>{formatINR(total)}</strong>
            </>
          )}
        </p>
      )}
      <small>{t.locationHint}</small>
      <button type="submit" disabled={busy || !allReady || !photo}>
        {denied ? t.submitDeny : t.submit}
      </button>
    </form>
  );
}

/** What was measured and the final amount, shown to both sides once weighed. */
export function WeighingResult({ pickup }: { pickup: Pickup }) {
  if (!pickup.weighed_at) return null;
  return (
    <section className="card">
      <h2>{t.resultTitle}</h2>
      <ul className="checks">
        {pickup.items.map((item) => (
          <li key={item.category}>
            {item.category_name}: {t.declared} {amount(item, declared(item))}, {t.verified}{" "}
            {amount(item, item.verified_amount ?? "0")}
            {item.choice && ` (${t.choices[item.choice]})`}
            {item.amount_paise !== null && pickup.final_amount_paise !== 0 && (
              <> → {formatINR(item.amount_paise)}</>
            )}
          </li>
        ))}
      </ul>
      {pickup.final_amount_paise !== null && pickup.final_amount_paise > 0 && (
        <p>
          {t.finalAmount}: <strong>{formatINR(pickup.final_amount_paise)}</strong>
          <br />
          <small>{t.finalNote}</small>
        </p>
      )}
      <p>
        <small>
          {t.weighedAt} {formatIST(pickup.weighed_at)}
          {pickup.weighing_distance_m !== null && `, ${pickup.weighing_distance_m} ${t.metresAway}`}
        </small>
      </p>
      {pickup.scale_photo_url && (
        <a href={pickup.scale_photo_url} target="_blank" rel="noreferrer">
          <img className="scale-photo" src={pickup.scale_photo_url} alt={t.photoAlt} />
        </a>
      )}
    </section>
  );
}
