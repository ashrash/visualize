import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Link2Off } from "lucide-react";
import { useState } from "react";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent } from "../../components/ui/dialog";
import { Field, Input, Select } from "../../components/ui/input";
import { toast } from "../../components/ui/toast";
import { useProjectStore } from "../../state/projectStore";
import {
  createShare,
  iframeSnippet,
  linkUrl,
  listShares,
  revokeShare,
  type ShareLink,
} from "./api";

const EXPIRY = [
  { value: "", label: "Never" },
  { value: "1", label: "1 day" },
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
];

async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${what} copied`);
  } catch {
    toast(`Couldn't copy ${what.toLowerCase()}: select it and copy by hand`);
  }
}

function LinkRow({ link, onRevoke }: { link: ShareLink; onRevoke: () => void }) {
  const url = linkUrl(link.sharePath);
  const snippet = iframeSnippet(link.embedPath);
  const expires = link.expiresAt
    ? `expires ${new Date(link.expiresAt).toLocaleDateString()}`
    : "no expiry";
  return (
    <li className="flex flex-col gap-2 rounded-md border border-line p-2">
      <div className="flex items-center gap-2">
        <Input
          readOnly
          aria-label="Share URL"
          value={url}
          onFocus={(e) => e.currentTarget.select()}
        />
        <Button onClick={() => void copy(url, "Link")}>
          <Copy size={14} aria-hidden="true" />
          Copy
        </Button>
      </div>
      <div className="flex items-center gap-2">
        <Input
          readOnly
          aria-label="Embed snippet"
          value={snippet}
          className="font-mono text-xs"
          onFocus={(e) => e.currentTarget.select()}
        />
        <Button onClick={() => void copy(snippet, "Embed code")}>
          <Copy size={14} aria-hidden="true" />
          Copy embed
        </Button>
      </div>
      <div className="flex items-center justify-between text-xs text-muted">
        <span>{`Created ${new Date(link.createdAt).toLocaleDateString()}, ${expires}`}</span>
        <Button size="sm" variant="ghost" onClick={onRevoke}>
          <Link2Off size={14} aria-hidden="true" />
          Revoke
        </Button>
      </div>
    </li>
  );
}

/** Share links for one dashboard: mint, copy the URL or `<iframe>` snippet, revoke. */
export function ShareDialog({
  open,
  onOpenChange,
  projectId,
  dashboardId,
  dashboardName,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  dashboardId: string;
  dashboardName: string;
}) {
  const client = useQueryClient();
  const dirty = useProjectStore((s) => s.dirty);
  const [expiry, setExpiry] = useState("");
  const key = ["shares", projectId];
  const links = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => listShares(projectId, signal),
    enabled: open,
  });
  const create = useMutation({
    mutationFn: () =>
      createShare({
        projectId,
        kind: "dashboard",
        itemId: dashboardId,
        expiresInDays: expiry ? Number(expiry) : null,
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: key }),
  });
  const revoke = useMutation({
    mutationFn: (token: string) => revokeShare(token),
    onSuccess: () => client.invalidateQueries({ queryKey: key }),
  });
  const mine = (links.data ?? []).filter(
    (l) => l.kind === "dashboard" && l.itemId === dashboardId && !l.revoked,
  );
  const isLocal = ["localhost", "127.0.0.1"].includes(window.location.hostname);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={`Share "${dashboardName}"`}
        description="Anyone with the link sees a read-only copy."
      >
        {dirty ? (
          <p role="status" className="rounded-md border border-line bg-surface p-2 text-xs">
            You have unsaved changes. A link shows the last saved version, so save first.
          </p>
        ) : null}
        {isLocal ? (
          <p className="text-xs text-muted">
            This app runs on this machine, so these links open only here (or on your network if the
            server was started with --host 0.0.0.0).
          </p>
        ) : null}
        <div className="flex items-end gap-2">
          <Field label="Link expires" className="w-40">
            {(p) => (
              <Select
                {...p}
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
                options={EXPIRY}
              />
            )}
          </Field>
          <Button variant="primary" disabled={create.isPending} onClick={() => create.mutate()}>
            Create link
          </Button>
        </div>
        {create.isError ? (
          <p role="alert" className="text-sm text-danger">
            {create.error.message}
          </p>
        ) : null}
        {links.isError ? (
          <p role="alert" className="text-sm text-danger">
            {links.error.message}
          </p>
        ) : null}
        {mine.length > 0 ? (
          <ul aria-label="Share links" className="flex flex-col gap-2">
            {mine.map((link) => (
              <LinkRow key={link.token} link={link} onRevoke={() => revoke.mutate(link.token)} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">{links.isPending ? "Loading…" : "No links yet."}</p>
        )}
      </DialogContent>
    </Dialog>
  );
}
