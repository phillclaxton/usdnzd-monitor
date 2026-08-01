import { Suspense, lazy } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import Layout from './components/Layout';
import { Loading } from './components/ui';
import Dashboard from './pages/Dashboard';
import SettingsPage from './pages/SettingsPage';

// The charting library is by far the largest dependency. Splitting it out keeps
// the dashboard fast to open on a phone, which is where it is mostly read.
const ChartPage = lazy(() => import('./pages/ChartPage'));

export default function App() {
  return (
    <Layout>
      <Suspense fallback={<Loading label="Loading…" />}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/chart" element={<ChartPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </Layout>
  );
}
