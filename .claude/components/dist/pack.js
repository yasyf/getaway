const v = window.CcPresent;
if (!v)
  throw new Error("cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it");
const n = v.jsxRuntime.jsx, t = v.jsxRuntime.jsxs;
v.jsxRuntime.Fragment;
function y({ amount: e, currency: r }) {
  const i = new Intl.NumberFormat(void 0, { style: "currency", currency: r }), o = i.resolvedOptions().maximumFractionDigits;
  return i.format(e / 10 ** o);
}
function w(e) {
  return new Intl.NumberFormat(void 0, { notation: "compact", maximumFractionDigits: 1 }).format(e);
}
function D(e) {
  return new Intl.NumberFormat(void 0, { useGrouping: !0 }).format(e);
}
function u(e) {
  const r = Math.floor(e / 60), i = e % 60;
  return `${r}h ${String(i).padStart(2, "0")}m`;
}
function p(e) {
  return e.slice(11, 16);
}
function z(e, r) {
  const i = g(r) - g(e);
  return i === 0 ? "" : i > 0 ? `+${i}` : String(i);
}
function k(e) {
  const r = Number(e.slice(5, 7)), i = Number(e.slice(8, 10));
  return `${new Intl.DateTimeFormat(void 0, { weekday: "short", timeZone: "UTC" }).format(
    Date.UTC(Number(e.slice(0, 4)), r - 1, i)
  )} ${r}/${i}`;
}
function I(e) {
  const r = new Intl.RelativeTimeFormat(void 0, { numeric: "auto" }), i = Math.round((Date.parse(e) - Date.now()) / 6e4);
  if (Math.abs(i) < 60) return r.format(i, "minute");
  const o = Math.round(i / 60);
  return Math.abs(o) < 24 ? r.format(o, "hour") : r.format(Math.round(o / 24), "day");
}
function g(e) {
  return Date.UTC(Number(e.slice(0, 4)), Number(e.slice(5, 7)) - 1, Number(e.slice(8, 10))) / 864e5;
}
const C = {
  fontFamily: "var(--font-mono)",
  fontSize: "0.7rem",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  padding: "0.1rem 0.5rem",
  borderRadius: "999px",
  color: "var(--accent)",
  background: "color-mix(in srgb, var(--accent) 12%, var(--surface))",
  border: "1px solid color-mix(in srgb, var(--accent) 30%, var(--border))"
}, N = {
  fontSize: "0.7rem",
  textTransform: "capitalize",
  padding: "0.1rem 0.45rem",
  borderRadius: "var(--radius-md)",
  color: "var(--muted)",
  border: "1px solid var(--border)"
}, x = { fontFamily: "var(--font-mono)", fontSize: "0.85rem" };
function b(e) {
  return Date.UTC(
    Number(e.slice(0, 4)),
    Number(e.slice(5, 7)) - 1,
    Number(e.slice(8, 10)),
    Number(e.slice(11, 13)),
    Number(e.slice(14, 16))
  ) / 6e4;
}
function R({ seg: e }) {
  const r = z(e.departsAt, e.arrivesAt);
  return /* @__PURE__ */ t("div", { style: { display: "flex", alignItems: "baseline", gap: "0.75rem", flexWrap: "wrap" }, children: [
    /* @__PURE__ */ n("span", { style: x, children: e.flightNumber }),
    /* @__PURE__ */ t("span", { children: [
      e.origin,
      " → ",
      e.destination
    ] }),
    /* @__PURE__ */ t("span", { style: x, children: [
      p(e.departsAt),
      " → ",
      p(e.arrivesAt),
      r && /* @__PURE__ */ n("sup", { style: { color: "var(--muted)", marginLeft: "0.15rem" }, children: r })
    ] }),
    /* @__PURE__ */ n("span", { style: N, children: e.cabin }),
    /* @__PURE__ */ n("span", { style: { color: "var(--muted)", fontSize: "0.8rem" }, children: e.aircraft }),
    /* @__PURE__ */ n("span", { style: { marginLeft: "auto", color: "var(--muted)", fontSize: "0.8rem" }, children: u(e.durationMinutes) })
  ] });
}
function F({ block: e }) {
  const r = e, i = r.segments[0], o = r.segments[r.segments.length - 1];
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
              o.destination
            ] }),
            /* @__PURE__ */ n("span", { style: C, children: r.program })
          ] }),
          /* @__PURE__ */ t("div", { style: { textAlign: "right", fontVariantNumeric: "tabular-nums" }, children: [
            /* @__PURE__ */ t("span", { style: { fontWeight: 600 }, children: [
              D(r.miles),
              " miles"
            ] }),
            /* @__PURE__ */ t("span", { style: { color: "var(--muted)" }, children: [
              " + ",
              y(r.taxes)
            ] })
          ] })
        ] }),
        /* @__PURE__ */ t("div", { style: { color: "var(--muted)", fontSize: "0.8rem" }, children: [
          r.remainingSeats,
          " seats · ",
          u(r.totalDurationMinutes),
          " ·",
          " ",
          /* @__PURE__ */ t("span", { title: r.updatedAt, children: [
            "checked ",
            I(r.updatedAt)
          ] })
        ] }),
        /* @__PURE__ */ n("div", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: r.segments.map((l, d) => {
          const c = r.segments[d + 1], a = c && l.destination === c.origin;
          return /* @__PURE__ */ t("div", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: [
            /* @__PURE__ */ n(R, { seg: l }),
            c && (a ? /* @__PURE__ */ t(
              "div",
              {
                style: {
                  color: "var(--muted)",
                  fontSize: "0.75rem",
                  paddingLeft: "0.6rem",
                  borderLeft: "2px dashed var(--border)"
                },
                children: [
                  u(b(c.departsAt) - b(l.arrivesAt)),
                  " layover in",
                  " ",
                  l.destination
                ]
              }
            ) : /* @__PURE__ */ n("div", { style: { borderTop: "1px solid var(--border)" } }))
          ] }, l.flightNumber + l.departsAt);
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
const S = {
  fontFamily: "var(--font-mono)",
  fontSize: "1.6rem",
  fontWeight: 600,
  lineHeight: 1.1
}, h = { color: "var(--muted)", fontSize: "0.8rem" }, W = {
  fontSize: "0.7rem",
  textTransform: "capitalize",
  padding: "0.1rem 0.45rem",
  borderRadius: "var(--radius-md)",
  color: "var(--muted)",
  border: "1px solid var(--border)"
};
function T({ block: e }) {
  const r = e, i = z(r.departsAt, r.arrivesAt);
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
            children: y(r.price)
          }
        ),
        /* @__PURE__ */ t("div", { style: { display: "flex", alignItems: "flex-end", gap: "1rem" }, children: [
          /* @__PURE__ */ t("div", { style: { textAlign: "center" }, children: [
            /* @__PURE__ */ n("div", { style: S, children: p(r.departsAt) }),
            /* @__PURE__ */ n("div", { style: h, children: r.origin })
          ] }),
          /* @__PURE__ */ t("div", { style: { flex: 1, textAlign: "center", paddingBottom: "0.5rem" }, children: [
            /* @__PURE__ */ n("div", { style: { color: "var(--muted)", fontSize: "0.75rem", marginBottom: "0.2rem" }, children: u(r.durationMinutes) }),
            /* @__PURE__ */ n("div", { style: { borderTop: "1px solid var(--border)" } })
          ] }),
          /* @__PURE__ */ t("div", { style: { textAlign: "center" }, children: [
            /* @__PURE__ */ t("div", { style: S, children: [
              p(r.arrivesAt),
              i && /* @__PURE__ */ n("sup", { style: { color: "var(--muted)", fontSize: "0.9rem", marginLeft: "0.15rem" }, children: i })
            ] }),
            /* @__PURE__ */ n("div", { style: h, children: r.destination })
          ] })
        ] }),
        /* @__PURE__ */ t("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ n("span", { style: { fontFamily: "var(--font-mono)", fontSize: "0.85rem" }, children: r.flightNumber }),
          /* @__PURE__ */ n("span", { style: W, children: r.cabin }),
          r.aircraft && /* @__PURE__ */ n("span", { style: h, children: r.aircraft })
        ] })
      ]
    }
  );
}
const A = window.CcPresent;
if (!A)
  throw new Error("cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it");
