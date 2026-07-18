const A = window.CcPresent;
if (!A)
  throw new Error("cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it");
const r = A.jsxRuntime.jsx, s = A.jsxRuntime.jsxs, v = A.jsxRuntime.Fragment;
function E() {
  const e = window.CcPresent;
  if (!e)
    throw new Error("cc-present: window.CcPresent unavailable; the host must install it before a pack bundle loads");
  return e;
}
function p() {
  return E().ui.tokens;
}
function le(e) {
  E().ui.toast(e);
}
function R(e, t) {
  return E().ui.usePackState(e, t);
}
const ce = {
  suite: "ok",
  solid: "ok",
  dated: "warn",
  barely: "danger",
  verify: "neutral"
};
function S(e) {
  return {
    display: "flex",
    flexDirection: "column",
    gap: "0.75rem",
    width: "100%",
    boxSizing: "border-box",
    padding: "1rem",
    background: e.surface,
    color: e.text,
    border: `1px solid ${e.border}`,
    borderRadius: e.radiusLg
  };
}
function I(e) {
  return {
    fontFamily: e.fontMono,
    fontSize: "0.7rem",
    letterSpacing: e.trackCaps,
    textTransform: "uppercase",
    color: e.dim
  };
}
function re(e, t) {
  return t === "ok" ? e.ok : t === "warn" ? e.warn : t === "danger" ? e.danger : e.dim;
}
function _({ children: e, style: t }) {
  const n = p();
  return /* @__PURE__ */ r("span", { style: { ...I(n), ...t }, children: e });
}
function B({ children: e }) {
  const t = p();
  return /* @__PURE__ */ r(
    "span",
    {
      style: {
        fontFamily: t.fontMono,
        fontSize: "0.7rem",
        textTransform: "uppercase",
        letterSpacing: t.trackCaps,
        padding: "0.1rem 0.5rem",
        borderRadius: "999px",
        color: t.accent,
        background: `color-mix(in srgb, ${t.accent} 12%, ${t.surface})`,
        border: `1px solid color-mix(in srgb, ${t.accent} 30%, ${t.border})`
      },
      children: e
    }
  );
}
function k({ children: e, tone: t = "neutral" }) {
  const n = p(), i = re(n, t);
  return /* @__PURE__ */ r(
    "span",
    {
      style: {
        fontSize: "0.7rem",
        textTransform: "capitalize",
        padding: "0.1rem 0.45rem",
        borderRadius: n.radiusMd,
        color: i,
        border: `1px solid ${t === "neutral" ? n.border : `color-mix(in srgb, ${i} 45%, ${n.border})`}`
      },
      children: e
    }
  );
}
function O({ tone: e, children: t }) {
  const n = p(), i = re(n, e);
  return /* @__PURE__ */ r(
    "span",
    {
      style: {
        fontFamily: n.fontMono,
        fontSize: "0.68rem",
        textTransform: "uppercase",
        letterSpacing: n.trackCaps,
        padding: "0.1rem 0.45rem",
        borderRadius: "999px",
        color: i,
        background: `color-mix(in srgb, ${i} 14%, ${n.surface})`,
        border: `1px solid color-mix(in srgb, ${i} 45%, ${n.border})`
      },
      children: t
    }
  );
}
function L({ verdict: e }) {
  return /* @__PURE__ */ r(O, { tone: ce[e], children: e });
}
function M({ href: e, children: t, primary: n }) {
  const i = p(), o = {
    fontFamily: i.fontProse,
    fontSize: "0.85rem",
    textDecoration: "none",
    cursor: "pointer"
  }, l = n ? {
    ...o,
    display: "inline-block",
    padding: "0.5rem 0.9rem",
    borderRadius: i.radiusMd,
    background: i.accent,
    color: i.accentFg,
    border: `1px solid ${i.accent}`
  } : { ...o, color: i.accent };
  return /* @__PURE__ */ r("a", { href: e, target: "_blank", rel: "noopener noreferrer", style: l, children: t });
}
function Q({
  label: e,
  children: t,
  defaultOpen: n
}) {
  const i = p(), [o, l] = R("open", n ?? !1);
  return /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.4rem" }, children: [
    /* @__PURE__ */ s(
      "button",
      {
        type: "button",
        "aria-expanded": o,
        onClick: () => l(!o),
        style: {
          ...I(i),
          display: "inline-flex",
          alignItems: "center",
          gap: "0.3rem",
          width: "fit-content",
          background: "transparent",
          border: "none",
          padding: 0,
          cursor: "pointer"
        },
        children: [
          o ? "hide" : e,
          /* @__PURE__ */ r(
            "span",
            {
              "aria-hidden": !0,
              style: {
                display: "inline-block",
                transition: "transform 120ms ease",
                transform: o ? "rotate(180deg)" : "rotate(0deg)"
              },
              children: "▾"
            }
          )
        ]
      }
    ),
    o && /* @__PURE__ */ r("div", { children: t })
  ] });
}
const ie = window.CcPresent;
if (!ie)
  throw new Error("cc-present: window.CcPresent unavailable; a pack bundle loaded before the host installed it");
