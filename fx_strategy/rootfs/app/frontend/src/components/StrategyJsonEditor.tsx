/**
 * Editing a strategy as JSON.
 *
 * The document is exactly the shape the API accepts, so a strategy copied from
 * one installation can be pasted into another with no conversion step. The text
 * is checked against the server as it is edited, and the consequences of a
 * paste — dropped tranches, moved targets, reset alert state — are shown before
 * the save button is ever pressed.
 */
import { useEffect, useRef, useState } from 'react';

import { Banner, Card, Loading } from '@/components/ui';
import { useDocumentPreview, useSaveDocument, useStrategyDocument } from '@/hooks/useStrategy';
import { ApiError } from '@/lib/api';
import type { DocumentProblem } from '@/types';

/** Delay before edited text is sent for checking, in milliseconds. */
const CHECK_DELAY = 400;

function useDebounced(value: string, delay = CHECK_DELAY): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/** A rejected save carries the same located problems the preview returns. */
function problemsFrom(error: unknown): DocumentProblem[] {
  if (error instanceof ApiError && Array.isArray(error.details)) {
    return error.details as DocumentProblem[];
  }
  return [];
}

function describe(problem: DocumentProblem): string {
  if (problem.line !== null) {
    return `Line ${problem.line}, column ${problem.column ?? 1}: ${problem.message}`;
  }
  return problem.path ? `${problem.path} — ${problem.message}` : problem.message;
}

export default function StrategyJsonEditor({
  strategyId,
  fallbackText = '',
  fieldEditorHasUnsavedChanges = false,
}: {
  strategyId: number | null;
  /** Used when there is no saved strategy yet, so a new plan starts from the draft. */
  fallbackText?: string;
  fieldEditorHasUnsavedChanges?: boolean;
}) {
  const stored = useStrategyDocument(strategyId);
  const save = useSaveDocument(strategyId);
  const box = useRef<HTMLTextAreaElement | null>(null);

  const [text, setText] = useState<string | null>(null);
  const [copyNote, setCopyNote] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const source = strategyId === null ? fallbackText : (stored.data?.text ?? null);

  useEffect(() => {
    // Load once. A refetch must not discard what the user has typed.
    if (text === null && source !== null) setText(source);
  }, [source, text]);

  const settled = useDebounced(text ?? '');
  const report = useDocumentPreview(strategyId, settled);

  if (text === null) return <Loading label="Loading the strategy document…" />;

  const problems = report.data?.valid === false ? report.data.problems : problemsFrom(save.error);
  const changes = report.data?.valid ? report.data.changes : [];
  const warnings = report.data?.valid ? report.data.warnings : [];
  const blocked = report.data?.valid === false;

  const edit = (next: string) => {
    setText(next);
    setSaved(false);
    setCopyNote(null);
    save.reset();
  };

  const tidy = () => {
    try {
      edit(JSON.stringify(JSON.parse(text) as unknown, null, 2));
    } catch {
      // Nothing to do: the problem list already says where the syntax breaks.
    }
  };

  const copy = async () => {
    // Ingress is often served over plain http on a LAN, where the clipboard API
    // is unavailable. Selecting the text is the honest fallback.
    try {
      await navigator.clipboard.writeText(text);
      setCopyNote('Copied to the clipboard.');
    } catch {
      box.current?.select();
      setCopyNote('Selected — press Ctrl+C (⌘+C) to copy.');
    }
  };

  const discard = () => {
    if (source !== null) edit(source);
  };

  const submit = () => {
    setSaved(false);
    save.mutate(text, { onSuccess: () => setSaved(true) });
  };

  return (
    <Card
      title="Edit as JSON"
      subtitle="Paste a whole strategy at once. The document is the same shape the API accepts."
    >
      <Banner tone="info">
        This document is the <strong>plan</strong>: amounts, ladder and deadlines. Recorded
        conversions, alert history and the audit trail are facts rather than settings, and saving
        a document never alters them.
      </Banner>

      {fieldEditorHasUnsavedChanges && (
        <Banner tone="warning">
          The field editor has unsaved changes, and they are not in this document. Save there
          first if you want to keep them.
        </Banner>
      )}

      <label htmlFor="strategy-json">Strategy JSON</label>
      <textarea
        id="strategy-json"
        ref={box}
        className="fx-code-editor"
        rows={22}
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
        value={text}
        onChange={(event) => edit(event.target.value)}
      />

      <div className="fx-toolbar">
        <button
          type="button"
          className="is-primary"
          onClick={submit}
          disabled={save.isPending || blocked}
        >
          {save.isPending ? 'Saving…' : strategyId === null ? 'Create from JSON' : 'Save JSON'}
        </button>
        <button type="button" onClick={() => void copy()}>
          Copy
        </button>
        <button type="button" onClick={tidy}>
          Tidy formatting
        </button>
        <button type="button" onClick={discard} disabled={source === null || text === source}>
          Discard changes
        </button>
      </div>

      {copyNote && <p className="fx-stat-note">{copyNote}</p>}
      {saved && <Banner tone="info">Strategy saved from the document.</Banner>}
      {save.isError && problemsFrom(save.error).length === 0 && (
        <Banner tone="error">{(save.error as Error).message}</Banner>
      )}
      {report.isError && (
        <Banner tone="warning">
          The document could not be checked: {(report.error as Error).message}
        </Banner>
      )}

      {problems.length > 0 && (
        <div role="alert">
          <h3>
            {problems.length} problem{problems.length === 1 ? '' : 's'}
          </h3>
          <ul className="fx-issue-list">
            {problems.map((problem, index) => (
              <li key={`${problem.path}-${index}`}>{describe(problem)}</li>
            ))}
          </ul>
        </div>
      )}

      {report.data?.valid && (
        <div role="status">
          {warnings.map((warning) => (
            <Banner key={warning} tone="warning">
              {warning}
            </Banner>
          ))}
          <h3>
            {changes.length === 0
              ? 'No changes'
              : `${changes.length} change${changes.length === 1 ? '' : 's'}`}
          </h3>
          {changes.length === 0 ? (
            <p className="fx-stat-note">
              This document matches the saved strategy. Saving it would change nothing.
            </p>
          ) : (
            <div className="fx-table-wrap">
              <table className="fx-table">
                <thead>
                  <tr>
                    <th className="fx-left">Field</th>
                    <th className="fx-left">Now</th>
                    <th className="fx-left">After saving</th>
                  </tr>
                </thead>
                <tbody>
                  {changes.map((change) => (
                    <tr key={change.path}>
                      <td className="fx-left">
                        <code>{change.path}</code>
                      </td>
                      <td className="fx-left">{change.before}</td>
                      <td className="fx-left">{change.after}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {stored.data && Object.keys(stored.data.omitted).length > 0 && (
        <details>
          <summary>What the document leaves out</summary>
          <ul className="fx-issue-list">
            {Object.entries(stored.data.omitted).map(([field, reason]) => (
              <li key={field}>
                <code>{field}</code> — {reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}
