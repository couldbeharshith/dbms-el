export {};

declare global {
  interface Window {
    desktop?: {
      getSettings: () => Promise<Record<string, unknown>>;
      setSettings: (patch: Record<string, unknown>) => Promise<Record<string, unknown>>;
      captureNow: () => Promise<boolean>;
    };
  }
}
