import { useEffect, useState } from "react";
import { Link } from "react-router";

import { fetchMyPickups, type Pickup } from "../api/orders";
import { formatINR } from "../format/money";
import { strings } from "../strings";
import { formatSlot } from "./slots";

const t = strings.pickup;

/** The dealer's accepted pickups, in the Buying view. */
export function MyPickups() {
  const [pickups, setPickups] = useState<Pickup[] | null>(null);

  useEffect(() => {
    fetchMyPickups().then((all) => setPickups(all.filter((pickup) => pickup.my_role === "dealer")));
  }, []);

  if (!pickups || pickups.length === 0) return null;

  return (
    <section className="card">
      <h2>{t.myPickups}</h2>
      <ul className="listing-list">
        {pickups.map((pickup) => (
          <li key={pickup.id}>
            <Link to={`/orders/${pickup.id}`}>
              <span>
                <strong>
                  {pickup.slot_start && pickup.slot_end
                    ? formatSlot(pickup.slot_start, pickup.slot_end)
                    : t.noTimeYet}
                </strong>
                <br />
                <small>
                  {pickup.locality} · {formatINR(pickup.estimated_total_paise)} (
                  {strings.offers.estimate}){pickup.actions.length > 0 && ` · ${t.needsYou}`}
                </small>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
