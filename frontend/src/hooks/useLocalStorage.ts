import { useCallback, useState } from 'react';

/**
 * Small localStorage-backed state helper. Used to persist the demo's
 * identity (user_id, dashboard token, etc.) across page navigations without
 * a backend session.
 */
export function useLocalStorage(key: string, initial = ''): [string, (v: string) => void] {
  const [value, setValue] = useState<string>(() => {
    try {
      return window.localStorage.getItem(key) ?? initial;
    } catch {
      return initial;
    }
  });

  const set = useCallback(
    (v: string) => {
      setValue(v);
      try {
        if (v === '') {
          window.localStorage.removeItem(key);
        } else {
          window.localStorage.setItem(key, v);
        }
      } catch {
        // ignore storage failures (private browsing, quota, etc.)
      }
    },
    [key],
  );

  return [value, set];
}
