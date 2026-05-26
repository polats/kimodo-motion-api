import ReactDOM from 'react-dom/client'
import KataBuilder from './kata/KataBuilder.jsx'

// No StrictMode: it double-invokes effects in dev, which would create/destroy
// the per-node WebGL previews twice and churn GL contexts.
ReactDOM.createRoot(document.getElementById('root')).render(<KataBuilder />)
