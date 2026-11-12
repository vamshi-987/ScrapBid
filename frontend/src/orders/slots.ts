/**
 * Pickup slot helpers. Slots are chosen in IST; the server checks every rule
 * (hours, notice, how far ahead) and its answer is final. These defaults mirror
 * the server's config so the picker only offers sensible times.
 */
export const SLOT_HOURS = 2;
export const DAY_START_HOUR = 8;
export const DAY_END_HOUR = 20;
export const MAX_DAYS_AHEAD = 7;

/** Start times a 2-hour slot can begin at, every 30 minutes: "08:00" … "18:00". */
export function slotStartTimes(): string[] {
  const times: string[] = [];
  for (
    let minutes = DAY_START_HOUR * 60;
    minutes <= (DAY_END_HOUR - SLOT_HOURS) * 60;
    minutes += 30
  ) {
    const hours = String(Math.floor(minutes / 60)).padStart(2, "0");
    times.push(`${hours}:${minutes % 60 === 0 ? "00" : "30"}`);
  }
  return times;
}

/** "2026-11-12" + "10:30" → "2026-11-12T10:30:00+05:30" (an IST time for the API). */
export function istStart(date: string, time: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time)) return null;
  return `${date}T${time}:00+05:30`;
}

const istDay = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Kolkata",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** Today's and the last bookable date in IST, as "YYYY-MM-DD", for the date picker. */
export function bookableDates(now = new Date()): { min: string; max: string } {
  const last = new Date(now.getTime() + MAX_DAYS_AHEAD * 24 * 60 * 60 * 1000);
  return { min: istDay.format(now), max: istDay.format(last) };
}

const istTime = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  hour: "numeric",
  minute: "2-digit",
});
const istDate = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  weekday: "short",
  day: "numeric",
  month: "short",
});

/** "Thu, 12 Nov, 10:00 am – 12:00 pm" in IST. */
export function formatSlot(start: string, end: string): string {
  const from = new Date(start);
  return `${istDate.format(from)}, ${istTime.format(from)} – ${istTime.format(new Date(end))}`;
}
