import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { toast } from "sonner";
import { errorMessage } from "@/lib/api";

/** Mutation with toast feedback and cache invalidation. */
export function useAction<TVars, TResult>(fn: (vars: TVars) => Promise<TResult>, opts: {
  success?: string | ((r: TResult) => string); invalidate?: QueryKey[]; onSuccess?: (r: TResult) => void; onError?: (e: unknown) => void;
} = {}) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      if (opts.success) toast.success(typeof opts.success === "function" ? opts.success(r) : opts.success);
      for (const k of opts.invalidate ?? []) qc.invalidateQueries({ queryKey: k });
      opts.onSuccess?.(r);
    },
    onError: (e) => {
      if (opts.onError) opts.onError(e);
      else toast.error(errorMessage(e));
    },
  });
}
