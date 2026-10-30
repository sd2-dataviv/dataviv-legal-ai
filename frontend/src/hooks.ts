import { useCallback, useEffect, useRef, useState } from "react";
import { errorText } from "./api";

export type Loaded<T> = {
  data: T | null;
  error: string | null;
  status: number | null;
  loading: boolean;
  reload: () => void;
};

/** Run an API call whenever `deps` change; keeps the last good data while reloading. */
export function useLoad<T>(fn: (() => Promise<T>) | null, deps: unknown[]): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    const call = fnRef.current;
    if (!call) {
      setData(null);
      return;
    }
    let live = true;
    setLoading(true);
    call()
      .then((d) => {
        if (!live) return;
        setData(d);
        setError(null);
        setStatus(null);
      })
      .catch((e: unknown) => {
        if (!live) return;
        setError(errorText(e));
        setStatus((e as { status?: number }).status ?? null);
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, status, loading, reload };
}

/** Call `fn` every `ms` while `active` is true. */
export function useInterval(fn: () => void, ms: number, active: boolean): void {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => ref.current(), ms);
    return () => clearInterval(id);
  }, [ms, active]);
}
