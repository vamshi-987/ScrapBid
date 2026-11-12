import { useEffect, useState } from "react";

import { fetchContact, mapsLink, type Contact } from "../api/orders";
import { strings } from "../strings";

const t = strings.pickup.contact;

/** The other side's contact details, while the order is open. */
export function ContactCard({ orderId, role }: { orderId: number; role: "seller" | "dealer" }) {
  const [contact, setContact] = useState<Contact | "closed" | null | undefined>(undefined);

  useEffect(() => {
    fetchContact(orderId).then(setContact);
  }, [orderId]);

  if (contact === undefined || contact === null) return null;

  return (
    <section className="card">
      <h2>{role === "dealer" ? t.sellerTitle : t.dealerTitle}</h2>
      {contact === "closed" ? (
        <p>{t.closed}</p>
      ) : (
        <>
          {contact.name && <p>{contact.name}</p>}
          <p>
            <a href={`tel:${contact.phone}`}>{contact.phone}</a>
          </p>
          {contact.address_line && (
            <p>
              {contact.address_line}, {contact.locality} {contact.pincode}
              {contact.latitude !== undefined && contact.longitude !== undefined && (
                <>
                  <br />
                  <a
                    href={mapsLink(contact.latitude, contact.longitude)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {t.openInMaps}
                  </a>
                </>
              )}
            </p>
          )}
          <small>{t.privacy}</small>
        </>
      )}
    </section>
  );
}
