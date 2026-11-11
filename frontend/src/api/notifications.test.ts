import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchNotifications, fetchUnreadCount, markRead } from "./notifications";

function respondWith(response: Response | Error) {
  const fetchMock =
    response instanceof Error
      ? vi.fn().mockRejectedValue(response)
      : vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("document", { cookie: "csrftoken=tok123" });
  return fetchMock;
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("notifications API", () => {
  it("reads the unread count, and treats failures as zero", async () => {
    respondWith(json(200, { unread: 3 }));
    expect(await fetchUnreadCount()).toBe(3);

    respondWith(json(403, {}));
    expect(await fetchUnreadCount()).toBe(0);

    respondWith(new Error("offline"));
    expect(await fetchUnreadCount()).toBe(0);
  });

  it("reads the first page of notifications", async () => {
    respondWith(json(200, { count: 1, results: [{ id: 1, kind: "new_offer" }] }));

    expect(await fetchNotifications()).toEqual([{ id: 1, kind: "new_offer" }]);
  });

  it("marks one as read with POST and the CSRF token", async () => {
    const fetchMock = respondWith(json(200, {}));

    await markRead(7);

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/notifications/7/read");
    expect(init.method).toBe("POST");
    expect((init.headers as Headers).get("X-CSRFToken")).toBe("tok123");
  });
});
