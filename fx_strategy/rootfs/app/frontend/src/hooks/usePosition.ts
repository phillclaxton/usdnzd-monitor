import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api';
import type {
  ConversionHistory,
  FxAlert,
  FxState,
  Position,
  PositionInput,
  RecordConversionInput,
} from '@/types';

/**
 * Anything that changes the position changes the history's improvement figures
 * too, and the other way round, because both are measured against the same
 * baseline. Invalidating them together is simpler than reasoning about which
 * one a given mutation touched.
 */
function invalidateAll(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: ['fx'] });
}

export function useFxState() {
  return useQuery({
    queryKey: ['fx', 'state'],
    queryFn: () => api.get<FxState>('fx/state'),
    // The position's own figures only change when they are edited, but the rate
    // they are valued at moves on the backend's polling schedule. Matching
    // useCurrentRate keeps the two from disagreeing on screen.
    refetchInterval: 30_000,
  });
}

export function useConversionHistory() {
  return useQuery({
    queryKey: ['fx', 'conversions'],
    queryFn: () => api.get<ConversionHistory>('fx/conversions'),
  });
}

export function useFxAlerts(limit = 20) {
  return useQuery({
    queryKey: ['fx', 'alerts', limit],
    queryFn: () => api.get<FxAlert[]>(`fx/alerts?limit=${limit}`),
    refetchInterval: 60_000,
  });
}

/** Replace every field. Use `usePatchState` to change one of them. */
export function useSaveState() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: PositionInput) => api.post<Position>('fx/state', body),
    onSuccess: () => invalidateAll(client),
  });
}

/**
 * A partial edit.
 *
 * A key present with a null value clears that field; a key left out is not
 * touched. That distinction is the whole reason this is a PATCH, so callers
 * must send only the keys they mean to change.
 */
export function usePatchState() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: PositionInput) => api.patch<Position>('fx/state', body),
    onSuccess: () => invalidateAll(client),
  });
}

/**
 * Record a conversion that has just happened.
 *
 * **This reduces the held balance.** `POST /conversions` does not, and that
 * difference is the only reason both exist.
 */
export function useRecordConversion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: RecordConversionInput) =>
      api.post<ConversionHistory>('fx/conversions', body),
    onSuccess: () => invalidateAll(client),
  });
}

/**
 * Correct a historical record.
 *
 * Goes through `/conversions/{id}`, which is deliberately free of side effects:
 * it never credits the balance back. The form says so on screen, because the
 * moment someone edits an amount is the moment they would otherwise expect it
 * to.
 */
export function useCorrectConversion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) =>
      api.put<unknown>(`conversions/${id}`, body),
    onSuccess: () => invalidateAll(client),
  });
}

export function useDeleteConversion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.del<unknown>(`conversions/${id}`),
    onSuccess: () => invalidateAll(client),
  });
}
