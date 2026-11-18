import { useQuery } from "@tanstack/react-query";
import { FileText, ImageIcon, Paperclip, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { Button, Card, CardHeader, Skeleton } from "./ui/primitives";

interface AttachmentMeta { id: string; filename: string; content_type: string; size_bytes: number; created_at: string }

/** Documents attached to a business record (delivery notes, invoices, photos). Validated server-side. */
export function AttachmentsPanel({ entityType, entityId, canUpload, title = "Documents", description }: {
  entityType: string; entityId: string; canUpload: boolean; title?: string; description?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const list = useQuery({ queryKey: ["attachments", entityType, entityId], queryFn: () => api.get<AttachmentMeta[]>("/attachments", { entity_type: entityType, entity_id: entityId }) });
  const upload = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    form.append("entity_type", entityType);
    form.append("entity_id", entityId);
    setBusy(true);
    try {
      await api.upload("/attachments", form);
      toast.success(`${file.name} attached`);
      list.refetch();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
      if (ref.current) ref.current.value = "";
    }
  };
  return (
    <Card>
      <CardHeader title={title} description={description ?? "PDF, PNG, JPEG, WebP or CSV up to 10 MB."}
        actions={canUpload && <>
          <input ref={ref} type="file" className="hidden" accept=".pdf,.png,.jpg,.jpeg,.webp,.csv" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} aria-label={`Upload to ${title}`} />
          <Button size="sm" loading={busy} onClick={() => ref.current?.click()}><Upload /> Attach</Button>
        </>} />
      {list.isLoading ? <Skeleton className="m-4 h-10" /> : !list.data?.length ? (
        <p className="flex items-center gap-2 px-5 py-4 text-sm text-muted"><Paperclip className="size-4" /> No documents attached.</p>
      ) : (
        <ul className="divide-y divide-line">
          {list.data.map((a) => (
            <li key={a.id} className="flex items-center gap-3 px-5 py-2.5 text-sm">
              {a.content_type.startsWith("image/") ? <ImageIcon className="size-4 text-muted" /> : <FileText className="size-4 text-muted" />}
              <a href={api.downloadUrl(`/attachments/${a.id}`)} target="_blank" rel="noreferrer" className="min-w-0 flex-1 truncate hover:text-accent">{a.filename}</a>
              <span className="text-xs text-muted">{Math.max(1, Math.round(a.size_bytes / 1024))} KB · {dateTime(a.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** Product image: upload via attachments, then link it on the product. */
export function ProductImage({ productId, attachmentId, canEdit, onChanged }: { productId: string; attachmentId: string | null; canEdit: boolean; onChanged: () => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const upload = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    form.append("entity_type", "product");
    form.append("entity_id", productId);
    setBusy(true);
    try {
      const att = await api.upload<{ id: string }>("/attachments", form);
      await api.patch(`/products/${productId}`, { image_attachment_id: att.id });
      toast.success("Product image updated");
      onChanged();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col items-center gap-2">
      <div className="grid size-28 place-items-center overflow-hidden rounded-xl border border-line bg-surface-2">
        {attachmentId ? <img src={api.downloadUrl(`/attachments/${attachmentId}`)} alt="Product" className="size-full object-cover" />
          : <ImageIcon className="size-7 text-muted" aria-label="No product image" />}
      </div>
      {canEdit && <>
        <input ref={ref} type="file" className="hidden" accept="image/png,image/jpeg,image/webp" aria-label="Upload product image"
          onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        <Button size="sm" variant="ghost" loading={busy} onClick={() => ref.current?.click()}>{attachmentId ? "Replace image" : "Add image"}</Button>
      </>}
    </div>
  );
}
