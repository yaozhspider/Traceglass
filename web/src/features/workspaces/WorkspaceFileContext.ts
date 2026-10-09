import { createContext } from 'react';
export const WorkspaceFileContext = createContext<
  ((path: string) => void) | null
>(null);
