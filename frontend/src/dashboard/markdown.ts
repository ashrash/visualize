import DOMPurify from "dompurify";
import { marked } from "marked";

/** Tags a text widget may contain. No scripts, styles, forms, iframes or embeds. */
const ALLOWED_TAGS = [
  "p",
  "br",
  "hr",
  "strong",
  "em",
  "del",
  "code",
  "pre",
  "blockquote",
  "ul",
  "ol",
  "li",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "a",
  "img",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "span",
];
const ALLOWED_ATTR = ["href", "src", "alt", "title", "rel", "target", "align"];
// Only web links and images: no javascript:, data: or vbscript: URLs.
const ALLOWED_URI = /^(?:https?:|mailto:|\/(?!\/)|#)/i;

let hooked = false;
function ensureHooks() {
  if (hooked) return;
  hooked = true;
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A") {
      node.setAttribute("rel", "noopener noreferrer");
      node.setAttribute("target", "_blank");
    }
  });
}

/** Markdown to sanitized HTML, safe to inject into the page. */
export function renderMarkdown(markdown: string): string {
  ensureHooks();
  const html = marked.parse(markdown, { async: false, gfm: true, breaks: false });
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOWED_URI_REGEXP: ALLOWED_URI,
    ALLOW_DATA_ATTR: false,
  });
}
