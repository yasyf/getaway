const y = window.CcPresent;
if (!y)
  throw new Error("cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it");
const n = y.jsxRuntime.jsx, t = y.jsxRuntime.jsxs;
y.jsxRuntime.Fragment;
function m({ amount: e, currency: r }) {
  const i = new Intl.NumberFormat(void 0, { style: "currency", currency: r }), l = i.resolvedOptions().maximumFractionDigits;
  return i.format(e / 10 ** l);
}
function D(e) {
  return new Intl.NumberFormat(void 0, { notation: "compact", maximumFractionDigits: 1 }).format(e);
}
function x(e) {
  return new Intl.NumberFormat(void 0, { useGrouping: !0 }).format(e);
}
function f(e) {
  const r = Math.floor(e / 60), i = e % 60;
  return `${r}h ${String(i).padStart(2, "0")}m`;
}
function h(e) {
  return e.slice(11, 16);
}
function I(e, r) {
  const i = z(r) - z(e);
  return i === 0 ? "" : i > 0 ? `+${i}` : String(i);
}
function g(e) {
  const r = Number(e.slice(5, 7)), i = Number(e.slice(8, 10));
  return `${new Intl.DateTimeFormat(void 0, { weekday: "short", timeZone: "UTC" }).format(
    Date.UTC(Number(e.slice(0, 4)), r - 1, i)
  )} ${r}/${i}`;
}
function w(e) {
  const r = new Intl.RelativeTimeFormat(void 0, { numeric: "auto" }), i = Math.round((Date.parse(e) - Date.now()) / 6e4);
  if (Math.abs(i) < 60) return r.format(i, "minute");
  const l = Math.round(i / 60);
  return Math.abs(l) < 24 ? r.format(l, "hour") : r.format(Math.round(l / 24), "day");
}
function z(e) {
  return Date.UTC(Number(e.slice(0, 4)), Number(e.slice(5, 7)) - 1, Number(e.slice(8, 10))) / 864e5;
}
const F = {
  fontFamily: "var(--font-mono)",
  fontSize: "0.7rem",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  padding: "0.1rem 0.5rem",
  borderRadius: "999px",
  color: "var(--accent)",
  background: "color-mix(in srgb, var(--accent) 12%, var(--surface))",
  border: "1px solid color-mix(in srgb, var(--accent) 30%, var(--border))"
}, T = {
  fontSize: "0.7rem",
  textTransform: "capitalize",
  padding: "0.1rem 0.45rem",
  borderRadius: "var(--radius-md)",
  color: "var(--muted)",
  border: "1px solid var(--border)"
}, k = { fontFamily: "var(--font-mono)", fontSize: "0.85rem" };
function N(e) {
  return Date.UTC(
    Number(e.slice(0, 4)),
    Number(e.slice(5, 7)) - 1,
    Number(e.slice(8, 10)),
    Number(e.slice(11, 13)),
    Number(e.slice(14, 16))
  ) / 6e4;
}
function P({ seg: e }) {
  const r = I(e.departsAt, e.arrivesAt);
  return /* @__PURE__ */ t("div", { style: { display: "flex", alignItems: "baseline", gap: "0.75rem", flexWrap: "wrap" }, children: [
    /* @__PURE__ */ n("span", { style: k, children: e.flightNumber }),
    /* @__PURE__ */ t("span", { children: [
      e.origin,
      " → ",
      e.destination
    ] }),
    /* @__PURE__ */ t("span", { style: k, children: [
      h(e.departsAt),
      " → ",
      h(e.arrivesAt),
      r && /* @__PURE__ */ n("sup", { style: { color: "var(--muted)", marginLeft: "0.15rem" }, children: r })
    ] }),
    /* @__PURE__ */ n("span", { style: T, children: e.cabin }),
    /* @__PURE__ */ n("span", { style: { color: "var(--muted)", fontSize: "0.8rem" }, children: e.aircraft }),
    /* @__PURE__ */ n("span", { style: { marginLeft: "auto", color: "var(--muted)", fontSize: "0.8rem" }, children: f(e.durationMinutes) })
  ] });
}
function L({ block: e }) {
  const r = e, i = r.segments[0], l = r.segments[r.segments.length - 1];
  return /* @__PURE__ */ t(
    "div",
    {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "0.75rem",
        color: "var(--text)",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        padding: "1rem"
      },
      children: [
        /* @__PURE__ */ t("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ t("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
            /* @__PURE__ */ t("span", { style: { fontWeight: 700, fontSize: "1.05rem" }, children: [
              i.origin,
              " → ",
              l.destination
            ] }),
            /* @__PURE__ */ n("span", { style: F, children: r.program })
          ] }),
          /* @__PURE__ */ t("div", { style: { textAlign: "right", fontVariantNumeric: "tabular-nums" }, children: [
            /* @__PURE__ */ t("span", { style: { fontWeight: 600 }, children: [
              x(r.miles),
              " miles"
            ] }),
            /* @__PURE__ */ t("span", { style: { color: "var(--muted)" }, children: [
              " + ",
              m(r.taxes)
            ] })
          ] })
        ] }),
        /* @__PURE__ */ t("div", { style: { color: "var(--muted)", fontSize: "0.8rem" }, children: [
          r.remainingSeats,
          " seats · ",
          f(r.totalDurationMinutes),
          " ·",
          " ",
          /* @__PURE__ */ t("span", { title: r.updatedAt, children: [
            "checked ",
            w(r.updatedAt)
          ] })
        ] }),
        /* @__PURE__ */ n("div", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: r.segments.map((o, c) => {
          const d = r.segments[c + 1], a = d && o.destination === d.origin;
          return /* @__PURE__ */ t("div", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: [
            /* @__PURE__ */ n(P, { seg: o }),
            d && (a ? /* @__PURE__ */ t(
              "div",
              {
                style: {
                  color: "var(--muted)",
                  fontSize: "0.75rem",
                  paddingLeft: "0.6rem",
                  borderLeft: "2px dashed var(--border)"
                },
                children: [
                  f(N(d.departsAt) - N(o.arrivesAt)),
                  " layover in",
                  " ",
                  o.destination
                ]
              }
            ) : /* @__PURE__ */ n("div", { style: { borderTop: "1px solid var(--border)" } }))
          ] }, o.flightNumber + o.departsAt);
        }) }),
        /* @__PURE__ */ n(
          "a",
          {
            href: r.bookingLink.url,
            target: "_blank",
            rel: "noopener noreferrer",
            style: { color: "var(--accent)", fontWeight: 600, textDecoration: "none" },
            children: r.bookingLink.label
          }
        )
      ]
    }
  );
}
const A = {
  fontFamily: "var(--font-mono)",
  fontSize: "1.6rem",
  fontWeight: 600,
  lineHeight: 1.1
}, v = { color: "var(--muted)", fontSize: "0.8rem" }, M = {
  fontSize: "0.7rem",
  textTransform: "capitalize",
  padding: "0.1rem 0.45rem",
  borderRadius: "var(--radius-md)",
  color: "var(--muted)",
  border: "1px solid var(--border)"
};
function j({ block: e }) {
  const r = e, i = I(r.departsAt, r.arrivesAt);
  return /* @__PURE__ */ t(
    "div",
    {
      style: {
        position: "relative",
        display: "flex",
        flexDirection: "column",
        gap: "0.75rem",
        color: "var(--text)",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        padding: "1rem"
      },
      children: [
        r.price && /* @__PURE__ */ n(
          "span",
          {
            style: {
              position: "absolute",
              top: "0.6rem",
              right: "0.75rem",
              fontWeight: 600,
              fontSize: "0.85rem",
              padding: "0.1rem 0.5rem",
              borderRadius: "999px",
              color: "var(--accent)",
              background: "color-mix(in srgb, var(--accent) 12%, var(--surface))"
            },
            children: m(r.price)
          }
        ),
        /* @__PURE__ */ t("div", { style: { display: "flex", alignItems: "flex-end", gap: "1rem" }, children: [
          /* @__PURE__ */ t("div", { style: { textAlign: "center" }, children: [
            /* @__PURE__ */ n("div", { style: A, children: h(r.departsAt) }),
            /* @__PURE__ */ n("div", { style: v, children: r.origin })
          ] }),
          /* @__PURE__ */ t("div", { style: { flex: 1, textAlign: "center", paddingBottom: "0.5rem" }, children: [
            /* @__PURE__ */ n("div", { style: { color: "var(--muted)", fontSize: "0.75rem", marginBottom: "0.2rem" }, children: f(r.durationMinutes) }),
            /* @__PURE__ */ n("div", { style: { borderTop: "1px solid var(--border)" } })
          ] }),
          /* @__PURE__ */ t("div", { style: { textAlign: "center" }, children: [
            /* @__PURE__ */ t("div", { style: A, children: [
              h(r.arrivesAt),
              i && /* @__PURE__ */ n("sup", { style: { color: "var(--muted)", fontSize: "0.9rem", marginLeft: "0.15rem" }, children: i })
            ] }),
            /* @__PURE__ */ n("div", { style: v, children: r.destination })
          ] })
        ] }),
        /* @__PURE__ */ t("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ n("span", { style: { fontFamily: "var(--font-mono)", fontSize: "0.85rem" }, children: r.flightNumber }),
          /* @__PURE__ */ n("span", { style: M, children: r.cabin }),
          r.aircraft && /* @__PURE__ */ n("span", { style: v, children: r.aircraft })
        ] })
      ]
    }
  );
}
const C = window.CcPresent;
if (!C)
  throw new Error("cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it");
