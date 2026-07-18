import type { CSSProperties } from 'react';
import { useCallback, useState } from 'react';
import type { PackComponentProps, ThemeTokens } from './host/present';
import { toast, tokens, usePackState } from './host/present';
import { CapsLabel, capsStyle } from './ui';

type NoteBarProps = Pick<PackComponentProps, 'value' | 'submit' | 'disabled' | 'context'>;

function actionButton(t: ThemeTokens, primary: boolean, disabled: boolean): CSSProperties {
  return {
    minWidth: '4rem',
    padding: '0.4rem 0.8rem',
    fontFamily: t.fontProse,
    fontSize: '0.85rem',
    borderRadius: t.radiusMd,
    border: `1px solid ${primary ? t.accent : t.border}`,
    background: primary ? t.accent : t.surface,
    color: primary ? t.accentFg : t.text,
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.55 : 1,
  };
}

// A CapsLabel-styled affordance: dim at rest, text tone on hover.
function GhostButton({ label, disabled, onClick }: { label: string; disabled: boolean; onClick: () => void }) {
  const t = tokens();
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        ...capsStyle(t),
        color: hover && !disabled ? t.text : t.dim,
        width: 'fit-content',
        background: 'transparent',
        border: 'none',
        padding: 0,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
    >
      {label}
    </button>
  );
}

// The per-block comment affordance every block mounts in its footer; its note
// merges into the block value so sibling fields survive the upsert.
export function NoteBar({ value, submit, disabled, context }: NoteBarProps) {
  const t = tokens();
  const committedNote = (value as { note?: string } | null)?.note;
  const [open, setOpen] = usePackState<boolean>('note.open', false);
  const [draft, setDraft] = usePackState<string>('note.draft', committedNote ?? '');

  const sendDisabled = disabled || draft.trim() === '';

  const onSend = useCallback(() => {
    const note = draft.trim();
    if (!note || disabled) return;
    submit({ ...((value as object) ?? {}), note });
    toast({ kind: 'info', text: 'Note sent' });
    setOpen(false);
  }, [draft, disabled, value, submit, setOpen]);

  if (context.closed || context.roundOver) return null;

  if (open) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
        <textarea
          rows={2}
          maxLength={2000}
          value={draft}
          placeholder="Add a note for this option…"
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault();
              onSend();
            } else if (e.key === 'Escape') {
              e.preventDefault();
              setOpen(false);
            }
          }}
          style={{
            width: '100%',
            boxSizing: 'border-box',
            resize: 'vertical',
            padding: '0.5rem 0.65rem',
            fontFamily: t.fontProse,
            fontSize: '0.9rem',
            color: t.text,
            background: t.bg,
            border: `1px solid ${t.border}`,
            borderRadius: t.radiusMd,
          }}
        />
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button type="button" disabled={sendDisabled} onClick={onSend} style={actionButton(t, true, sendDisabled)}>
            Send
          </button>
          <button type="button" onClick={() => setOpen(false)} style={actionButton(t, false, false)}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  if (committedNote) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
        <CapsLabel>your note</CapsLabel>
        <div
          style={{
            fontFamily: t.fontProse,
            fontSize: '0.85rem',
            color: t.text,
            borderLeft: `2px solid ${t.border}`,
            paddingLeft: '0.6rem',
          }}
        >
          {committedNote}
        </div>
        <GhostButton
          label="edit"
          disabled={disabled}
          onClick={() => {
            setDraft(committedNote);
            setOpen(true);
          }}
        />
      </div>
    );
  }

  return <GhostButton label="add note" disabled={disabled} onClick={() => setOpen(true)} />;
}
