import type { FallbackProps } from "react-error-boundary";

import { log } from "@/lib/log";

// Top-level crash boundary fallback — keeps a render error from blanking the app and
// records it through the observability seam.
export function RootErrorFallback({ error }: FallbackProps) {
    const message = error instanceof Error ? error.message : String(error);
    log.error("ui.crash", { message });
    return (
        <main role="alert">
            <p>Something went wrong.</p>
            <pre>{message}</pre>
        </main>
    );
}
