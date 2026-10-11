import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { ActionVerification } from './pages/ActionVerification'
import { AiAnalysis } from './pages/AiAnalysis'
import { AnomalyCenter } from './pages/AnomalyCenter'
import { FactoryOverview } from './pages/FactoryOverview'
import { FmeaRisk } from './pages/FmeaRisk'
import { LineMonitor } from './pages/LineMonitor'

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/overview" replace />} />
        <Route path="overview" element={<FactoryOverview />} />
        <Route path="line" element={<LineMonitor />} />
        <Route path="anomalies" element={<AnomalyCenter />} />
        <Route path="analysis" element={<AiAnalysis />} />
        <Route path="fmea" element={<FmeaRisk />} />
        <Route path="actions" element={<ActionVerification />} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  )
}