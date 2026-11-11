import type { SellerOffer } from "../api/offers";
import { formatINR } from "../format/money";
import { formatIST } from "../format/time";
import { strings } from "../strings";

const t = strings.offers;

/** Text for one offer card. Every amount travels with the estimate label. */
export function offerSummary(offer: SellerOffer) {
  return {
    title: `${offer.dealer_name}, ${offer.dealer_locality}`,
    amount: formatINR(offer.estimated_total_paise),
    estimateNote: t.estimateNote,
    validity: offer.is_live ? `${t.validUntil} ${formatIST(offer.expires_at)}` : t.statuses.expired,
    selectable: offer.is_live,
  };
}
