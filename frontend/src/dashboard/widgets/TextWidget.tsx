import { useMemo } from "react";
import { renderMarkdown } from "../markdown";

export function TextWidget({ markdown }: { markdown: string }) {
  // Sanitized by DOMPurify inside renderMarkdown; this is the only place HTML is injected.
  const html = useMemo(() => renderMarkdown(markdown), [markdown]);
  return (
    <div
      className="dashboard-text h-full overflow-auto p-3 text-sm text-fg"
      // biome-ignore lint/security/noDangerouslySetInnerHtml: sanitized with DOMPurify above
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
