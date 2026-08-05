import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api';
import type {
  DocumentPreview,
  FeeModel,
  Scenarios,
  Strategy,
  StrategyDocument,
  StrategyInput,
  StrategySummary,
  StrategyTemplate,
  Tranche,
  ValidationReport,
} from '@/types';

export function useSummary() {
  return useQuery({
    queryKey: ['strategy', 'summary'],
    queryFn: () => api.get<StrategySummary>('summary'),
    // A 404 here is the normal "no strategy yet" state, not a transient error.
    retry: false,
    refetchInterval: 60_000,
  });
}

export function useStrategies() {
  return useQuery({
    queryKey: ['strategy', 'list'],
    queryFn: () => api.get<Strategy[]>('strategies'),
  });
}

export function useStrategy(id: number | null) {
  return useQuery({
    queryKey: ['strategy', 'detail', id],
    queryFn: () => api.get<Strategy>(`strategies/${id}`),
    enabled: id !== null,
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: ['strategy', 'templates'],
    queryFn: () => api.get<StrategyTemplate[]>('strategy-templates'),
    staleTime: 5 * 60_000,
  });
}

export function useFeeModels() {
  return useQuery({
    queryKey: ['strategy', 'fee-models'],
    queryFn: () => api.get<FeeModel[]>('fee-models'),
  });
}

export function useScenarios(id: number | null, customRate?: string) {
  const suffix = customRate ? `&custom_rate=${encodeURIComponent(customRate)}` : '';
  return useQuery({
    queryKey: ['strategy', 'scenarios', id, customRate ?? ''],
    queryFn: () => api.get<Scenarios>(`strategies/${id}/scenarios?periods=4${suffix}`),
    enabled: id !== null,
  });
}

export function useValidation(id: number | null) {
  return useQuery({
    queryKey: ['strategy', 'validate', id],
    queryFn: () => api.get<ValidationReport>(`strategies/${id}/validate`),
    enabled: id !== null,
  });
}

function useStrategyInvalidation() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ['strategy'] });
    void queryClient.invalidateQueries({ queryKey: ['audit'] });
  };
}

export function useCreateStrategy() {
  const invalidate = useStrategyInvalidation();
  return useMutation({
    mutationFn: (payload: StrategyInput) => api.post<Strategy>('strategies', payload),
    onSuccess: invalidate,
  });
}

export function useUpdateStrategy(id: number) {
  const invalidate = useStrategyInvalidation();
  return useMutation({
    mutationFn: (payload: StrategyInput) => api.put<Strategy>(`strategies/${id}`, payload),
    onSuccess: invalidate,
  });
}

export function useStrategyAction(id: number) {
  const invalidate = useStrategyInvalidation();
  return useMutation({
    mutationFn: (action: 'activate' | 'pause' | 'resume' | 'complete' | 'duplicate') =>
      api.post<Strategy>(`strategies/${id}/${action}`),
    onSuccess: invalidate,
  });
}

/** The strategy as JSON, for the document editor. */
export function useStrategyDocument(id: number | null) {
  return useQuery({
    queryKey: ['strategy', 'document', id],
    queryFn: () => api.get<StrategyDocument>(`strategies/${id}/document`),
    enabled: id !== null,
  });
}

/**
 * A dry run of a pasted document.
 *
 * Modelled as a query rather than a mutation because the text is the key: it
 * writes nothing, repeated checks of the same text are answered from cache, and
 * an answer for text the user has since edited can never overwrite a newer one.
 */
export function useDocumentPreview(id: number | null, text: string) {
  const path = id === null ? 'strategies/document/preview' : `strategies/${id}/document/preview`;
  return useQuery({
    queryKey: ['strategy', 'document-preview', id, text],
    queryFn: () => api.post<DocumentPreview>(path, { text }),
    enabled: text.trim().length > 0,
    retry: false,
    staleTime: Infinity,
  });
}

/** Apply a pasted document, creating the strategy if there is not one yet. */
export function useSaveDocument(id: number | null) {
  const invalidate = useStrategyInvalidation();
  return useMutation({
    mutationFn: (text: string) =>
      id === null
        ? api.post<Strategy>('strategies/document', { text })
        : api.put<Strategy>(`strategies/${id}/document`, { text }),
    onSuccess: invalidate,
  });
}

export function useTrancheAction() {
  const invalidate = useStrategyInvalidation();
  return useMutation({
    mutationFn: ({ id, action }: { id: number; action: 'acknowledge' | 'skip' }) =>
      api.post<Tranche>(`tranches/${id}/${action}`),
    onSuccess: invalidate,
  });
}