const _ = C.React, { createElement: te, Fragment: E, useCallback: ne, useEffect: ie, useMemo: ae, useRef: oe, useState: le } = _, O = ["economy", "premium", "business", "first"], U = {
  fontFamily: "var(--font-mono)",
  fontSize: "0.7rem",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  padding: "0.1rem 0.5rem",
  borderRadius: "999px",
  color: "var(--accent)",
  background: "color-mix(in srgb, var(--accent) 12%, var(--surface))",
  border: "1px solid color-mix(in srgb, var(--accent) 30%, var(--border))"
}, B = {
  fontSize: "0.7rem",
  textTransform: "capitalize",
  color: "var(--muted)",
  textAlign: "center",
  padding: "0 0.25rem"
};
function q({ block: e, value: r, submit: i, disabled: l }) {
  const o = e, c = r, d = O.filter((a) => o.rows.some((s) => s.cabins[a]));
  return /* @__PURE__ */ t("div", { style: { display: "flex", flexDirection: "column", gap: "0.75rem", color: "var(--text)" }, children: [
    /* @__PURE__ */ t("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ t("span", { style: { fontWeight: 700, fontSize: "1.05rem" }, children: [
        o.origin,
        " → ",
        o.destination
      ] }),
      o.program && /* @__PURE__ */ n("span", { style: U, children: o.program })
    ] }),
    /* @__PURE__ */ t(
      "div",
      {
        style: {
          display: "grid",
          gridTemplateColumns: `auto repeat(${d.length}, minmax(0, 1fr))`,
          gap: "0.4rem",
          alignItems: "stretch"
        },
        children: [
          /* @__PURE__ */ n("div", {}),
          d.map((a) => /* @__PURE__ */ n("div", { style: B, children: a }, a)),
          o.rows.map((a) => /* @__PURE__ */ t(E, { children: [
            /* @__PURE__ */ n(
              "div",
              {
                title: a.date,
                style: { alignSelf: "center", fontSize: "0.8rem", color: "var(--muted)", whiteSpace: "nowrap" },
                children: g(a.date)
              }
            ),
            d.map((s) => {
              const p = a.cabins[s];
              if (!p)
                return /* @__PURE__ */ n("div", { style: { textAlign: "center", color: "var(--muted)", alignSelf: "center" }, children: "—" }, s);
              const u = c?.date === a.date && c?.cabin === s;
              return /* @__PURE__ */ t(
                "button",
                {
                  type: "button",
                  disabled: l,
                  "aria-pressed": u,
                  onClick: () => i({ date: a.date, cabin: s }),
                  style: {
                    display: "flex",
                    flexDirection: "column",
                    gap: "0.15rem",
                    padding: "0.4rem 0.3rem",
                    cursor: l ? "not-allowed" : "pointer",
                    borderRadius: "var(--radius-md)",
                    border: `1px solid ${u ? "var(--accent)" : "var(--border)"}`,
                    background: u ? "color-mix(in srgb, var(--accent) 14%, var(--surface))" : "var(--surface)",
                    color: "var(--text)",
                    opacity: l && !u ? 0.55 : 1
                  },
                  children: [
                    /* @__PURE__ */ n("span", { style: { fontFamily: "var(--font-mono)", fontSize: "0.85rem", fontWeight: 600 }, children: D(p.miles) }),
                    /* @__PURE__ */ t("span", { style: { fontSize: "0.68rem", color: "var(--muted)" }, children: [
                      p.seats,
                      " seats",
                      p.direct ? " · nonstop" : ""
                    ] })
                  ]
                },
                s
              );
            })
          ] }, a.date))
        ]
      }
    )
  ] });
}
const H = {
  fontSize: "0.7rem",
  textTransform: "capitalize",
  padding: "0.1rem 0.45rem",
  borderRadius: "var(--radius-md)",
  color: "var(--muted)",
  border: "1px solid var(--border)"
};
function G({ block: e, value: r, submit: i, disabled: l }) {
  const o = e, c = r, d = `${e.id}-label`;
  return /* @__PURE__ */ t("div", { style: { display: "flex", flexDirection: "column", gap: "0.6rem", color: "var(--text)" }, children: [
    /* @__PURE__ */ n("div", { id: d, style: { fontWeight: 700, fontSize: "1.05rem" }, children: o.label }),
    /* @__PURE__ */ n("div", { role: "radiogroup", "aria-labelledby": d, style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: o.options.map((a) => {
      const s = c?.optionId === a.optionId;
      return /* @__PURE__ */ t(
        "button",
        {
          type: "button",
          role: "radio",
          "aria-checked": s,
          disabled: l,
          onClick: () => i({ optionId: a.optionId }),
          style: {
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "1rem",
            width: "100%",
            textAlign: "left",
            padding: "0.6rem 0.75rem",
            cursor: l ? "not-allowed" : "pointer",
            borderRadius: "var(--radius-md)",
            border: `1px solid ${s ? "var(--accent)" : "var(--border)"}`,
            background: s ? "color-mix(in srgb, var(--accent) 14%, var(--surface))" : "var(--surface)",
            color: "var(--text)",
            opacity: l && !s ? 0.55 : 1
          },
          children: [
            /* @__PURE__ */ t("span", { style: { display: "flex", flexDirection: "column", gap: "0.15rem" }, children: [
              /* @__PURE__ */ t("span", { style: { fontWeight: 600 }, children: [
                a.origin,
                " → ",
                a.destination
              ] }),
              /* @__PURE__ */ t("span", { style: { color: "var(--muted)", fontSize: "0.78rem" }, children: [
                "dep ",
                g(a.date)
              ] })
            ] }),
            /* @__PURE__ */ t("span", { style: { display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.15rem" }, children: [
              /* @__PURE__ */ n("span", { style: { fontSize: "0.78rem", color: "var(--muted)" }, children: a.program }),
              /* @__PURE__ */ t("span", { style: { fontFamily: "var(--font-mono)", fontSize: "0.85rem", fontWeight: 600 }, children: [
                D(a.miles),
                " + ",
                m(a.taxes)
              ] }),
              /* @__PURE__ */ t("span", { style: { display: "flex", alignItems: "center", gap: "0.35rem" }, children: [
                a.cabin && /* @__PURE__ */ n("span", { style: H, children: a.cabin }),
                s && /* @__PURE__ */ n("span", { style: { color: "var(--accent)", fontWeight: 700 }, children: "✓" })
              ] })
            ] })
          ]
        },
        a.optionId
      );
    }) })
  ] });
}
const V = {
  bot_wall: "rooms.aero blocked the lookup with a bot wall",
  logged_out: "the rooms.aero session was not signed in",
  geocode_miss: "the destination could not be geocoded",
  date_in_past: "check-in had already passed",
  failed: "the lodging lookup did not complete"
}, Z = {
  no_checkout: { head: "Lodging deferred", body: "No confirmed return date yet, so there is no checkout to price a stay against." },
  date_in_past: { head: "Lodging deferred", body: "Check-in falls in the past." },
  invalid_interval: { head: "Lodging deferred", body: "The derived stay interval isn't valid." },
  not_walked: { head: "Lodging not checked", body: "This option was not walked on rooms.aero." }
}, R = {
  display: "flex",
  flexDirection: "column",
  gap: "0.75rem",
  color: "var(--text)",
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-md)",
  padding: "1rem"
}, J = {
  fontFamily: "var(--font-mono)",
  fontSize: "0.7rem",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  padding: "0.1rem 0.5rem",
  borderRadius: "999px",
  color: "var(--accent)",
  background: "color-mix(in srgb, var(--accent) 12%, var(--surface))",
  border: "1px solid color-mix(in srgb, var(--accent) 30%, var(--border))"
}, b = {
  fontSize: "0.7rem",
  textTransform: "capitalize",
  padding: "0.1rem 0.45rem",
  borderRadius: "var(--radius-md)",
  color: "var(--muted)",
  border: "1px solid var(--border)"
}, W = {
  fontSize: "0.68rem",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  padding: "0.1rem 0.45rem",
  borderRadius: "999px",
  color: "var(--warn)",
  background: "color-mix(in srgb, var(--warn) 14%, var(--surface))",
  border: "1px solid color-mix(in srgb, var(--warn) 45%, var(--border))"
}, S = (e) => ({
  fontSize: "0.8rem",
  color: `var(--${e})`,
  background: `color-mix(in srgb, var(--${e}) 12%, var(--surface))`,
  border: `1px solid color-mix(in srgb, var(--${e}) 45%, var(--border))`,
  borderRadius: "var(--radius-md)",
  padding: "0.5rem 0.65rem"
}), K = { fontFamily: "var(--font-mono)", fontSize: "0.85rem", fontWeight: 600 };
function $({ destination: e, airport: r, session: i }) {
  return /* @__PURE__ */ t("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
    /* @__PURE__ */ n("span", { style: { fontWeight: 700, fontSize: "1.05rem" }, children: e }),
    r && /* @__PURE__ */ n("span", { style: b, children: r }),
    i === "anonymous" && /* @__PURE__ */ n("span", { style: W, children: "anonymous" })
  ] });
}
function Q({ offer: e, currency: r, nights: i }) {
  const l = [], o = [];
  return e.pointsPerNight !== null && (l.push(`${x(e.pointsPerNight)} pts`), o.push(`${x(e.pointsPerNight * i)} pts`)), e.cashPerNightCents !== null && (l.push(m({ amount: e.cashPerNightCents, currency: r })), o.push(m({ amount: e.cashPerNightCents * i, currency: r }))), /* @__PURE__ */ t("div", { style: { display: "flex", flexDirection: "column", gap: "0.15rem" }, children: [
    /* @__PURE__ */ t("div", { style: { display: "flex", alignItems: "baseline", gap: "0.5rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ n("span", { style: b, children: e.awardClass }),
      /* @__PURE__ */ n("span", { style: K, children: l.join(" + ") }),
      /* @__PURE__ */ n("span", { style: { color: "var(--muted)", fontSize: "0.75rem" }, children: "/ night" }),
      e.centsPerPoint !== null && /* @__PURE__ */ t("span", { style: { ...b, fontFamily: "var(--font-mono)" }, children: [
        e.centsPerPoint.toFixed(1),
        "¢/pt"
      ] })
    ] }),
    /* @__PURE__ */ t("div", { style: { color: "var(--muted)", fontSize: "0.72rem" }, children: [
      "≈ ",
      o.join(" + "),
      " est. for ",
      i,
      " ",
      i === 1 ? "night" : "nights"
    ] })
  ] });
}
function X({ block: e }) {
  const { interval: r } = e, i = r.nightClamped ? r.requestedNights ? `first ${r.nights} nights of ${r.requestedNights}` : `capped at rooms.aero's ${r.nights}-night maximum` : null, l = e.rooms.length === 0 ? V[e.searchState] : void 0;
  return /* @__PURE__ */ t("div", { style: R, children: [
    /* @__PURE__ */ t("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ n($, { destination: e.destination, airport: e.airport, session: e.session }),
      /* @__PURE__ */ t("span", { style: { color: "var(--muted)", fontSize: "0.8rem" }, title: e.checkedAt, children: [
        "checked ",
        w(e.checkedAt)
      ] })
    ] }),
    /* @__PURE__ */ t("div", { style: { color: "var(--muted)", fontSize: "0.8rem" }, children: [
      g(r.checkIn),
      " → ",
      g(r.checkOut),
      " · ",
      r.nights,
      " ",
      r.nights === 1 ? "night" : "nights",
      i && /* @__PURE__ */ t("span", { style: { color: "var(--warn)" }, children: [
        " · ",
        i
      ] })
    ] }),
    e.session === "anonymous" && /* @__PURE__ */ n("div", { style: S("warn"), children: "Anonymous session — these rates are not refreshed and can be weeks stale." }),
    l ? /* @__PURE__ */ t("div", { style: S("danger"), children: [
      "Lodging lookup couldn't complete: ",
      l,
      "."
    ] }) : e.rooms.length === 0 ? /* @__PURE__ */ n("div", { style: { color: "var(--muted)", fontSize: "0.85rem" }, children: "No award rooms found for this stay." }) : /* @__PURE__ */ n("div", { style: { display: "flex", flexDirection: "column", gap: "0.75rem" }, children: e.rooms.map((o, c) => /* @__PURE__ */ t("div", { style: { display: "flex", flexDirection: "column", gap: "0.4rem" }, children: [
      c > 0 && /* @__PURE__ */ n("div", { style: { borderTop: "1px solid var(--border)" } }),
      /* @__PURE__ */ n(Y, { room: o, nights: r.nights })
    ] }, o.program + o.name)) }),
    /* @__PURE__ */ n("div", { style: { color: "var(--muted)", fontSize: "0.72rem" }, children: "Per-night rates are the source of truth; any stay total is an estimate." })
  ] });
}
function Y({ room: e, nights: r }) {
  return /* @__PURE__ */ t("div", { style: { display: "flex", flexDirection: "column", gap: "0.4rem" }, children: [
    /* @__PURE__ */ t("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ n("span", { style: J, children: e.program }),
      /* @__PURE__ */ n("span", { style: { fontWeight: 600 }, children: e.name }),
      /* @__PURE__ */ t("span", { style: { marginLeft: "auto", display: "flex", alignItems: "center", gap: "0.4rem" }, children: [
        e.stale && /* @__PURE__ */ n("span", { style: W, children: "stale" }),
        /* @__PURE__ */ t("span", { title: e.checkedAt, style: { color: "var(--muted)", fontSize: "0.75rem" }, children: [
          "checked ",
          w(e.checkedAt)
        ] })
      ] })
    ] }),
    e.offers.map((i) => /* @__PURE__ */ n(Q, { offer: i, currency: e.currency, nights: r }, i.awardClass))
  ] });
}
function ee({ block: e }) {
  const { head: r, body: i } = Z[e.reason];
  return /* @__PURE__ */ t("div", { style: R, children: [
    e.destination && /* @__PURE__ */ n($, { destination: e.destination, airport: e.airport }),
    /* @__PURE__ */ t("div", { style: S("warn"), children: [
      /* @__PURE__ */ t("span", { style: { fontWeight: 600 }, children: [
        r,
        "."
      ] }),
      " ",
      i
    ] })
  ] });
}
function re({ block: e }) {
  const r = e;
  return r.state === "deferred" ? /* @__PURE__ */ n(ee, { block: r }) : /* @__PURE__ */ n(X, { block: r });
}
const se = {
  hostApi: 1,
  blocks: {
    itinerary: L,
    flight: j,
    availability: q,
    "option-picker": G,
    stay: re
  }
};
export {
  se as default
};
