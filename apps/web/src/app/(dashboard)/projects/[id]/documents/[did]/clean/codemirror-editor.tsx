"use client";

import React, { useEffect, useRef, useState } from "react";
import { EditorView, basicSetup } from "codemirror";
import { EditorState } from "@codemirror/state";
import { markdown } from "@codemirror/lang-markdown";
import { oneDark } from "@codemirror/theme-one-dark";

interface CodeMirrorEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
}

export function CodeMirrorEditor({
  value,
  onChange,
  readOnly = false,
}: CodeMirrorEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const [darkMode, setDarkMode] = useState(false);

  useEffect(() => {
    if (!containerRef.current) return;

    const state = EditorState.create({
      doc: value,
      extensions: [
        basicSetup,
        markdown(),
        EditorView.lineWrapping,
        ...(darkMode ? [oneDark] : []),
        ...(readOnly ? [EditorState.readOnly.of(true)] : []),
        EditorView.updateListener.of((update) => {
          if (update.docChanged && onChangeRef.current) {
            onChangeRef.current(update.state.doc.toString());
          }
        }),
      ],
    });

    const view = new EditorView({
      state,
      parent: containerRef.current,
    });

    viewRef.current = view;

    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // Re-create on readOnly or darkMode change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readOnly, darkMode]);

  // Sync external value changes
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const currentValue = view.state.doc.toString();
    if (currentValue !== value) {
      view.dispatch({
        changes: {
          from: 0,
          to: currentValue.length,
          insert: value,
        },
      });
    }
  }, [value]);

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-end px-2 py-0.5 border-b bg-muted/30">
        <button
          type="button"
          className="text-[10px] text-muted-foreground hover:text-foreground px-1.5 py-0.5 rounded border"
          onClick={() => setDarkMode((d) => !d)}
        >
          {darkMode ? "☀ 亮色" : "🌙 暗色"}
        </button>
      </div>
      <div ref={containerRef} className="flex-1 min-h-0 overflow-auto text-sm" />
    </div>
  );
}
