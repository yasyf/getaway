// The host surface a pack sees: the window.CcPresent contract (hostApi 1), the
// component prop shape, and thin accessors for the UI helpers. Copy this file
// verbatim into your own pack.

export interface CcPresentHost {
  hostApi: 1;
  React: typeof import('react');
  jsxRuntime: typeof import('react/jsx-runtime');
  reactDom: { createPortal: (...args: unknown[]) => unknown };
  ui: {
    renderMarkdown: (md: string) => string;
    renderInlineMarkdown: (md: string) => string;
    Clamped: import('react').ComponentType<{ lines?: number; children?: import('react').ReactNode }>;
  };
}

// PackBlock is the opaque block object a component renders: id and dotted type are
// known, every other field is pack-defined.
export interface PackBlock {
  id: string;
  type: string;
  [key: string]: unknown;
}

// PackComponentProps is what the host calls every pack component with.
export interface PackComponentProps {
  block: PackBlock;
  value: unknown;
  submit: (payload: unknown) => void;
  disabled: boolean;
}

function requireHost(): CcPresentHost {
  const host = window.CcPresent;
  if (!host) {
    throw new Error('cc-present: window.CcPresent unavailable; the host must install it before a pack bundle loads');
  }
  return host;
}

// renderMarkdown turns Markdown into the host's sanitized HTML string.
export function renderMarkdown(md: string): string {
  return requireHost().ui.renderMarkdown(md);
}
