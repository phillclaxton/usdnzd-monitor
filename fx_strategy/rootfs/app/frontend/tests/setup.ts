import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// Recharts' ResponsiveContainer observes its element to size the chart. jsdom
// has no ResizeObserver, so any component rendering a chart would throw during
// commit and fail for a reason unrelated to what the test asserts. Zero
// dimensions are fine here: these tests read text, not pixels.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
}

afterEach(() => {
  cleanup();
});
