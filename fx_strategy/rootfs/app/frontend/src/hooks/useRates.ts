import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/lib/api';
import type { CurrentRate, ProviderStatus, RateHistory, RefreshResult } from '@/types';

export const RATE_RANGES = ['24h', '7d', '30d', '3m', '6m', '1y'] as const;
export type RateRange = (typeof RATE_RANGES)[number];

export function useCurrentRate() {
  return useQuery({
    queryKey: ['rate', 'current'],
    queryFn: () => api.get<CurrentRate>('rates/current'),
    // The backend polls on its own schedule; this keeps the open tab in step
    // without adding load to the upstream provider.
    refetchInterval: 30_000,
  });
}

export function useRateHistory(range: RateRange) {
  return useQuery({
    queryKey: ['rate', 'history', range],
    queryFn: () => api.get<RateHistory>(`rates/history?range=${range}`),
    staleTime: 60_000,
  });
}

export function useProviderStatus() {
  return useQuery({
    queryKey: ['rate', 'providers'],
    queryFn: () => api.get<ProviderStatus[]>('rates/providers'),
    staleTime: 60_000,
  });
}

function useRateInvalidation() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ['rate'] });
    void queryClient.invalidateQueries({ queryKey: ['strategy'] });
  };
}

export function useRefreshRate() {
  const invalidate = useRateInvalidation();
  return useMutation({
    mutationFn: () => api.post<RefreshResult>('rates/refresh'),
    onSuccess: invalidate,
  });
}

export function useSetManualRate() {
  const invalidate = useRateInvalidation();
  return useMutation({
    mutationFn: (payload: { rate: string; note?: string }) =>
      api.post<RefreshResult>('rates/manual', payload),
    onSuccess: invalidate,
  });
}