const M = A.React, { createElement: B, Fragment: $, useCallback: G, useEffect: H, useMemo: V, useRef: Z, useState: _ } = M, j = ["economy", "premium", "business", "first"], L = {
  fontFamily: "var(--font-mono)",
  fontSize: "0.7rem",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  padding: "0.1rem 0.5rem",
  borderRadius: "999px",
  color: "var(--accent)",
  background: "color-mix(in srgb, var(--accent) 12%, var(--surface))",
  border: "1px solid color-mix(in srgb, var(--accent) 30%, var(--border))"
}, E = {
  fontSize: "0.7rem",
  textTransform: "capitalize",
  color: "var(--muted)",
  textAlign: "center",
  padding: "0 0.25rem"
};
function P({ block: e, value: r, submit: i, disabled: o }) {
  const l = e, d = r, c = j.filter((a) => l.rows.some((s) => s.cabins[a]));
  return /* @__PURE__ */ t("div", { style: { display: "flex", flexDirection: "column", gap: "0.75rem", color: "var(--text)" }, children: [
    /* @__PURE__ */ t("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ t("span", { style: { fontWeight: 700, fontSize: "1.05rem" }, children: [
        l.origin,
        " → ",
        l.destination
      ] }),
      l.program && /* @__PURE__ */ n("span", { style: L, children: l.program })
    ] }),
    /* @__PURE__ */ t(
      "div",
      {
        style: {
          display: "grid",
          gridTemplateColumns: `auto repeat(${c.length}, minmax(0, 1fr))`,
          gap: "0.4rem",
          alignItems: "stretch"
        },
        children: [
          /* @__PURE__ */ n("div", {}),
          c.map((a) => /* @__PURE__ */ n("div", { style: E, children: a }, a)),
          l.rows.map((a) => /* @__PURE__ */ t($, { children: [
            /* @__PURE__ */ n(
              "div",
              {
                title: a.date,
                style: { alignSelf: "center", fontSize: "0.8rem", color: "var(--muted)", whiteSpace: "nowrap" },
                children: k(a.date)
              }
            ),
            c.map((s) => {
              const m = a.cabins[s];
              if (!m)
                return /* @__PURE__ */ n("div", { style: { textAlign: "center", color: "var(--muted)", alignSelf: "center" }, children: "—" }, s);
              const f = d?.date === a.date && d?.cabin === s;
              return /* @__PURE__ */ t(
                "button",
                {
                  type: "button",
                  disabled: o,
                  "aria-pressed": f,
                  onClick: () => i({ date: a.date, cabin: s }),
                  style: {
                    display: "flex",
                    flexDirection: "column",
                    gap: "0.15rem",
                    padding: "0.4rem 0.3rem",
                    cursor: o ? "not-allowed" : "pointer",
                    borderRadius: "var(--radius-md)",
                    border: `1px solid ${f ? "var(--accent)" : "var(--border)"}`,
                    background: f ? "color-mix(in srgb, var(--accent) 14%, var(--surface))" : "var(--surface)",
                    color: "var(--text)",
                    opacity: o && !f ? 0.55 : 1
                  },
                  children: [
                    /* @__PURE__ */ n("span", { style: { fontFamily: "var(--font-mono)", fontSize: "0.85rem", fontWeight: 600 }, children: w(m.miles) }),
                    /* @__PURE__ */ t("span", { style: { fontSize: "0.68rem", color: "var(--muted)" }, children: [
                      m.seats,
                      " seats",
                      m.direct ? " · nonstop" : ""
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
const O = {
  fontSize: "0.7rem",
  textTransform: "capitalize",
  padding: "0.1rem 0.45rem",
  borderRadius: "var(--radius-md)",
  color: "var(--muted)",
  border: "1px solid var(--border)"
};
function U({ block: e, value: r, submit: i, disabled: o }) {
  const l = e, d = r, c = `${e.id}-label`;
  return /* @__PURE__ */ t("div", { style: { display: "flex", flexDirection: "column", gap: "0.6rem", color: "var(--text)" }, children: [
    /* @__PURE__ */ n("div", { id: c, style: { fontWeight: 700, fontSize: "1.05rem" }, children: l.label }),
    /* @__PURE__ */ n("div", { role: "radiogroup", "aria-labelledby": c, style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: l.options.map((a) => {
      const s = d?.optionId === a.optionId;
      return /* @__PURE__ */ t(
        "button",
        {
          type: "button",
          role: "radio",
          "aria-checked": s,
          disabled: o,
          onClick: () => i({ optionId: a.optionId }),
          style: {
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "1rem",
            width: "100%",
            textAlign: "left",
            padding: "0.6rem 0.75rem",
            cursor: o ? "not-allowed" : "pointer",
            borderRadius: "var(--radius-md)",
            border: `1px solid ${s ? "var(--accent)" : "var(--border)"}`,
            background: s ? "color-mix(in srgb, var(--accent) 14%, var(--surface))" : "var(--surface)",
            color: "var(--text)",
            opacity: o && !s ? 0.55 : 1
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
                k(a.date)
              ] })
            ] }),
            /* @__PURE__ */ t("span", { style: { display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.15rem" }, children: [
              /* @__PURE__ */ n("span", { style: { fontSize: "0.78rem", color: "var(--muted)" }, children: a.program }),
              /* @__PURE__ */ t("span", { style: { fontFamily: "var(--font-mono)", fontSize: "0.85rem", fontWeight: 600 }, children: [
                w(a.miles),
                " + ",
                y(a.taxes)
              ] }),
              /* @__PURE__ */ t("span", { style: { display: "flex", alignItems: "center", gap: "0.35rem" }, children: [
                a.cabin && /* @__PURE__ */ n("span", { style: O, children: a.cabin }),
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
const q = {
  hostApi: 1,
  blocks: {
    itinerary: F,
    flight: T,
    availability: P,
    "option-picker": U
  }
};
export {
  q as default
};
