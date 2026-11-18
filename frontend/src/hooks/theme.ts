import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.classList.contains("dark") ? "dark" : "light"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    try { localStorage.setItem("above-theme", theme); } catch { /* storage unavailable */ }
  }, [theme]);
  return [theme, useCallback(() => setTheme((t) => (t === "dark" ? "light" : "dark")), [])];
}
