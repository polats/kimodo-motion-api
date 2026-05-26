import { createContext } from 'react'
// Handlers shared from KataBuilder down to each MoveNode: { update, generate, branch }.
export const KataCtx = createContext(null)