const de = ie.React, { createElement: Ee, Fragment: me, useCallback: pe, useEffect: _e, useMemo: Be, useRef: Oe, useState: oe } = de;
function U(e, t, n) {
  return {
    minWidth: "4rem",
    padding: "0.4rem 0.8rem",
    fontFamily: e.fontProse,
    fontSize: "0.85rem",
    borderRadius: e.radiusMd,
    border: `1px solid ${t ? e.accent : e.border}`,
    background: t ? e.accent : e.surface,
    color: t ? e.accentFg : e.text,
    cursor: n ? "not-allowed" : "pointer",
    opacity: n ? 0.55 : 1
  };
}
function q({ label: e, disabled: t, onClick: n }) {
  const i = p(), [o, l] = oe(!1);
  return /* @__PURE__ */ r(
    "button",
    {
      type: "button",
      disabled: t,
      onClick: n,
      onMouseEnter: () => l(!0),
      onMouseLeave: () => l(!1),
      style: {
        ...I(i),
        color: o && !t ? i.text : i.dim,
        width: "fit-content",
        background: "transparent",
        border: "none",
        padding: 0,
        cursor: t ? "not-allowed" : "pointer"
      },
      children: e
    }
  );
}
function z({ value: e, submit: t, disabled: n, context: i }) {
  const o = p(), l = e?.note, [a, c] = R("note.open", !1), [d, f] = R("note.draft", l ?? ""), h = n || d.trim() === "", u = pe(() => {
    const m = d.trim();
    !m || n || (t({ ...e ?? {}, note: m }), le({ kind: "info", text: "Note sent" }), c(!1));
  }, [d, n, e, t, c]);
  return i.closed || i.roundOver ? null : a ? /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.4rem" }, children: [
    /* @__PURE__ */ r(
      "textarea",
      {
        rows: 2,
        maxLength: 2e3,
        value: d,
        placeholder: "Add a note for this option…",
        disabled: n,
        onChange: (m) => f(m.target.value),
        onKeyDown: (m) => {
          (m.metaKey || m.ctrlKey) && m.key === "Enter" ? (m.preventDefault(), u()) : m.key === "Escape" && (m.preventDefault(), c(!1));
        },
        style: {
          width: "100%",
          boxSizing: "border-box",
          resize: "vertical",
          padding: "0.5rem 0.65rem",
          fontFamily: o.fontProse,
          fontSize: "0.9rem",
          color: o.text,
          background: o.bg,
          border: `1px solid ${o.border}`,
          borderRadius: o.radiusMd
        }
      }
    ),
    /* @__PURE__ */ s("div", { style: { display: "flex", gap: "0.5rem" }, children: [
      /* @__PURE__ */ r("button", { type: "button", disabled: h, onClick: u, style: U(o, !0, h), children: "Send" }),
      /* @__PURE__ */ r("button", { type: "button", onClick: () => c(!1), style: U(o, !1, !1), children: "Cancel" })
    ] })
  ] }) : l ? /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.3rem" }, children: [
    /* @__PURE__ */ r(_, { children: "your note" }),
    /* @__PURE__ */ r(
      "div",
      {
        style: {
          fontFamily: o.fontProse,
          fontSize: "0.85rem",
          color: o.text,
          borderLeft: `2px solid ${o.border}`,
          paddingLeft: "0.6rem"
        },
        children: l
      }
    ),
    /* @__PURE__ */ r(
      q,
      {
        label: "edit",
        disabled: n,
        onClick: () => {
          f(l), c(!0);
        }
      }
    )
  ] }) : /* @__PURE__ */ r(q, { label: "add note", disabled: n, onClick: () => c(!0) });
}
function $({ amount: e, currency: t }) {
  const n = new Intl.NumberFormat(void 0, { style: "currency", currency: t }), i = n.resolvedOptions().maximumFractionDigits;
  return n.format(e / 10 ** i);
}
function se(e) {
  return new Intl.NumberFormat(void 0, { notation: "compact", maximumFractionDigits: 1 }).format(e);
}
function b(e) {
  return new Intl.NumberFormat(void 0, { useGrouping: !0 }).format(e);
}
function H(e) {
  const t = /* @__PURE__ */ new Map();
  for (const { amount: n, currency: i } of e)
    t.set(i, (t.get(i) ?? 0) + n);
  return [...t].map(([n, i]) => $({ amount: i, currency: n })).join(" + ");
}
function fe(e) {
  return e.map(({ program: t, miles: n }) => `${t} ${b(n)}`).join(" · ");
}
function C(e) {
  const t = Math.floor(e / 60), n = e % 60;
  return `${t}h ${String(n).padStart(2, "0")}m`;
}
function w(e) {
  return e.slice(11, 16);
}
function V(e, t) {
  const n = J(t) - J(e);
  return n === 0 ? "" : n > 0 ? `+${n}` : String(n);
}
function T(e) {
  const t = Number(e.slice(5, 7)), n = Number(e.slice(8, 10));
  return `${new Intl.DateTimeFormat(void 0, { weekday: "short", timeZone: "UTC" }).format(
    Date.UTC(Number(e.slice(0, 4)), t - 1, n)
  )} ${t}/${n}`;
}
function P(e) {
  const t = new Intl.RelativeTimeFormat(void 0, { numeric: "auto" }), n = Math.round((Date.parse(e) - Date.now()) / 6e4);
  if (Math.abs(n) < 60) return t.format(n, "minute");
  const i = Math.round(n / 60);
  return Math.abs(i) < 24 ? t.format(i, "hour") : t.format(Math.round(i / 24), "day");
}
function J(e) {
  return Date.UTC(Number(e.slice(0, 4)), Number(e.slice(5, 7)) - 1, Number(e.slice(8, 10))) / 864e5;
}
const W = { barely: 2, dated: 1, suite: 0, solid: 0, verify: 0 };
function Z(e) {
  return { fontFamily: e.fontMono, fontSize: "0.85rem" };
}
function X(e) {
  return Date.UTC(
    Number(e.slice(0, 4)),
    Number(e.slice(5, 7)) - 1,
    Number(e.slice(8, 10)),
    Number(e.slice(11, 13)),
    Number(e.slice(14, 16))
  ) / 6e4;
}
function ue(e) {
  let t = null;
  for (const { seatQuality: n } of e) {
    const i = n?.verdict;
    i && W[i] > 0 && (t === null || W[i] > W[t]) && (t = i);
  }
  return t;
}
function G(e, t) {
  return e.destination === t.origin;
}
function he(e) {
  const t = e.slice(0, -1).filter((n, i) => G(n, e[i + 1]));
  return t.length === 0 ? "nonstop" : t.length === 1 ? `1 stop (${t[0].destination})` : `${t.length} stops`;
}
function ye({ seg: e }) {
  const t = p(), n = V(e.departsAt, e.arrivesAt);
  return /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "baseline", gap: "0.6rem", flexWrap: "wrap" }, children: [
    /* @__PURE__ */ r("span", { style: Z(t), children: e.flightNumber }),
    /* @__PURE__ */ s("span", { children: [
      e.origin,
      " → ",
      e.destination
    ] }),
    /* @__PURE__ */ s("span", { style: Z(t), children: [
      w(e.departsAt),
      " → ",
      w(e.arrivesAt),
      n && /* @__PURE__ */ r("sup", { style: { color: t.dim, marginLeft: "0.15rem" }, children: n })
    ] }),
    /* @__PURE__ */ r(k, { children: e.cabin }),
    /* @__PURE__ */ s("span", { style: { color: t.dim, fontSize: "0.8rem" }, children: [
      e.aircraft,
      e.aircraftCode ? ` (${e.aircraftCode})` : ""
    ] }),
    e.seatQuality && /* @__PURE__ */ s(
      "span",
      {
        title: e.seatQuality.note ?? void 0,
        style: { display: "inline-flex", alignItems: "center", gap: "0.3rem" },
        children: [
          /* @__PURE__ */ r(L, { verdict: e.seatQuality.verdict }),
          e.seatQuality.product && /* @__PURE__ */ r("span", { style: { color: t.dim, fontSize: "0.8rem" }, children: e.seatQuality.product })
        ]
      }
    ),
    /* @__PURE__ */ r("span", { style: { marginLeft: "auto", color: t.dim, fontSize: "0.8rem" }, children: C(e.durationMinutes) })
  ] });
}
function ge({ segments: e }) {
  const t = p();
  return /* @__PURE__ */ r("div", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: e.map((n, i) => {
    const o = e[i + 1], l = o && G(n, o);
    return /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: [
      /* @__PURE__ */ r(ye, { seg: n }),
      o && (l ? /* @__PURE__ */ s(
        "div",
        {
          style: {
            color: t.dim,
            fontSize: "0.75rem",
            paddingLeft: "0.6rem",
            borderLeft: `2px dashed ${t.border}`
          },
          children: [
            C(X(o.departsAt) - X(n.arrivesAt)),
            " layover in",
            " ",
            n.destination
          ]
        }
      ) : /* @__PURE__ */ r("div", { style: { borderTop: `1px solid ${t.border}` } }))
    ] }, n.flightNumber + n.departsAt);
  }) });
}
function xe({ block: e, value: t, submit: n, disabled: i, context: o }) {
  const l = p(), a = e, c = a.segments[0], d = a.segments[a.segments.length - 1], f = a.segments.slice(0, -1).some((y, N) => !G(y, a.segments[N + 1])), h = ue(a.segments), u = H(a.taxes), m = a.bookingLinks.find((y) => y.primary), F = a.bookingLinks.filter((y) => y !== m);
  return /* @__PURE__ */ s("div", { style: { ...S(l), opacity: o.closed ? 0.6 : 1, transition: "opacity 120ms ease" }, children: [
    /* @__PURE__ */ s("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
        /* @__PURE__ */ s("span", { style: { fontWeight: 700, fontSize: "1.05rem" }, children: [
          c.origin,
          " → ",
          d.destination
        ] }),
        /* @__PURE__ */ r(B, { children: a.program })
      ] }),
      /* @__PURE__ */ s(
        "div",
        {
          style: {
            marginLeft: "auto",
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-end",
            fontFamily: l.fontMono,
            fontVariantNumeric: "tabular-nums"
          },
          children: [
            /* @__PURE__ */ s("span", { style: { fontWeight: 600, fontSize: "0.95rem" }, children: [
              b(a.miles),
              " miles"
            ] }),
            u ? /* @__PURE__ */ s("span", { style: { color: l.dim, fontSize: "0.8rem" }, children: [
              "+ ",
              u
            ] }) : a.taxesNote ? /* @__PURE__ */ r("span", { style: { color: l.dim, fontSize: "0.8rem", fontFamily: l.fontProse }, children: a.taxesNote }) : null
          ]
        }
      )
    ] }),
    /* @__PURE__ */ s("div", { style: { display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.4rem", color: l.dim, fontSize: "0.8rem" }, children: [
      /* @__PURE__ */ r("span", { children: C(a.totalDurationMinutes) }),
      /* @__PURE__ */ r("span", { "aria-hidden": !0, children: "·" }),
      /* @__PURE__ */ r("span", { children: he(a.segments) }),
      f && /* @__PURE__ */ s(v, { children: [
        /* @__PURE__ */ r("span", { "aria-hidden": !0, children: "·" }),
        /* @__PURE__ */ r("span", { children: "open jaw" })
      ] }),
      /* @__PURE__ */ r("span", { "aria-hidden": !0, children: "·" }),
      /* @__PURE__ */ s("span", { children: [
        a.remainingSeats,
        " ",
        a.remainingSeats === 1 ? "seat" : "seats"
      ] }),
      h && /* @__PURE__ */ s(v, { children: [
        /* @__PURE__ */ r("span", { "aria-hidden": !0, children: "·" }),
        /* @__PURE__ */ r(L, { verdict: h })
      ] }),
      /* @__PURE__ */ r("span", { "aria-hidden": !0, children: "·" }),
      /* @__PURE__ */ s("span", { title: a.fetchedAt, children: [
        "checked ",
        P(a.fetchedAt)
      ] })
    ] }),
    /* @__PURE__ */ r(Q, { label: "flights & booking", children: /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.75rem" }, children: [
      /* @__PURE__ */ r(ge, { segments: a.segments }),
      /* @__PURE__ */ s("div", { style: { display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.75rem" }, children: [
        m && /* @__PURE__ */ r(M, { href: m.url, primary: !0, children: m.label }),
        F.map((y) => /* @__PURE__ */ r(M, { href: y.url, children: y.label }, y.url))
      ] }),
      a.taxesNote && /* @__PURE__ */ r("div", { style: { color: l.dim, fontSize: "0.78rem" }, children: a.taxesNote })
    ] }) }),
    /* @__PURE__ */ r(z, { value: t, submit: n, disabled: i, context: o })
  ] });
}
function be({ block: e, value: t, submit: n, disabled: i, context: o }) {
  const l = p(), a = e, c = V(a.departsAt, a.arrivesAt), d = { fontFamily: l.fontMono, fontSize: "1.6rem", fontWeight: 600, lineHeight: 1.1 }, f = { color: l.dim, fontSize: "0.8rem" };
  return /* @__PURE__ */ s("div", { style: { ...S(l), position: "relative" }, children: [
    a.price && /* @__PURE__ */ r(
      "span",
      {
        style: {
          position: "absolute",
          top: "0.7rem",
          right: "0.85rem",
          fontWeight: 600,
          fontSize: "0.85rem",
          padding: "0.1rem 0.5rem",
          borderRadius: "999px",
          color: l.accent,
          background: `color-mix(in srgb, ${l.accent} 12%, ${l.surface})`
        },
        children: $(a.price)
      }
    ),
    /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "flex-end", gap: "1rem" }, children: [
      /* @__PURE__ */ s("div", { style: { textAlign: "center" }, children: [
        /* @__PURE__ */ r("div", { style: d, children: w(a.departsAt) }),
        /* @__PURE__ */ r("div", { style: f, children: a.origin })
      ] }),
      /* @__PURE__ */ s("div", { style: { flex: 1, textAlign: "center", paddingBottom: "0.5rem" }, children: [
        /* @__PURE__ */ r("div", { style: { color: l.dim, fontSize: "0.75rem", marginBottom: "0.2rem" }, children: C(a.durationMinutes) }),
        /* @__PURE__ */ r("div", { style: { borderTop: `1px solid ${l.border}` } })
      ] }),
      /* @__PURE__ */ s("div", { style: { textAlign: "center" }, children: [
        /* @__PURE__ */ s("div", { style: d, children: [
          w(a.arrivesAt),
          c && /* @__PURE__ */ r("sup", { style: { color: l.dim, fontSize: "0.9rem", marginLeft: "0.15rem" }, children: c })
        ] }),
        /* @__PURE__ */ r("div", { style: f, children: a.destination })
      ] })
    ] }),
    /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ r("span", { style: { fontFamily: l.fontMono, fontSize: "0.85rem" }, children: a.flightNumber }),
      /* @__PURE__ */ r(k, { children: a.cabin }),
      a.aircraft && /* @__PURE__ */ r("span", { style: f, children: a.aircraft }),
      a.aircraftCode && /* @__PURE__ */ r("span", { style: { ...f, fontFamily: l.fontMono }, children: a.aircraftCode }),
      a.seatQuality && /* @__PURE__ */ s(
        "span",
        {
          title: a.seatQuality.note ?? void 0,
          style: { display: "inline-flex", alignItems: "center", gap: "0.3rem" },
          children: [
            /* @__PURE__ */ r(L, { verdict: a.seatQuality.verdict }),
            a.seatQuality.product && /* @__PURE__ */ r("span", { style: { color: l.dim, fontSize: "0.8rem" }, children: a.seatQuality.product })
          ]
        }
      )
    ] }),
    /* @__PURE__ */ r(z, { value: t, submit: n, disabled: i, context: o })
  ] });
}
const ve = ["economy", "premium", "business", "first"], Y = 10;
function we(e, t) {
  const n = `${e.length} ${e.length === 1 ? "date" : "dates"}`, i = t[t.length - 1];
  if (!i) return n;
  let o = 1 / 0;
  for (const l of e) {
    const a = l.cabins[i];
    a && a.miles < o && (o = a.miles);
  }
  return `${n} · ${i} from ${se(o)}`;
}
function Se({ label: e, disabled: t, onClick: n }) {
  const i = p(), [o, l] = oe(!1);
  return /* @__PURE__ */ r(
    "button",
    {
      type: "button",
      disabled: t,
      onClick: n,
      onMouseEnter: () => l(!0),
      onMouseLeave: () => l(!1),
      style: {
        ...I(i),
        color: o && !t ? i.text : i.dim,
        width: "fit-content",
        background: "transparent",
        border: "none",
        padding: 0,
        cursor: t ? "not-allowed" : "pointer"
      },
      children: e
    }
  );
}
function ke({
  cell: e,
  selected: t,
  locked: n,
  onToggle: i
}) {
  const o = p();
  return e ? /* @__PURE__ */ s(
    "button",
    {
      type: "button",
      disabled: n,
      "aria-pressed": t,
      onClick: i,
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "0.15rem",
        padding: "0.4rem 0.3rem",
        cursor: n ? "not-allowed" : "pointer",
        borderRadius: o.radiusMd,
        border: `1px solid ${t ? o.accent : o.border}`,
        background: t ? `color-mix(in srgb, ${o.accent} 14%, ${o.surface})` : o.surface,
        color: o.text,
        opacity: n && !t ? 0.55 : 1
      },
      children: [
        /* @__PURE__ */ s(
          "span",
          {
            style: {
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "0.2rem",
              fontFamily: o.fontMono,
              fontSize: "0.85rem",
              fontWeight: 600
            },
            children: [
              t && /* @__PURE__ */ r("span", { "aria-hidden": !0, style: { color: o.accent }, children: "✓" }),
              se(e.miles)
            ]
          }
        ),
        /* @__PURE__ */ s("span", { style: { fontSize: "0.68rem", color: o.dim }, children: [
          e.seats,
          " seats",
          e.direct ? " · nonstop" : ""
        ] })
      ]
    }
  ) : /* @__PURE__ */ r("div", { style: { textAlign: "center", color: o.dim, alignSelf: "center" }, children: "—" });
}
function ee({
  rows: e,
  present: t,
  showHeader: n,
  isPicked: i,
  toggle: o,
  locked: l
}) {
  const a = p();
  return /* @__PURE__ */ s(
    "div",
    {
      style: {
        display: "grid",
        gridTemplateColumns: `auto repeat(${t.length}, minmax(0, 1fr))`,
        gap: "0.4rem",
        alignItems: "stretch"
      },
      children: [
        n && /* @__PURE__ */ s(v, { children: [
          /* @__PURE__ */ r("div", {}),
          t.map((c) => /* @__PURE__ */ r(
            "div",
            {
              style: { fontSize: "0.7rem", textTransform: "capitalize", color: a.dim, textAlign: "center", padding: "0 0.25rem" },
              children: c
            },
            c
          ))
        ] }),
        e.map((c) => /* @__PURE__ */ s(me, { children: [
          /* @__PURE__ */ r("div", { title: c.date, style: { alignSelf: "center", fontSize: "0.8rem", color: a.dim, whiteSpace: "nowrap" }, children: T(c.date) }),
          t.map((d) => /* @__PURE__ */ r(
            ke,
            {
              cell: c.cabins[d],
              selected: i(c.date, d),
              locked: l,
              onToggle: () => o(c.date, d)
            },
            d
          ))
        ] }, c.date))
      ]
    }
  );
}
function $e({ block: e, value: t, submit: n, disabled: i, context: o }) {
  const l = p(), a = e, c = t, d = c?.picks ?? [], f = ve.filter((g) => a.rows.some((x) => x.cabins[g])), h = i || o.closed || o.roundOver, u = (g, x) => d.some((D) => D.date === g && D.cabin === x), m = (g, x) => {
    const D = u(g, x) ? d.filter((K) => !(K.date === g && K.cabin === x)) : [...d, { date: g, cabin: x }];
    n({ ...c ?? {}, picks: D });
  }, F = () => n({ ...c ?? {}, picks: [] }), y = a.rows.slice(0, Y), N = a.rows.slice(Y);
  return /* @__PURE__ */ s("div", { style: { ...S(l), opacity: o.closed ? 0.6 : 1 }, children: [
    /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.3rem" }, children: [
      /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
        /* @__PURE__ */ s("span", { style: { fontWeight: 700, fontSize: "1.05rem" }, children: [
          a.origin,
          " → ",
          a.destination
        ] }),
        a.program && /* @__PURE__ */ r(B, { children: a.program })
      ] }),
      /* @__PURE__ */ r("span", { style: { color: l.dim, fontSize: "0.8rem" }, children: we(a.rows, f) })
    ] }),
    /* @__PURE__ */ r(ee, { rows: y, present: f, showHeader: !0, isPicked: u, toggle: m, locked: h }),
    N.length > 0 && /* @__PURE__ */ r(Q, { label: `show all ${a.rows.length} dates`, children: /* @__PURE__ */ r("div", { style: { marginTop: "0.4rem" }, children: /* @__PURE__ */ r(ee, { rows: N, present: f, showHeader: !1, isPicked: u, toggle: m, locked: h }) }) }),
    /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ s(_, { children: [
        d.length,
        " picked"
      ] }),
      d.length > 0 && /* @__PURE__ */ r(Se, { label: "clear", disabled: h, onClick: F })
    ] }),
    /* @__PURE__ */ r(z, { value: t, submit: n, disabled: i, context: o })
  ] });
}
const ze = {
  bot_wall: "rooms.aero blocked the lookup with a bot wall",
  logged_out: "the rooms.aero session was not signed in",
  geocode_miss: "the destination could not be geocoded",
  date_in_past: "check-in had already passed",
  failed: "the lodging lookup did not complete"
}, Ce = {
  no_checkout: { head: "Lodging deferred", body: "No confirmed return date yet, so there is no checkout to price a stay against." },
  open_jaw_stop: { head: "Lodging deferred", body: "The next leg departs from a different airport, so there is no checkout to price a stay against." },
  date_in_past: { head: "Lodging deferred", body: "Check-in falls in the past." },
  invalid_interval: { head: "Lodging deferred", body: "The derived stay interval isn't valid." },
  not_walked: { head: "Lodging not checked", body: "This option was not walked on rooms.aero." }
};
function j(e, t) {
  const n = t === "warn" ? e.warn : e.danger;
  return {
    fontSize: "0.8rem",
    color: n,
    background: `color-mix(in srgb, ${n} 12%, ${e.surface})`,
    border: `1px solid color-mix(in srgb, ${n} 45%, ${e.border})`,
    borderRadius: e.radiusMd,
    padding: "0.5rem 0.65rem"
  };
}
function Ne(e) {
  let t = null;
  for (const n of e)
    for (const i of n.offers)
      i.pointsPerNight !== null && (!t || i.pointsPerNight < t.points) && (t = { points: i.pointsPerNight, centsPerPoint: i.centsPerPoint, name: n.name });
  return t;
}
function ae({ destination: e, airport: t, session: n }) {
  return /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
    /* @__PURE__ */ r("span", { style: { fontWeight: 700, fontSize: "1.05rem" }, children: e }),
    t && /* @__PURE__ */ r(k, { children: t }),
    n === "anonymous" && /* @__PURE__ */ r(O, { tone: "warn", children: "anonymous" })
  ] });
}
function De({ offer: e, currency: t, nights: n }) {
  const i = p(), o = [], l = [];
  return e.pointsPerNight !== null && (o.push(`${b(e.pointsPerNight)} pts`), l.push(`${b(e.pointsPerNight * n)} pts`)), e.cashPerNightCents !== null && (o.push($({ amount: e.cashPerNightCents, currency: t })), l.push($({ amount: e.cashPerNightCents * n, currency: t }))), /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.15rem" }, children: [
    /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "baseline", gap: "0.5rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ r(k, { children: e.awardClass }),
      /* @__PURE__ */ r("span", { style: { fontFamily: i.fontMono, fontSize: "0.85rem", fontWeight: 600 }, children: o.join(" + ") }),
      /* @__PURE__ */ r("span", { style: { color: i.dim, fontSize: "0.75rem" }, children: "/ night" }),
      e.centsPerPoint !== null && /* @__PURE__ */ s(
        "span",
        {
          style: {
            fontSize: "0.7rem",
            fontFamily: i.fontMono,
            padding: "0.1rem 0.45rem",
            borderRadius: i.radiusMd,
            color: i.dim,
            border: `1px solid ${i.border}`
          },
          children: [
            e.centsPerPoint.toFixed(1),
            "¢/pt"
          ]
        }
      )
    ] }),
    /* @__PURE__ */ s("div", { style: { color: i.dim, fontSize: "0.72rem" }, children: [
      "≈ ",
      l.join(" + "),
      " est. for ",
      n,
      " ",
      n === 1 ? "night" : "nights"
    ] })
  ] });
}
function Me({ room: e, nights: t }) {
  const n = p();
  return /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.4rem" }, children: [
    /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ r(B, { children: e.program }),
      /* @__PURE__ */ r("span", { style: { fontWeight: 600 }, children: e.name }),
      /* @__PURE__ */ s("span", { style: { marginLeft: "auto", display: "flex", alignItems: "center", gap: "0.4rem" }, children: [
        e.stale && /* @__PURE__ */ r(O, { tone: "warn", children: "stale" }),
        /* @__PURE__ */ s("span", { title: e.checkedAt, style: { color: n.dim, fontSize: "0.75rem" }, children: [
          "checked ",
          P(e.checkedAt)
        ] })
      ] })
    ] }),
    e.offers.map((i) => /* @__PURE__ */ r(De, { offer: i, currency: e.currency, nights: t }, i.awardClass))
  ] });
}
function Ae({ block: e, note: t, closed: n }) {
  const i = p(), { interval: o } = e, l = o.nightClamped ? o.requestedNights ? `first ${o.nights} nights of ${o.requestedNights}` : `capped at rooms.aero's ${o.nights}-night maximum` : null, a = e.rooms.length === 0 ? ze[e.searchState] : void 0, c = Ne(e.rooms);
  return /* @__PURE__ */ s("div", { style: { ...S(i), opacity: n ? 0.6 : 1 }, children: [
    /* @__PURE__ */ s("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ r(ae, { destination: e.destination, airport: e.airport, session: e.session }),
      /* @__PURE__ */ s("span", { style: { color: i.dim, fontSize: "0.8rem" }, title: e.checkedAt, children: [
        "checked ",
        P(e.checkedAt)
      ] })
    ] }),
    /* @__PURE__ */ s("div", { style: { color: i.dim, fontSize: "0.8rem" }, children: [
      T(o.checkIn),
      " → ",
      T(o.checkOut),
      " · ",
      o.nights,
      " ",
      o.nights === 1 ? "night" : "nights",
      l && /* @__PURE__ */ s("span", { style: { color: i.warn }, children: [
        " · ",
        l
      ] })
    ] }),
    e.rooms.length > 0 && /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "baseline", gap: "0.5rem", flexWrap: "wrap", fontSize: "0.85rem" }, children: [
      c && /* @__PURE__ */ s(v, { children: [
        /* @__PURE__ */ s("span", { style: { fontWeight: 600 }, children: [
          "from ",
          b(c.points),
          " pts/night"
        ] }),
        c.centsPerPoint !== null && /* @__PURE__ */ s("span", { style: { color: i.dim }, children: [
          "· ",
          c.centsPerPoint.toFixed(1),
          "¢/pt"
        ] }),
        /* @__PURE__ */ s("span", { style: { color: i.dim }, children: [
          "· ",
          c.name
        ] })
      ] }),
      /* @__PURE__ */ s("span", { style: { marginLeft: "auto", color: i.dim, fontSize: "0.78rem" }, children: [
        e.rooms.length,
        " ",
        e.rooms.length === 1 ? "property" : "properties"
      ] })
    ] }),
    e.session === "anonymous" && /* @__PURE__ */ r("div", { style: j(i, "warn"), children: "Anonymous session — these rates are not refreshed and can be weeks stale." }),
    a ? /* @__PURE__ */ s("div", { style: j(i, "danger"), children: [
      "Lodging lookup couldn't complete: ",
      a,
      "."
    ] }) : e.rooms.length === 0 ? /* @__PURE__ */ r("div", { style: { color: i.dim, fontSize: "0.85rem" }, children: "No award rooms found for this stay." }) : /* @__PURE__ */ s(v, { children: [
      /* @__PURE__ */ r(Q, { label: "show room detail", children: /* @__PURE__ */ r("div", { style: { display: "flex", flexDirection: "column", gap: "0.75rem", marginTop: "0.5rem" }, children: e.rooms.map((d, f) => /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.4rem" }, children: [
        f > 0 && /* @__PURE__ */ r("div", { style: { borderTop: `1px solid ${i.border}` } }),
        /* @__PURE__ */ r(Me, { room: d, nights: o.nights })
      ] }, d.program + d.name)) }) }),
      /* @__PURE__ */ r("div", { style: { color: i.dim, fontSize: "0.72rem" }, children: "Per-night rates are the source of truth; any stay total is an estimate." })
    ] }),
    t
  ] });
}
function Ie({ block: e, note: t, closed: n }) {
  const i = p(), { head: o, body: l } = Ce[e.reason];
  return /* @__PURE__ */ s("div", { style: { ...S(i), opacity: n ? 0.6 : 1 }, children: [
    e.destination && /* @__PURE__ */ r(ae, { destination: e.destination, airport: e.airport }),
    /* @__PURE__ */ s("div", { style: j(i, "warn"), children: [
      /* @__PURE__ */ s("span", { style: { fontWeight: 600 }, children: [
        o,
        "."
      ] }),
      " ",
      l
    ] }),
    t
  ] });
}
function Le({ block: e, value: t, submit: n, disabled: i, context: o }) {
  const l = e, a = /* @__PURE__ */ r(z, { value: t, submit: n, disabled: i, context: o });
  return l.state === "deferred" ? /* @__PURE__ */ r(Ie, { block: l, note: a, closed: o.closed }) : /* @__PURE__ */ r(Ae, { block: l, note: a, closed: o.closed });
}
function te(e) {
  return { fontFamily: e.fontMono, fontSize: "0.85rem" };
}
function Pe(e) {
  return {
    flexShrink: 0,
    width: "1.6rem",
    height: "1.6rem",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: "999px",
    fontFamily: e.fontMono,
    fontSize: "0.8rem",
    fontWeight: 600,
    color: e.accent,
    background: `color-mix(in srgb, ${e.accent} 12%, ${e.surface})`,
    border: `1px solid color-mix(in srgb, ${e.accent} 30%, ${e.border})`
  };
}
function Fe(e) {
  if (e.kind === "award") {
    const t = H(e.taxes ?? []);
    return `Book on ${e.program} — ${b(e.miles)}${t ? ` + ${t}` : ""}`;
  }
  return `Buy cash — ${$(e.price)}`;
}
function ne({ n: e, children: t }) {
  const n = p();
  return /* @__PURE__ */ s("div", { style: { display: "flex", gap: "0.6rem", alignItems: "flex-start" }, children: [
    /* @__PURE__ */ r("div", { style: Pe(n), children: e }),
    /* @__PURE__ */ r("div", { style: { flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "0.4rem" }, children: t })
  ] });
}
function We({ seat: e }) {
  const t = p(), n = e.picks ?? [], i = e.avoids ?? [];
  return n.length === 0 && i.length === 0 ? null : /* @__PURE__ */ s("span", { style: { fontSize: "0.8rem" }, children: [
    n.length > 0 && /* @__PURE__ */ s("span", { style: { color: t.ok }, children: [
      "pick",
      " ",
      n.map((o, l) => /* @__PURE__ */ s("span", { title: o.why, children: [
        l > 0 && " · ",
        o.seat
      ] }, o.seat))
    ] }),
    n.length > 0 && i.length > 0 && /* @__PURE__ */ r("span", { style: { color: t.dim }, children: " — " }),
    i.length > 0 && /* @__PURE__ */ s("span", { style: { color: t.danger }, children: [
      "avoid",
      " ",
      i.map((o, l) => /* @__PURE__ */ s("span", { title: o.why, children: [
        l > 0 && " · ",
        o.seat
      ] }, o.seat))
    ] })
  ] });
}
function Re({ flight: e }) {
  const t = p(), n = V(e.departsAt, e.arrivesAt);
  return /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.3rem" }, children: [
    /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "baseline", gap: "0.6rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ r("span", { style: te(t), children: e.flightNumber }),
      /* @__PURE__ */ s("span", { children: [
        e.origin,
        " → ",
        e.destination
      ] }),
      /* @__PURE__ */ s("span", { style: te(t), children: [
        w(e.departsAt),
        " → ",
        w(e.arrivesAt),
        n && /* @__PURE__ */ r("sup", { style: { color: t.dim, marginLeft: "0.15rem" }, children: n })
      ] }),
      e.cabin && /* @__PURE__ */ r(k, { children: e.cabin }),
      (e.aircraft || e.aircraftCode) && /* @__PURE__ */ s("span", { style: { color: t.dim, fontSize: "0.8rem" }, children: [
        e.aircraft,
        e.aircraft && e.aircraftCode ? ` (${e.aircraftCode})` : e.aircraftCode
      ] }),
      /* @__PURE__ */ r("span", { style: { marginLeft: "auto", color: t.dim, fontSize: "0.8rem" }, children: C(e.durationMinutes) })
    ] }),
    e.seat && /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.2rem", paddingLeft: "0.6rem" }, children: [
      /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
        /* @__PURE__ */ r(L, { verdict: e.seat.verdict }),
        e.seat.product && /* @__PURE__ */ r("span", { style: { color: t.dim, fontSize: "0.8rem" }, children: e.seat.product }),
        /* @__PURE__ */ r(We, { seat: e.seat })
      ] }),
      e.seat.note && /* @__PURE__ */ r("span", { style: { color: t.dim, fontSize: "0.78rem" }, children: e.seat.note })
    ] })
  ] });
}
function Te({ leg: e }) {
  const t = p(), n = e.bookingLinks.find((o) => o.primary), i = e.bookingLinks.filter((o) => o !== n);
  return /* @__PURE__ */ s(v, { children: [
    /* @__PURE__ */ s("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, children: [
      /* @__PURE__ */ r(k, { children: e.role }),
      /* @__PURE__ */ r("span", { style: { fontWeight: 600 }, children: Fe(e) })
    ] }),
    e.taxesNote && /* @__PURE__ */ r("span", { style: { color: t.dim, fontSize: "0.78rem" }, children: e.taxesNote }),
    /* @__PURE__ */ s("div", { style: { display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.75rem" }, children: [
      n && /* @__PURE__ */ r(M, { href: n.url, primary: !0, children: n.label }),
      i.map((o) => /* @__PURE__ */ r(M, { href: o.url, children: o.label }, o.url))
    ] }),
    /* @__PURE__ */ r("div", { style: { display: "flex", flexDirection: "column", gap: "0.5rem" }, children: e.flights.map((o) => /* @__PURE__ */ r(Re, { flight: o }, o.flightNumber + o.departsAt)) }),
    e.notes?.map((o, l) => /* @__PURE__ */ s("span", { style: { color: t.dim, fontSize: "0.8rem" }, children: [
      "• ",
      o
    ] }, l))
  ] });
}
function je({ block: e, value: t, submit: n, disabled: i, context: o }) {
  const l = p(), a = e, c = a.transfers ?? [], d = a.totals ? fe(a.totals.miles) : "", f = a.totals ? H(a.totals.cash) : "", h = !!a.totals && (d !== "" || f !== "");
  return /* @__PURE__ */ s("div", { style: { ...S(l), opacity: o.closed ? 0.6 : 1, transition: "opacity 120ms ease" }, children: [
    /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.2rem" }, children: [
      /* @__PURE__ */ r("span", { style: { fontWeight: 700, fontSize: "1.1rem" }, children: a.title }),
      a.subtitle && /* @__PURE__ */ r("span", { style: { color: l.dim, fontSize: "0.9rem" }, children: a.subtitle }),
      /* @__PURE__ */ s("span", { style: { color: l.dim, fontSize: "0.78rem" }, title: a.fetchedAt, children: [
        "availability checked ",
        P(a.fetchedAt)
      ] })
    ] }),
    h && /* @__PURE__ */ s(
      "div",
      {
        style: {
          display: "flex",
          flexDirection: "column",
          gap: "0.25rem",
          padding: "0.6rem 0.75rem",
          background: l.surfaceRaised,
          border: `1px solid ${l.border}`,
          borderRadius: l.radiusMd,
          fontFamily: l.fontMono,
          fontVariantNumeric: "tabular-nums",
          fontSize: "0.85rem"
        },
        children: [
          /* @__PURE__ */ r(_, { children: "total" }),
          d && /* @__PURE__ */ r("span", { children: d }),
          f && /* @__PURE__ */ r("span", { style: { color: l.dim }, children: f })
        ]
      }
    ),
    /* @__PURE__ */ s("div", { style: { display: "flex", flexDirection: "column", gap: "0.9rem" }, children: [
      c.map((u, m) => /* @__PURE__ */ s(ne, { n: m + 1, children: [
        /* @__PURE__ */ s("span", { style: { fontWeight: 600 }, children: [
          "Transfer ",
          b(u.amount),
          " ",
          u.from,
          " → ",
          u.to
        ] }),
        u.note && /* @__PURE__ */ r("span", { style: { color: l.dim, fontSize: "0.8rem" }, children: u.note })
      ] }, `transfer-${m}`)),
      a.legs.map((u, m) => /* @__PURE__ */ r(ne, { n: c.length + m + 1, children: /* @__PURE__ */ r(Te, { leg: u }) }, `leg-${m}`))
    ] }),
    /* @__PURE__ */ r(z, { value: t, submit: n, disabled: i, context: o })
  ] });
}
const Qe = {
  hostApi: 2,
  blocks: {
    itinerary: xe,
    flight: be,
    availability: $e,
    stay: Le,
    booking: je
  }
};
export {
  Qe as default
};
