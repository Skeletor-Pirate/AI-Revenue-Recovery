import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { Overview } from './pages/Overview'
import { Queue } from './pages/Queue'
import { Recovery } from './pages/Recovery'
import { Exceptions } from './pages/Exceptions'
import { Attention } from './pages/Attention'
import { Playground } from './pages/Playground'

function CaseRedirect() {
  const { id } = useParams()
  return <Navigate to={`/queue?case=${encodeURIComponent(id ?? '')}`} replace />
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/queue" element={<Queue />} />
          <Route path="/recovery" element={<Recovery />} />
          <Route path="/exceptions" element={<Exceptions />} />
          <Route path="/attention" element={<Attention />} />
          <Route path="/playground" element={<Playground />} />
          <Route path="/case/:id" element={<CaseRedirect />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
