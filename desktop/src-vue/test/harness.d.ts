// Harness globals for the headless driver protocol (window.__st/__ran
// asserted via the red smoke badge, __TAURI_INTERNALS__ stubs invoke).
// Declared here so `vue-tsc` checkJs passes on test/main.js.
export {};
declare global {
  interface Window {
    __TAURI_INTERNALS__: any;
    __st: any;
    __ran: string;
  }
}
